"""Unit tests: python adapter + module freshness (v1 design §6.1, task 9)."""
import asyncio
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest

from app.adapters import load_agent_object, resolve_adapter
from app.adapters.base import ExecutionContext
from app.adapters.python_adapter import PythonAdapter
from app.config import AdapterConfig
from app.errors import SpecValidationError
from app.models import AgentExecution, TestCase, TraceEvent
from app.trace import REDACTED

REPO_ROOT = Path(__file__).resolve().parents[1]


def _copy_example(name: str, tmp_path: Path) -> Path:
    src = REPO_ROOT / "examples" / name
    dst = tmp_path / name
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return dst


def _ctx(case, timeout=5):
    return ExecutionContext(test_case_id=case.id, timeout_seconds=timeout)


CASE = TestCase(id="T-01", rule_id="R", category="normal", user_input="你好")


# -- result handling ------------------------------------------------------------


def test_dict_return_normalizes_trace():
    def agent(message):
        return {"response": "ok", "latency_ms": 7, "trace": [
            {"type": "tool", "name": "refund", "args": {"amount": 1, "api_key": "sk-secret"}},
            {"type": "approval_result", "name": "request_human_approval",
             "result": {"approved": True}},
        ]}

    execution = asyncio.run(PythonAdapter(agent, "m:f").execute(CASE, _ctx(CASE)))
    assert execution.response == "ok"
    assert execution.trace[0].type == "tool_call"  # alias "tool" normalized
    assert execution.trace[0].id == "evt_1"
    assert execution.trace[0].args["api_key"] == REDACTED  # redaction applied
    assert execution.trace[1].result == {"approved": True}
    assert execution.latency_ms == 7
    assert execution.raw["latency_ms"] == 7


def test_agent_execution_return():
    def agent(message):
        return AgentExecution(response="hi",
                              trace=[TraceEvent(type="tool_call", name="t", args={"a": 1})])

    execution = asyncio.run(PythonAdapter(agent, "m:f").execute(CASE, _ctx(CASE)))
    assert execution.response == "hi"
    assert execution.trace[0].name == "t"


def test_async_function_supported():
    async def agent(message):
        return {"response": f"echo:{message}"}

    execution = asyncio.run(PythonAdapter(agent, "m:f").execute(CASE, _ctx(CASE)))
    assert execution.response == "echo:你好"


def test_bad_return_type_raises():
    def agent(message):
        return "just a string"

    with pytest.raises(TypeError, match="AgentExecution or dict, got str"):
        asyncio.run(PythonAdapter(agent, "m:f").execute(CASE, _ctx(CASE)))


# -- signature filtering ---------------------------------------------------------


def test_message_only_signature_filters_kwargs():
    seen = {}

    def agent(message):
        seen["message"] = message
        return {"response": "ok", "seen": seen}

    result = asyncio.run(PythonAdapter(agent, "m:f").execute(CASE, _ctx(CASE)))
    assert result.raw['seen'] == {"message": "你好"}  # history/actor not passed
    assert seen == {}  # Execution is isolated from the host process.


def test_history_and_actor_forwarded():
    seen = {}

    def agent(message, history, actor):
        seen.update(message=message, history=list(history), actor=dict(actor))
        return {"response": "ok", "seen": seen}

    case = TestCase(id="T-02", rule_id="R", category="normal", user_input="hi",
                    history=["turn 1"], actor={"account_id": "ACC-1"})
    result = asyncio.run(PythonAdapter(agent, "m:f").execute(case, _ctx(case)))
    assert result.raw['seen'] == {"message": "hi", "history": ["turn 1"], "actor": {"account_id": "ACC-1"}}


def test_var_kwargs_receives_everything():
    seen = {}

    def agent(**kwargs):
        seen.update(kwargs)
        return {"response": "ok", "seen": seen}

    result = asyncio.run(PythonAdapter(agent, "m:f").execute(CASE, _ctx(CASE)))
    assert set(result.raw['seen']) == {"message", "history", "actor"}


def test_timeout_raises():
    def slow(message):
        time.sleep(3.0)
        return {"response": "late"}

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(PythonAdapter(slow, "m:f").execute(CASE, _ctx(CASE, timeout=1)))


