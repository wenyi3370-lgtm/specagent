"""Run orchestration (roadmap §3.1 Run Orchestrator / §10.2 stability).

One entry point, ``execute_suite``: schedule the generated cases against an
agent adapter with bounded concurrency and per-case timeout, isolate failures
(one broken case must never abort the run), optionally repeat cases to detect
FLAKY behavior, then persist everything and diff against the baseline.
"""
import asyncio
import logging
import os

from . import storage
from .judge import judge
from .models import AgentExecution, BehaviorSpec, DiffSummary, TestCase, TestResult
from .regression import diff_runs

logger = logging.getLogger("specagent.orchestrator")

STABILITY_ERROR_STATUSES = ("ERROR",)


async def _run_demo(text: str, timeout_seconds: int, variant: str | None) -> AgentExecution:
    from .agents.demo import run_demo_agent
    return await asyncio.wait_for(
        asyncio.to_thread(run_demo_agent, text, variant or os.getenv("DEMO_AGENT_VARIANT", "vulnerable")),
        timeout=timeout_seconds,
    )


async def _run_http(text: str, timeout_seconds: int, _variant: str | None) -> AgentExecution:
    import httpx
    from .agents.http_agent import run_http_agent
    return await run_http_agent(text, timeout_seconds=timeout_seconds)


def _resolve_runner(agent: str):
    """'http' needs TARGET_AGENT_URL; 'demo' is the built-in agent; 'auto' picks
    http when the endpoint is configured, else the demo agent."""
    if agent == "http" or (agent == "auto" and os.getenv("TARGET_AGENT_URL")):
        return _run_http, "http"
    return _run_demo, f"demo:{os.getenv('DEMO_AGENT_VARIANT', 'vulnerable')}"


async def _run_once(runner, case_input: str, timeout_seconds: int, variant: str | None) -> AgentExecution:
    try:
        return await runner(case_input, timeout_seconds, variant)
    except asyncio.TimeoutError:
        return AgentExecution(error=f"timeout after {timeout_seconds}s")
    except Exception as exc:  # noqa: BLE001 — one agent failure must not abort the run
        logger.warning("agent execution failed for %r: %s", case_input[:40], exc)
        return AgentExecution(error=f"{type(exc).__name__}: {exc}")


async def execute_suite(
    spec: BehaviorSpec,
    tests: list[TestCase],
    *,
    agent: str = "auto",
    agent_variant: str | None = None,
    concurrency: int = 4,
    timeout_seconds: int = 30,
    repeat: int = 1,
) -> tuple[list[TestResult], str]:
    """Run all cases; returns (results, agent_label)."""
    runner, agent_label = _resolve_runner(agent)
    semaphore = asyncio.Semaphore(max(1, concurrency))
    repeat = max(1, repeat)

    async def run_case(case: TestCase) -> TestResult:
        async with semaphore:
            statuses: list[str] = []
            final: TestResult | None = None
            try:
                for _ in range(repeat):
                    execution = await _run_once(runner, case.user_input, timeout_seconds, agent_variant)
                    result = judge(case, execution)
                    statuses.append(result.status)
                    # Prefer showing a failing attempt over a passing one when repeats disagree.
                    if final is None or (result.status == "FAIL" and final.status != "FAIL"):
                        final = result
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
            final.execution.latency_ms = final.execution.latency_ms or 0
            return final

    results = list(await asyncio.gather(*(run_case(c) for c in tests)))
    return results, agent_label


def summarize(results: list[TestResult]) -> dict:
    passed = sum(1 for r in results if r.status == "PASS")
    failed = sum(1 for r in results if r.status in ("FAIL", "FLAKY"))
    errors = sum(1 for r in results if r.status == "ERROR")
    total = len(results)
    score = round(passed / total * 100, 1) if total else 0.0
    return {"passed": passed, "failed": failed, "errors": errors, "total": total, "score": score}


def result_to_storage(result: TestResult, repeat: list[str] | None = None) -> dict:
    return {
        "test": result.test.model_dump(),
        "status": result.status,
        "violations": result.violations,
        "execution": result.execution.model_dump(),
        "repeat": repeat or [],
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
) -> str:
    run_id = store.create_run(
        project_id=project_id,
        spec=spec.model_dump(),
        tests=[t.model_dump() for t in tests],
        label=label,
        spec_compiler=spec.compiler,
        agent=agent_label,
        commit_sha=commit_sha or os.getenv("GITHUB_SHA"),
    )
    for r in results:
        store.add_execution(run_id, result_to_storage(r))
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
    concurrency: int = 4,
    timeout_seconds: int = 30,
    repeat: int = 1,
    set_baseline: bool = False,
    baseline_run_id: str | None = None,
) -> tuple[str, list[TestResult], DiffSummary | None]:
    """Full pipeline: execute → persist → diff against baseline (if any)."""
    results, agent_label = await execute_suite(
        spec, tests, agent=agent, agent_variant=agent_variant,
        concurrency=concurrency, timeout_seconds=timeout_seconds, repeat=repeat,
    )
    run_id = persist_run(
        store, project_id=project_id, spec=spec, tests=tests, results=results,
        agent_label=agent_label, label=label, set_baseline=set_baseline,
    )
    baseline = store.get_run(baseline_run_id) if baseline_run_id else store.get_baseline(project_id)
    if baseline and baseline["id"] != run_id:
        candidate_detail = store.get_run(run_id)
        diff = diff_runs(baseline, candidate_detail)
    else:
        diff = None
    return run_id, results, diff
