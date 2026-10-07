"""Run orchestration (roadmap §3.1 Run Orchestrator / §10.2 stability).

One entry point, ``execute_suite``: schedule the generated cases against an
AgentAdapter with bounded concurrency and per-case timeout, isolate failures
(one broken case must never abort the run), retry only transient network/5xx
errors, honor project-level concurrency limits and mid-run cancellation
(pending cases become CANCELED, finished results are preserved), apply
trace/response limits, optionally repeat cases to detect FLAKY behavior, then
persist everything and diff against the baseline.

The orchestrator is adapter-agnostic: it never imports a framework. Adapters
raise; this module converts failures into per-case ERROR results.
"""
import asyncio
import logging
import os
import time
from collections import defaultdict

from . import storage
from .adapters import resolve_adapter
from .adapters.base import AgentAdapter, ExecutionContext, TransientAgentError
from .judge import judge
from .llm_judge import judge_with_llm_async
from .models import AgentExecution, BehaviorSpec, DiffSummary, TestCase, TestResult
from .regression import diff_runs
from .trace import apply_limits

logger = logging.getLogger("specagent.orchestrator")

# Project-level concurrency (§10.2): concurrent runs on the same project share
# one budget so parallel runs cannot collectively hammer the target agent.
_PROJECT_SEMAPHORES: dict[str, asyncio.Semaphore] = {}
_PROJECT_LIMIT = defaultdict(lambda: int(os.getenv("SPECAGENT_PROJECT_CONCURRENCY", "8")))


def _project_semaphore(project_id: str) -> asyncio.Semaphore:
    sem = _PROJECT_SEMAPHORES.get(project_id)
    if sem is None:
        sem = asyncio.Semaphore(_PROJECT_LIMIT[project_id])
        _PROJECT_SEMAPHORES[project_id] = sem
    return sem


async def _execute_once(adapter: AgentAdapter, case: TestCase, context: ExecutionContext,
                        retries: int = 1) -> AgentExecution:
    """One judge-ready execution attempt.

    Wall-clock timing (v1 design §3.2): only the awaited `adapter.execute` is
    measured (retries do not accumulate; the last attempt wins). An
    adapter-reported latency > 0 is kept; a sub-millisecond run stays 0 — we
    never fabricate. The value is set before `judge()` copies it into the
    TestResult, and error results carry their elapsed time too.
    """
    attempt = 0
    while True:
        t0 = time.monotonic()
        try:
            execution = await adapter.execute(case, context)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            if execution.latency_ms <= 0 and elapsed_ms > 0:
                execution.latency_ms = elapsed_ms
            return execution
        except TransientAgentError as exc:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            if attempt >= retries:
                error = AgentExecution(error=f"{type(exc).__name__}: {exc}")
                error.latency_ms = elapsed_ms
                return error
            attempt += 1
            logger.warning("transient failure on %s (retry %s/%s): %s", case.id, attempt, retries, exc)
            await asyncio.sleep(min(0.5, 0.1 * attempt))
        except asyncio.TimeoutError:
            error = AgentExecution(error=f"timeout after {context.timeout_seconds}s")
            error.latency_ms = int((time.monotonic() - t0) * 1000)
            return error
        except Exception as exc:  # noqa: BLE001 — one agent failure must not abort the run
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            logger.warning("agent execution failed for case %s: %s", case.id, exc)
            error = AgentExecution(error=f"{type(exc).__name__}: {exc}")
            error.latency_ms = elapsed_ms
            return error


def _canceled_result(case: TestCase) -> TestResult:
    return TestResult(
        test=case, passed=False, status="CANCELED", violations=[],
        execution=AgentExecution(response="canceled before execution"),
    )


def _downgrade_unverified_passes(results: list[TestResult]) -> list[TestResult]:
    """A run whose traces contain zero tool_call events has verified nothing:
    every PASS in it is vacuous (constraints never matched a call). A
    per-case empty-trace check cannot make that call — a legitimate refusal
    also looks empty (§5.2 rule 3, and the fincare example refuses with an
    empty trace) — so it is made at run level, where a text-only agent is
    unambiguous. Those PASS results become ERROR ("unverified"), which the
    score, the diff (NEW_ERROR) and the verify counts all surface."""
    if not any(r.status == "PASS" for r in results):
        return results
    if any(e.type == "tool_call" for r in results for e in r.execution.trace):
        return results
    for result in results:
        if result.status == "PASS":
            result.status = "ERROR"
            result.passed = False
            result.execution.error = ("unverified: no tool_call events in any "
                                      "trace of this run; nothing was verified")
    return results