# -- factory errors via resolve_adapter -------------------------------------------


def test_resolve_adapter_python_errors(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    with pytest.raises(SpecValidationError, match="required for adapter.type=python"):
        resolve_adapter(AdapterConfig(type="python"), base_dir=str(proj))
    (proj / "empty.py").write_text("VALUE = 1\n", encoding="utf-8")
    with pytest.raises(SpecValidationError, match="cannot import module 'no_such_module'"):
        resolve_adapter(AdapterConfig(type="python", agent="no_such_module:run_agent"),
                        base_dir=str(proj))
    with pytest.raises(SpecValidationError, match="no attribute 'run_agent'"):
        resolve_adapter(AdapterConfig(type="python", agent="empty:run_agent"),
                        base_dir=str(proj))
    (proj / "nc.py").write_text("run_agent = 42\n", encoding="utf-8")
    with pytest.raises(SpecValidationError, match="expected a callable, got int"):
        resolve_adapter(AdapterConfig(type="python", agent="nc:run_agent"), base_dir=str(proj))
    (proj / "bad.py").write_text("def run_agent(msg):\n    return {}\n", encoding="utf-8")
    with pytest.raises(SpecValidationError, match="must accept a 'message' parameter"):
        resolve_adapter(AdapterConfig(type="python", agent="bad:run_agent"), base_dir=str(proj))


def test_adapter_label_records_module_path(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "agent.py").write_text(
        "def run_agent(message, history=None, actor=None):\n    return {'response': 'ok'}\n",
        encoding="utf-8")
    adapter = resolve_adapter(AdapterConfig(type="python", agent="agent:run_agent"),
                              base_dir=str(proj))
    assert adapter.name == "python:agent:run_agent"  # label stored in runs.agent


# -- module freshness (§6.1) --------------------------------------------------------


def test_reload_picks_up_rewritten_module(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "agent.py").write_text(
        "def run_agent(message):\n    return {'response': 'v1'}\n", encoding="utf-8")
    fn = load_agent_object("agent:run_agent", base_dir=str(proj), reload=True)
    assert fn("hi")["response"] == "v1"
    (proj / "agent.py").write_text(
        "def run_agent(message):\n    return {'response': 'v2'}\n", encoding="utf-8")
    fn = load_agent_object("agent:run_agent", base_dir=str(proj), reload=True)
    assert fn("hi")["response"] == "v2"


def test_reload_purges_sibling_modules(tmp_path):
    """The stale-sibling finding (§6.1): agent.py imports a sibling module;
    rewriting the sibling only — same length, so a stale .pyc would survive
    validation — must be visible after reload. The sibling name is unique to
    this test: the §6.1 purge evicts modules under base_dir plus the target
    module name, not arbitrary same-named siblings of OTHER projects."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "reload_probe_helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    (proj / "agent.py").write_text(
        "import reload_probe_helper\n\nget = lambda: reload_probe_helper.VALUE\n",
        encoding="utf-8")
    assert load_agent_object("agent:get", base_dir=str(proj), reload=True)() == 1
    (proj / "reload_probe_helper.py").write_text("VALUE = 2\n", encoding="utf-8")  # same length
    assert load_agent_object("agent:get", base_dir=str(proj), reload=True)() == 2


def test_threaded_reload_smoke_across_example_dirs(tmp_path):
    """8 threads reloading two example dirs that share the plain module name
    `agent` — the import lock serializes; no ImportError; sys.path restored."""
    openai_dir = _copy_example("openai-agent", tmp_path)
    fincare_dir = _copy_example("fincare-agent", tmp_path)
    targets = [(str(openai_dir), "agent:AGENT"), (str(fincare_dir), "agent:run_agent")]
    before_path = list(sys.path)
    errors: list[Exception] = []

    def worker(i):
        try:
            base_dir, path = targets[i % 2]
            for _ in range(3):
                obj = load_agent_object(path, base_dir=base_dir, reload=True)
                assert callable(obj) or hasattr(obj, "model")
        except Exception as exc:  # noqa: BLE001 — collected for the assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert sys.path == before_path
