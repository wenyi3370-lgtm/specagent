"""Unit tests: agent adapters (roadmap v0.4 §7) — all offline via injected fakes."""
import json

import httpx
import pytest

from app.adapters import resolve_adapter
from app.adapters.base import ExecutionContext
from app.adapters.demo_adapter import DemoAdapter
from app.adapters.http_adapter import HttpAdapter
from app.adapters.langgraph_adapter import LangGraphAdapter
from app.adapters.openai_adapter import OpenAIAgentDefinition, OpenAIResponsesAdapter, OpenAITool
from app.config import AdapterConfig
from app.errors import SpecValidationError
from app.models import TestCase

CASE = TestCase(id="T-01", rule_id="R", category="normal", user_input="请退款800元")


def _ctx(timeout=5):
    return ExecutionContext(test_case_id=CASE.id, timeout_seconds=timeout)


async def _call(adapter, case=CASE):
    return await adapter.execute(case, _ctx())


# -- demo adapter ------------------------------------------------------------


def test_demo_adapter_normalizes_ids():
    result = _run(_call(DemoAdapter("patched")))
    assert result.trace
    assert all(e.seq and e.id == f"evt_{e.seq}" for e in result.trace)


def _run(awaitable):
    import asyncio
    return asyncio.run(awaitable)


# -- http adapter ------------------------------------------------------------


def test_http_adapter_sends_contract_and_normalizes_trace():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "response": "ok",
            "trace": [{"type": "tool", "name": "refund", "args": {"amount": 1}}],
        })

    import os
    os.environ["TARGET_AGENT_URL"] = "http://agent.example/test-hook"
    try:
        adapter = HttpAdapter(transport=httpx.MockTransport(handler))
        execution = _run(_call(adapter))
    finally:
        os.environ.pop("TARGET_AGENT_URL")
    assert captured["body"]["message"] == "请退款800元"
    assert captured["body"]["test_case_id"] == "T-01"
    assert "context" in captured["body"]
    assert execution.trace[0].type == "tool_call"  # alias "tool" normalized
    assert execution.trace[0].id == "evt_1"


def test_http_adapter_forwards_history():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"response": "ok", "trace": []})

    import os
    os.environ["TARGET_AGENT_URL"] = "http://agent.example/test-hook"
    try:
        adapter = HttpAdapter(transport=httpx.MockTransport(handler))
        case = CASE.model_copy(update={"history": ["先看看退款政策"]})
        _run(_call(adapter, case))
    finally:
        os.environ.pop("TARGET_AGENT_URL")
    assert captured["body"]["history"] == ["先看看退款政策"]


def test_http_adapter_requires_endpoint():
    import os
    os.environ.pop("TARGET_AGENT_URL", None)
    adapter = HttpAdapter()
    with pytest.raises(RuntimeError, match="TARGET_AGENT_URL"):
        _run(_call(adapter))


# -- openai adapter ----------------------------------------------------------


class _FakeItem:
    def __init__(self, type, **kw):
        self.type = type
        self.__dict__.update(kw)


class _FakeResponse:
    def __init__(self, output):
        self.output = output

    def model_dump(self):
        return {"output_count": len(self.output)}


