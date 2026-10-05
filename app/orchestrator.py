"""Run orchestration (roadmap §3.1 Run Orchestrator / §10.2 stability).

One entry point, ``execute_suite``: schedule the generated cases against an
AgentAdapter with bounded concurrency and per-case timeout, isolate failures
(one broken case must never abort the run), optionally repeat cases to detect
FLAKY behavior, then persist everything and diff against the baseline.

The orchestrator is adapter-agnostic: it never imports a framework. Adapters
raise; this module converts failures into per-case ERROR results.
"""
import asyncio
import logging
import os

from . import storage
from .adapters import resolve_adapter
from .adapters.base import AgentAdapter, ExecutionContext
from .judge import judge
from .llm_judge import judge_with_llm_async
from .models import AgentExecution, BehaviorSpec, DiffSummary, TestCase, TestResult
from .regression import diff_runs

logger = logging.getLogger("specagent.orchestrator")


async def _execute_once(adapter: AgentAdapter, case: TestCase, context: ExecutionContext) -> AgentExecution:
    try:
        execution = await adapter.execute(case, context)
        return execution
    except asyncio.TimeoutError:
        return AgentExecution(error=f"timeout after {context.timeout_seconds}s")
    except Exception as exc:  # noqa: BLE001 — one agent failure must not abort the run
        logger.warning("agent execution failed for case %s: %s", case.id, exc)
        return AgentExecution(error=f"{type(exc).__name__}: {exc}")


async def execute_suite(
    spec: BehaviorSpec,
    tests: list[TestCase],
    *,
    adapter: AgentAdapter,
    concurrency: int = 4,
    timeout_seconds: int = 30,
    repeat: int = 1,
) -> list[TestResult]:
    """Run all cases against the adapter; returns results."""
    semaphore = asyncio.Semaphore(max(1, concurrency))
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
                    execution = await _execute_once(adapter, case, context)
                    result = judge(case, execution)
                    statuses.append(result.status)
                    if final is None or (result.status == "FAIL" and final.status != "FAIL"):
                        final = result
                # LLM judge (§8.3 layer 3) runs once per case, advisory only.
                if case.llm_checks and final is not None:
                    final.llm_verdict = await judge_with_llm_async(case, final.execution)
            except Exception as exc:  # noqa: BLE001 — defensive: judge/trace bugs isolate here too
                logger.exception("case %s crashed", case.id)
                final = TestResult(
                    test=case, passed=False, status="ERROR",
                    violations=[], execution=AgentExecution(error=f"orchestrator: {exc}"),
                )
            assert final is not None
            if len(set(statuses)) > 1:
                final.status = "FLAKY"
                final.passed = False
                final.violations = final.violations or [f"unstable behavior across {repeat} repeats: {statuses}"]
            return final

    return list(await asyncio.gather(*(run_case(c) for c in tests)))


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
    set_baseline: bool = False,
    baseline_run_id: str | None = None,
    spec_source: str | None = None,
) -> tuple[str, list[TestResult], DiffSummary | None]:
    """Full pipeline: execute → persist → diff against baseline (if any)."""
    if adapter is None:
        adapter = resolve_adapter(agent=agent, agent_variant=agent_variant)
    results = await execute_suite(
        spec, tests, adapter=adapter,
        concurrency=concurrency, timeout_seconds=timeout_seconds, repeat=repeat,
    )
    run_id = persist_run(
        store, project_id=project_id, spec=spec, tests=tests, results=results,
        agent_label=adapter.name, label=label, set_baseline=set_baseline,
        spec_source=spec_source,
    )
    baseline = store.get_run(baseline_run_id) if baseline_run_id else store.get_baseline(project_id)
    if baseline and baseline["id"] != run_id:
        candidate_detail = store.get_run(run_id)
        diff = diff_runs(baseline, candidate_detail)
    else:
        diff = None
    return run_id, results, diff