async def execute_suite(
    spec: BehaviorSpec,
    tests: list[TestCase],
    *,
    adapter: AgentAdapter,
    concurrency: int = 4,
    timeout_seconds: int = 30,
    repeat: int = 1,
    retries: int = 1,
    max_trace_events: int = 200,
    max_response_chars: int = 20000,
    project_id: str | None = None,
    should_cancel=None,
) -> list[TestResult]:
    """Run all cases against the adapter; returns results."""
    semaphore = asyncio.Semaphore(max(1, concurrency))
    project_sem = _project_semaphore(project_id) if project_id else None
    repeat = max(1, repeat)

    async def run_case(case: TestCase) -> TestResult:
        async with semaphore:
            context = ExecutionContext(
                test_case_id=case.id,
                timeout_seconds=timeout_seconds,
                metadata={"rule_id": case.rule_id, "category": case.category},
            )
            statuses: list[str] = []
            final: TestResult | None = None
            try:
                for _ in range(repeat):
                    if should_cancel and should_cancel():
                        if final is None:
                            final = _canceled_result(case)
                        statuses.append("CANCELED")
                        break
                    execution = await _execute_once(adapter, case, context, retries)
                    apply_limits(execution, max_trace_events, max_response_chars)
                    result = judge(case, execution)
                    statuses.append(result.status)
                    if final is None or (result.status == "FAIL" and final.status != "FAIL"):
                        final = result
                # LLM judge (§8.3 layer 3) runs once per case, advisory only.
                if case.llm_checks and final is not None and final.status != "CANCELED":
                    final.llm_verdict = await judge_with_llm_async(case, final.execution)
            except Exception as exc:  # noqa: BLE001 — defensive: judge/trace bugs isolate here too
                logger.exception("case %s crashed", case.id)
                final = TestResult(
                    test=case, passed=False, status="ERROR",
                    violations=[], execution=AgentExecution(error=f"orchestrator: {exc}"),
                )
            assert final is not None
            if "CANCELED" not in statuses and len(set(statuses)) > 1:
                final.status = "FLAKY"
                final.passed = False
                final.violations = final.violations or [f"unstable behavior across {repeat} repeats: {statuses}"]
            elif "CANCELED" in statuses:
                final.status = "CANCELED"
                final.passed = False
            return final

    if project_sem is None:
        results = list(await asyncio.gather(*(run_case(c) for c in tests)))
    else:
        async with project_sem:
            results = list(await asyncio.gather(*(run_case(c) for c in tests)))
    return _downgrade_unverified_passes(results)


def summarize(results: list[TestResult]) -> dict:
    passed = sum(1 for r in results if r.status == "PASS")
    failed = sum(1 for r in results if r.status in ("FAIL", "FLAKY"))
    errors = sum(1 for r in results if r.status == "ERROR")
    canceled = sum(1 for r in results if r.status == "CANCELED")
    total = len(results)
    score = round(passed / total * 100, 1) if total else 0.0
    return {"passed": passed, "failed": failed, "errors": errors,
            "canceled": canceled, "total": total, "score": score}


def result_to_storage(result: TestResult, spec: BehaviorSpec | None = None) -> dict:
    """Shape a TestResult for the store, including normalized violation rows
    (§9.1 violations table) with rule severity resolved from the spec."""
    severity = {r.id: r.severity for r in spec.rules} if spec else {}
    tool_calls = [
        {"id": e.id, "name": e.name, "args": e.args}
        for e in result.execution.trace if e.type == "tool_call"
    ]
    violation_rows = [{
        "rule_id": result.test.rule_id,
        "severity": severity.get(result.test.rule_id, "medium"),
        "reason": v,
        "evidence": {"tool_calls": tool_calls, "status": result.status},
    } for v in result.violations]
    return {
        "test": result.test.model_dump(),
        "status": result.status,
        "violations": result.violations,
        "execution": result.execution.model_dump(),
        "llm_verdict": result.llm_verdict.model_dump() if result.llm_verdict else None,
        "review": None,
        "violation_rows": violation_rows,
        "repeat": [],
    }