class _FakeResponsesAPI:
    """Scripted Responses API: pops one FakeResponse per call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


def test_openai_adapter_runs_tool_loop_and_normalizes_trace():
    approval = OpenAITool(name="request_human_approval", executor=lambda a: {"status": "approved"})
    refund = OpenAITool(name="refund", executor=lambda a: {"status": "refunded"})
    definition = OpenAIAgentDefinition(model="gpt-fake", instructions="behave", tools=[approval, refund])
    api = _FakeResponsesAPI([
        _FakeResponse([_FakeItem("function_call", name="request_human_approval",
                                 arguments=json.dumps({"amount": 1200}), call_id="c1")]),
        _FakeResponse([_FakeItem("function_call", name="refund",
                                 arguments=json.dumps({"amount": 1200}), call_id="c2")]),
        _FakeResponse([_FakeItem("message", content=[_FakeItem("output_text", text="已退款")])]),
    ])

    class _FakeClient:
        def __init__(self):
            self.responses = api

    execution = _run(_call(OpenAIResponsesAdapter(definition, client=_FakeClient())))

    names = [(e.type, e.name) for e in execution.trace]
    assert ("user_message", "user_input") in names
    assert ("tool_call", "request_human_approval") in names
    assert ("tool_result", "request_human_approval") in names
    assert ("tool_call", "refund") in names
    assert ("assistant_message", "reply") in names
    assert execution.response == "已退款"
    assert execution.raw == {"output_count": 1}
    # approval must precede refund in the normalized trace → judge-able ordering
    seqs = {e.name: e.seq for e in execution.trace if e.type == "tool_call"}
    assert seqs["request_human_approval"] < seqs["refund"]
    # tool outputs were fed back into the loop
    assert any(isinstance(c, dict) and c.get("type") == "function_call_output"
               for c in api.calls[1]["input"])


def test_openai_adapter_unregistered_tool_returns_error_in_trace():
    definition = OpenAIAgentDefinition(tools=[])
    api = _FakeResponsesAPI([
        _FakeResponse([_FakeItem("function_call", name="explode", arguments="{}", call_id="c1")]),
        _FakeResponse([_FakeItem("message", content=[_FakeItem("output_text", text="done")])]),
    ])

    class _FakeClient:
        responses = api

    execution = _run(_call(OpenAIResponsesAdapter(definition, client=_FakeClient())))
    tool_result = next(e for e in execution.trace if e.type == "tool_result")
    assert "no executor registered" in str(tool_result.result)


def test_openai_adapter_rejects_wrong_definition_type():
    with pytest.raises(TypeError):
        OpenAIResponsesAdapter({"model": "x"})


# -- langgraph adapter -------------------------------------------------------


class _FakeGraph:
    def __init__(self, events):
        self._events = events

    async def astream_events(self, inputs, version=None):
        assert version == "v2"
        for e in self._events:
            yield e


def test_langgraph_adapter_converts_stream_events():
    graph = _FakeGraph([
        {"event": "on_tool_start", "name": "refund", "data": {"input": {"amount": 1200}}},
        {"event": "on_tool_end", "name": "refund", "data": {"output": "refunded"}},
        {"event": "on_chat_model_end", "data": {"output": type("M", (), {"content": "好的"})()}},
        {"event": "on_chain_start", "name": "node"},  # ignored
    ])
    execution = _run(_call(LangGraphAdapter(graph)))
    assert [(e.type, e.name) for e in execution.trace] == [
        ("user_message", "user_input"),
        ("tool_call", "refund"),
        ("tool_result", "refund"),
        ("assistant_message", "reply"),
    ]
    assert execution.response == "好的"
    assert execution.trace[1].args == {"amount": 1200}


def test_langgraph_adapter_rejects_non_graph():
    with pytest.raises(TypeError):
        LangGraphAdapter(object())


# -- resolution --------------------------------------------------------------


def test_resolve_adapter_from_config():
    assert isinstance(resolve_adapter(AdapterConfig(type="demo", variant="patched")), DemoAdapter)
    assert resolve_adapter(AdapterConfig(type="demo", variant="patched")).name == "demo:patched"


def test_resolve_adapter_openai_requires_agent_path():
    with pytest.raises(SpecValidationError):
        resolve_adapter(AdapterConfig(type="openai"))


def test_resolve_adapter_openai_missing_module_is_field_error():
    config = AdapterConfig(type="openai", agent="no_such_module_xyz:AGENT")
    with pytest.raises(SpecValidationError) as exc:
        resolve_adapter(config)
    assert "no_such_module_xyz" in exc.value.errors[0]


def test_resolve_adapter_string_agent_unknown():
    with pytest.raises(SpecValidationError):
        resolve_adapter(agent="langgraph")  # framework adapters need config


def test_load_agent_object_local_module(tmp_path):
    (tmp_path / "mymod.py").write_text("AGENT = 42\n", encoding="utf-8")
    from app.adapters import load_agent_object
    assert load_agent_object("mymod:AGENT", base_dir=str(tmp_path)) == 42
