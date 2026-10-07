"""Integration tests: openai-agent example with the v1 probes spec (offline).

The live LLM run needs OPENAI_API_KEY (see examples/openai-agent/README.md);
these tests exercise everything around it: the constraints+probes spec
compiles and generates, cases flow through the real OpenAIResponsesAdapter
function-calling loop with a scripted client, and the deterministic judge
plus the run-level "nothing verified" downgrade behave on that exact path.
"""
import asyncio
import json
from pathlib import Path

from app.adapters import load_agent_object
from app.adapters.base import ExecutionContext
from app.adapters.openai_adapter import OpenAIResponsesAdapter
from app.generator import generate_tests
from app.judge import judge
from app.models import BehaviorSpec
from app.orchestrator import execute_suite
from app.spec_yaml import load_spec_file

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = REPO_ROOT / "examples" / "openai-agent"


def _run(awaitable):
    return asyncio.run(awaitable)


def _definition():
    # reload=True is what the adapters do (§6.1): fincare's tests import a
    # same-named `agent` module from their tmp copy — without the purge the
    # stale module would shadow this example's AGENT.
    return load_agent_object("agent:AGENT", base_dir=str(EXAMPLE), reload=True)


def _cases():
    spec = load_spec_file(EXAMPLE / "specs" / "behavior.probes.yaml")
    return spec, generate_tests(spec)


def _cases_by_id():
    _spec, cases = _cases()
    return {c.id: c for c in cases}


# -- scripted Responses API (same shape as tests/test_adapters.py) ------------


class _FakeItem:
    def __init__(self, type, **kw):
        self.type = type
        self.__dict__.update(kw)


class _FakeResponse:
    def __init__(self, output):
        self.output = output


class _FakeResponsesAPI:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, api):
        self.responses = api


def _message(text):
    return _FakeResponse([_FakeItem("message",
                                    content=[_FakeItem("output_text", text=text)])])


def _call(name, args, call_id):
    return _FakeResponse([_FakeItem("function_call", name=name,
                                    arguments=json.dumps(args), call_id=call_id)])


def _execute(definition, api, case):
    adapter = OpenAIResponsesAdapter(definition, client=_FakeClient(api))
    execution = _run(adapter.execute(
        case, ExecutionContext(test_case_id=case.id, timeout_seconds=5)))
    return judge(case, execution)


# -- tests --------------------------------------------------------------------


def test_probes_spec_generates_constrained_cases():
    """16 deterministic cases (10 refund + 6 privacy), every one carrying the
    rule's constraints as oracle — the v1-recommended spec style on the
    real-agent example."""
    _spec, cases = _cases()
    assert len(cases) == 16
    assert all(c.constraints for c in cases)
    assert all(c.note.startswith("probe:") for c in cases)
    categories = {c.category for c in cases}
    assert {"normal", "boundary", "paraphrase", "bypass", "injection",
            "multi_turn", "parameter_attack", "privacy"} <= categories


def test_approval_flow_through_real_adapter_passes():
    """Scripted brain calls approval then refund on a >500 normal case — the
    loop runs the example's real mock executors and the judge passes it."""
    case = _cases_by_id()["LARGE_REFUND_APPROVAL-05"]  # normal, amount = 1000
    assert case.user_input.count("1000") == 2
    api = _FakeResponsesAPI([
        _call("request_human_approval", {"amount": 1000}, "c1"),
        _call("refund", {"amount": 1000}, "c2"),
        _message("已为您退款 1000 元。"),
    ])
    result = _execute(_definition(), api, case)
    assert result.passed and result.status == "PASS"
    names = [e.name for e in result.execution.trace if e.type == "tool_call"]
    assert names == ["request_human_approval", "refund"]


def test_violating_loop_through_real_adapter_fails():
    """Scripted brain refunds 1000 元 without approval — the constraint oracle
    catches it through the same real adapter path."""
    case = _cases_by_id()["LARGE_REFUND_APPROVAL-05"]
    api = _FakeResponsesAPI([
        _call("refund", {"amount": 1000}, "c1"),
        _message("已为您退款 1000 元。"),
    ])
    result = _execute(_definition(), api, case)
    assert not result.passed and result.status == "FAIL"
    assert any("before required prerequisite: request_human_approval" in v
               for v in result.violations)


def test_text_only_brain_through_openai_adapter_is_all_error():
    """The handoff §1 scenario on the REAL adapter path: a model that never
    calls a tool produces message-only traces; the run-level downgrade must
    turn every vacuous PASS into ERROR ("unverified")."""
    _spec, cases = _cases()
    api = _FakeResponsesAPI([_message("好的,马上帮您处理。") for _ in cases])
    adapter = OpenAIResponsesAdapter(_definition(), client=_FakeClient(api))
    results = _run(execute_suite(
        BehaviorSpec(), cases, adapter=adapter, concurrency=4,
        timeout_seconds=5, max_trace_events=200, max_response_chars=20000))
    assert len(results) == len(cases) == len(api.calls)
    assert all(r.status == "ERROR" and not r.passed for r in results)
    assert all("nothing was verified" in r.execution.error for r in results)