def persist_run(
    store: storage.Store,
    *,
    project_id: str,
    spec: BehaviorSpec,
    tests: list[TestCase],
    results: list[TestResult],
    agent_label: str,
    label: str = "",
    commit_sha: str | None = None,
    set_baseline: bool = False,
    spec_source: str | None = None,
) -> str:
    store.ensure_project(project_id, adapter_type=agent_label.split(":")[0])
    spec_id = store.save_spec(project_id, spec_source or spec.model_dump_json(), spec.model_dump())
    run_id = store.create_run(
        project_id=project_id,
        spec=spec.model_dump(),
        tests=[t.model_dump() for t in tests],
        label=label,
        spec_compiler=spec.compiler,
        agent=agent_label,
        commit_sha=commit_sha or os.getenv("GITHUB_SHA"),
        spec_id=spec_id,
    )
    for r in results:
        store.add_execution(run_id, result_to_storage(r, spec))
    stats = summarize(results)
    store.complete_run(run_id, **stats)
    if set_baseline:
        store.set_baseline(run_id)
    logger.info("run %s persisted: %s/%s passed, score %s", run_id, stats["passed"], stats["total"], stats["score"])
    return run_id


async def run_with_diff(
    store: storage.Store,
    *,
    project_id: str,
    spec: BehaviorSpec,
    tests: list[TestCase],
    label: str = "",
    agent: str = "auto",
    agent_variant: str | None = None,
    adapter: AgentAdapter | None = None,
    concurrency: int = 4,
    timeout_seconds: int = 30,
    repeat: int = 1,
    retries: int = 1,
    max_trace_events: int = 200,
    max_response_chars: int = 20000,
    set_baseline: bool = False,
    baseline_run_id: str | None = None,
    commit_sha: str | None = None,
    spec_source: str | None = None,
    cancel_registry=None,
) -> tuple[str, list[TestResult], DiffSummary | None]:
    """Full pipeline: register the run → execute → persist → diff.

    The run record is created with status=running *before* any case executes,
    so it is observable (and cancelable by id) while in flight.
    """
    if adapter is None:
        adapter = resolve_adapter(agent=agent, agent_variant=agent_variant)
    store.ensure_project(project_id, adapter_type=adapter.name.split(":")[0])
    spec_id = store.save_spec(project_id, spec_source or spec.model_dump_json(), spec.model_dump())
    run_id = store.create_run(
        project_id=project_id, spec=spec.model_dump(),
        tests=[t.model_dump() for t in tests], label=label,
        spec_compiler=spec.compiler, agent=adapter.name,
        commit_sha=commit_sha or os.getenv("GITHUB_SHA"), spec_id=spec_id,
    )
    should_cancel = None
    if cancel_registry is not None:
        cancel_registry.register(run_id)
        should_cancel = lambda: cancel_registry.is_canceled(run_id)  # noqa: E731
    try:
        results = await execute_suite(
            spec, tests, adapter=adapter,
            concurrency=concurrency, timeout_seconds=timeout_seconds, repeat=repeat,
            retries=retries, max_trace_events=max_trace_events,
            max_response_chars=max_response_chars, project_id=project_id,
            should_cancel=should_cancel,
        )
        for r in results:
            store.add_execution(run_id, result_to_storage(r, spec))
        stats = summarize(results)
        any_canceled = stats.get("canceled", 0) > 0
        store.complete_run(run_id, status="canceled" if any_canceled else "completed", **stats)
        if set_baseline:
            store.set_baseline(run_id)
        logger.info("run %s persisted: %s/%s passed, score %s",
                    run_id, stats["passed"], stats["total"], stats["score"])
    finally:
        if cancel_registry is not None:
            cancel_registry.unregister(run_id)

    baseline = store.get_run(baseline_run_id) if baseline_run_id else store.get_baseline(project_id)
    if baseline and baseline["id"] != run_id:
        candidate_detail = store.get_run(run_id)
        diff = diff_runs(baseline, candidate_detail)
    else:
        diff = None
    return run_id, results, diff
