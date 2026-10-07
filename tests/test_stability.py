"""Unit tests: v0.7 stability & security (roadmap §10)."""
import asyncio
import json

import httpx
import pytest

from app.adapters.base import ExecutionContext, TransientAgentError
from app.adapters.http_adapter import HttpAdapter, host_allowed
from app.models import AgentExecution, TestCase, TraceEvent
from app.orchestrator import _project_semaphore, execute_suite
from app.trace import apply_limits

CASE = TestCase(id="T-01", rule_id="R", category="normal", user_input="hi")


def _ok_execution() -> AgentExecution:
    # A trace with one tool call: a zero-activity run downgrades PASS to
    # ERROR ("nothing verified"), and these stubs test retry/cancellation,
    # not that downgrade.
    return AgentExecution(response="ok",
                          trace=[TraceEvent(type="tool_call", name="tool", seq=1)])


def _ctx():
    return ExecutionContext(test_case_id=CASE.id, timeout_seconds=5)


def _run(awaitable):
    return asyncio.run(awaitable)


# -- allowlist (§10.1 SSRF guard) --------------------------------------------


def test_host_allowed_matching():
    assert host_allowed("agent.example.com", ["agent.example.com"])
    assert host_allowed("AGENT.Example.COM", ["agent.example.com"])
    assert host_allowed("api.agent.example.com", ["*.agent.example.com"])
    assert not host_allowed("evil.example.com", ["agent.example.com"])
    assert not host_allowed("agent.example.com.evil.io", ["agent.example.com"])
    assert not host_allowed("agent.example.com", ["*.agent.example.com"])  # bare suffix rejected
    assert not host_allowed(None, ["*"])


def _http_with_response(status: int, body=None) -> HttpAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body or {"response": "x", "trace": []})

    return HttpAdapter(transport=httpx.MockTransport(handler))


def test_http_adapter_allowlist_blocks_foreign_host(monkeypatch):
    monkeypatch.setenv("TARGET_AGENT_URL", "https://internal-agent.example.com/hook")
    adapter = HttpAdapter(allowed_hosts=["agent.example.com"])
    with pytest.raises(RuntimeError, match="not in the allowlist"):
        _run(adapter.execute(CASE, _ctx()))


def test_http_adapter_allowlist_permits_listed_host(monkeypatch):
    monkeypatch.setenv("TARGET_AGENT_URL", "https://agent.example.com/hook")
    adapter = _http_with_response(200)
    adapter.allowed_hosts = ["agent.example.com"]
    execution = _run(adapter.execute(CASE, _ctx()))
    assert execution.error is None


def test_http_adapter_env_allowlist_merges(monkeypatch):
    monkeypatch.setenv("TARGET_AGENT_URL", "https://staging.internal/hook")
    monkeypatch.setenv("SPECAGENT_ALLOWED_HOSTS", "a.com, staging.internal")
    adapter = _http_with_response(200)
    assert adapter.allowed_hosts == ["a.com", "staging.internal"]
    assert _run(adapter.execute(CASE, _ctx())).response == "x"


# -- transient errors & retry (§10.2) ----------------------------------------


def test_http_adapter_5xx_is_transient_but_4xx_is_not(monkeypatch):
    monkeypatch.setenv("TARGET_AGENT_URL", "https://agent.example.com/hook")
    transient = _http_with_response(503)
    with pytest.raises(TransientAgentError):
        _run(transient.execute(CASE, _ctx()))
    permanent = _http_with_response(400)
    with pytest.raises(httpx.HTTPStatusError):
        _run(permanent.execute(CASE, _ctx()))


class _FlakyAdapter:
    name = "flaky"

    def __init__(self, failures, error_type=TransientAgentError):
        self.failures = failures
        self.calls = 0
        self.error_type = error_type

    async def execute(self, case, context):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error_type("boom")
        return _ok_execution()


async def _suite(adapter, **kw):
    from app.models import BehaviorSpec
    return await execute_suite(
        BehaviorSpec(), [CASE], adapter=adapter, concurrency=1,
        max_trace_events=200, max_response_chars=20000, **kw)


def test_transient_errors_are_retried_then_recover():
    adapter = _FlakyAdapter(failures=2)
    results = _run(_suite(adapter, retries=3))
    assert adapter.calls == 3
    assert results[0].status == "PASS"


def test_transient_errors_exhausting_retries_become_error():
    adapter = _FlakyAdapter(failures=99)
    results = _run(_suite(adapter, retries=2))
    assert adapter.calls == 3  # initial + 2 retries
    assert results[0].status == "ERROR"
    assert "boom" in results[0].execution.error


def test_business_errors_are_never_retried():
    adapter = _FlakyAdapter(failures=99, error_type=RuntimeError)
    results = _run(_suite(adapter, retries=5))
    assert adapter.calls == 1
    assert results[0].status == "ERROR"


# -- trace / response limits (§10.2 Trace Limit) -----------------------------


