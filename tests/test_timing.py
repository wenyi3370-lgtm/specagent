"""Orchestrator wall-clock timing (v1 design §3.2, task 2).

`_execute_once` measures `adapter.execute` with `time.monotonic()` and fills
`latency_ms` only when the adapter did not report one (> 0 is kept; a
sub-millisecond run stays 0). ERROR results carry their elapsed time too.
"""
import asyncio

from app.adapters.base import ExecutionContext
from app.models import BehaviorSpec, TestCase
from app.orchestrator import execute_suite


class _SleepAdapter:
    name = "sleep"

    def __init__(self, reported_latency_ms=0, fail=False):
        self.reported_latency_ms = reported_latency_ms
        self.fail = fail

    async def execute(self, case, context):
        await asyncio.sleep(0.05)
        if self.fail:
            raise RuntimeError("boom")
        from app.models import AgentExecution
        return AgentExecution(response="ok", latency_ms=self.reported_latency_ms)


def _case() -> TestCase:
    return TestCase(id="T-01", rule_id="R", category="normal", user_input="hi")


async def _one(adapter) -> list:
    return await execute_suite(BehaviorSpec(), [_case()], adapter=adapter, concurrency=1,
                               retries=0, max_trace_events=200, max_response_chars=20000)


def test_zero_reported_latency_is_filled_with_wall_clock():
    results = asyncio.run(_one(_SleepAdapter(reported_latency_ms=0)))
    assert results[0].latency_ms >= 40
    assert results[0].execution.latency_ms >= 40


def test_adapter_reported_latency_is_not_overwritten():
    results = asyncio.run(_one(_SleepAdapter(reported_latency_ms=7)))
    assert results[0].latency_ms == 7
    assert results[0].execution.latency_ms == 7


def test_error_result_carries_elapsed_time():
    results = asyncio.run(_one(_SleepAdapter(fail=True)))
    assert results[0].status == "ERROR"
    assert results[0].latency_ms >= 40
    assert results[0].execution.latency_ms >= 40


def test_judge_copies_the_timed_value_into_the_result():
    # The timing must be set before judge() runs — TestResult.latency_ms comes
    # from execution.latency_ms (design §3.2).
    results = asyncio.run(_one(_SleepAdapter(reported_latency_ms=0)))
    assert results[0].execution.latency_ms == results[0].latency_ms


def test_context_helper_is_unused_by_timing():
    # Guard: ExecutionContext carries no timing field; the clock lives in
    # _execute_once only (single source of truth).
    context = ExecutionContext(test_case_id="T-01")
    assert not hasattr(context, "latency_ms")