def test_trace_and_response_limits_mark_truncation():
    execution = AgentExecution(
        response="x" * 100,
        trace=[{"seq": i, "id": f"evt_{i}", "type": "assistant_message", "name": "e"} for i in range(1, 51)],
    )
    apply_limits(execution, max_trace_events=10, max_response_chars=20)
    assert execution.truncated is True
    assert len(execution.trace) == 10
    assert execution.trace[-1].id == "evt_10"
    assert len(execution.response) == 20


def test_limits_not_applied_when_within_bounds():
    execution = AgentExecution(response="short", trace=[{"seq": 1, "type": "assistant_message"}])
    apply_limits(execution, max_trace_events=10, max_response_chars=100)
    assert execution.truncated is False


# -- cancellation (§10.2) ----------------------------------------------------


def test_cancel_registry_lifecycle():
    from app.cancellation import CancelRegistry
    registry = CancelRegistry()
    assert registry.cancel("run_x") is False  # not active
    registry.register("run_x")
    assert registry.cancel("run_x") is True
    assert registry.is_canceled("run_x")
    registry.unregister("run_x")
    assert registry.is_canceled("run_x") is False


def test_canceled_pending_cases_preserve_partial_results():
    completed: list[str] = []

    class _SlowAdapter:
        name = "slow"

        async def execute(self, case, context):
            await asyncio.sleep(0.15)
            completed.append(case.id)
            return _ok_execution()

    from app.models import BehaviorSpec
    cases = [TestCase(id=f"T-{i:02}", rule_id="R", category="normal", user_input="x") for i in range(1, 7)]
    state = {"canceled": False}

    def should_cancel():
        return state["canceled"]

    async def scenario():
        async def cancel_soon():
            await asyncio.sleep(0.3)
            state["canceled"] = True
        results, _ = await asyncio.gather(
            _suite_inner(cases, should_cancel),
            cancel_soon(),
        )
        return results

    async def _suite_inner(cases, should_cancel):
        return await execute_suite(
            BehaviorSpec(), cases, adapter=_SlowAdapter(),
            concurrency=1, retries=0, max_trace_events=200,
            max_response_chars=20000, should_cancel=should_cancel)

    results = _run(scenario())
    statuses = [r.status for r in results]
    assert "PASS" in statuses and "CANCELED" in statuses  # partial results kept
    assert all(case_id in completed for case_id in
               [r.test.id for r in results if r.status == "PASS"])
    assert statuses.count("CANCELED") == 6 - len(completed)


# -- project-level concurrency (§10.2) ---------------------------------------


def test_project_semaphore_shared_and_bounded():
    assert _project_semaphore("p1") is _project_semaphore("p1")
    assert _project_semaphore("p1") is not _project_semaphore("p2")


# -- nothing-verified runs (handoff 2026-10-07 §1) ----------------------------


def _text_only_adapter():
    class _TextOnly:
        name = "text_only"

        async def execute(self, case, context):
            return AgentExecution(response="sure, done!")
    return _TextOnly()


def test_zero_activity_run_downgrades_pass_to_error():
    """A text-only agent must not score a green run: with no tool_call in any
    trace, every PASS is vacuous and becomes ERROR ("unverified")."""
    from app.models import BehaviorSpec
    cases = [TestCase(id=f"T-{i:02}", rule_id="R", category="normal", user_input="x")
             for i in range(1, 4)]
    results = _run(execute_suite(
        BehaviorSpec(), cases, adapter=_text_only_adapter(), concurrency=2,
        max_trace_events=200, max_response_chars=20000))
    assert all(r.status == "ERROR" and not r.passed for r in results)
    assert all("nothing was verified" in r.execution.error for r in results)


def test_run_with_any_tool_call_is_not_downgraded():
    """One genuine refusal case among engaging cases stays PASS — the run
    verified something."""
    from app.models import BehaviorSpec

    class _Mixed:
        name = "mixed"

        async def execute(self, case, context):
            if case.id == "T-01":  # refusal: empty trace, still PASS (§5.2 rule 3)
                return AgentExecution(response="已拒绝", trace=[])
            return _ok_execution()

    results = _run(execute_suite(
        BehaviorSpec(), [CASE, TestCase(id="T-02", rule_id="R", category="normal",
                                        user_input="x")],
        adapter=_Mixed(), concurrency=1,
        max_trace_events=200, max_response_chars=20000))
    assert [r.status for r in results] == ["PASS", "PASS"]


def test_downgrade_helper_skips_failures_and_cancellations():
    from app.models import BehaviorSpec
    spec = BehaviorSpec()

    class _FailThenText:
        name = "fail_then_text"

        async def execute(self, case, context):
            if case.id == "T-01":  # deterministic violation
                return AgentExecution(response="ok", trace=[
                    TraceEvent(type="tool_call", name="delete_account", seq=1)])
            return AgentExecution(response="ok", trace=[])

    results = _run(execute_suite(
        spec,
        [TestCase(id="T-01", rule_id="R", category="normal", user_input="x",
                  forbidden_calls=["delete_account"]),
         TestCase(id="T-02", rule_id="R", category="normal", user_input="x")],
        adapter=_FailThenText(), concurrency=1,
        max_trace_events=200, max_response_chars=20000))
    assert results[0].status == "FAIL"      # real violation survives untouched
    assert results[1].status == "PASS"      # a tool call exists in the run
