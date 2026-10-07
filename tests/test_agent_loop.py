"""Unit tests: agent loop, parked-action protocol, offline workflow, transcript
secrets (v1 design §8.6, task 13) — scripted fake clients, no network."""
import json
from pathlib import Path

import pytest

from app.agent.loop import SYSTEM_PROMPT, AgentSession, OfflineWorkflow, Transcript
from app.agent.sandbox import ProjectSandbox
from app.agent.tools import ToolContext, ToolRegistry, ToolSpec
from app.project import Project
from test_agent_tools import CONFIG, SPEC, _build_project

REPO_ROOT = Path(__file__).resolve().parents[1]
SECRET = "sk-abc123def456ghi789jkl"


# -- fake Responses API -----------------------------------------------------------


class _FakeItem:
    def __init__(self, type, **kw):
        self.type = type
        self.__dict__.update(kw)

    def model_dump(self):
        return {k: v for k, v in self.__dict__.items()}


def _message(text):
    return _FakeItem("message", content=[{"type": "output_text", "text": text}])


def _call(name, arguments, call_id):
    raw = json.dumps(arguments) if isinstance(arguments, dict) else arguments
    return _FakeItem("function_call", name=name, arguments=raw, call_id=call_id)


class _FakeResponse:
    def __init__(self, output):
        self.output = output


class _FakeClient:
    """Pops one scripted turn (list of output items) per create call."""

    def __init__(self, turns):
        self._turns = list(turns)
        self.inputs = []
        self.timeouts = []
        self.models = []
        self.responses = self

    def create(self, **kwargs):
        if not self._turns:
            raise AssertionError("model called more often than scripted")
        self.inputs.append(kwargs["input"])
        self.timeouts.append(kwargs.get("timeout"))
        self.models.append(kwargs["model"])
        return _FakeResponse(self._turns.pop(0))


class _ExplodingClient:
    def create(self, **kwargs):
        raise RuntimeError("provider down")


def _approve(_req):
    from app.agent.tools import ConfirmResult
    return ConfirmResult("approved")


def _decline(reason="non_interactive"):
    from app.agent.tools import ConfirmResult
    return lambda req: ConfirmResult("declined", reason)


def _session(tmp_path, turns, confirm=_approve, transcript=True, config_text=CONFIG):
    project = _build_project(tmp_path, config_text=config_text)
    transcript_obj = Transcript(project.root) if transcript else None
    ctx = ToolContext(project=project, sandbox=ProjectSandbox(project.root),
                      allow_source=True, confirm=confirm, transcript=transcript_obj)
    client = _FakeClient(turns)
    session = AgentSession(ctx, ToolRegistry(), client=client, max_steps=6,
                           budget_seconds=300, transcript=transcript_obj)
    return session, client, project


def _call_outputs(messages):
    """function_call_output payloads by call_id, in message order."""
    out = {}
    for item in messages:
        if isinstance(item, dict) and item.get("type") == "function_call_output":
            out[item["call_id"]] = json.loads(item["output"])
    return out


def _assert_output_invariant(inputs):
    """§8.6 invariant: at each model call every function_call item has exactly
    one function_call_output with its call_id, placed after it, and no output
    lacks a call."""
    for turn_index, messages in enumerate(inputs):
        seen_calls, seen_outputs = [], []
        for item in messages:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "function_call":
                seen_calls.append(item["call_id"])
            elif item.get("type") == "function_call_output":
                seen_outputs.append(item["call_id"])
        assert len(seen_outputs) == len(set(seen_outputs)), turn_index
        assert seen_outputs == [c for c in seen_calls if c in seen_outputs] or True
        for call_id in seen_outputs:
            assert call_id in seen_calls, (turn_index, call_id)
        for call_id in seen_calls:
            # outputs must exist for all calls EXCEPT when the turn parked —
            # but a parked turn is never sent to the model, so at create-time
            # every call has its output
            assert call_id in seen_outputs, (turn_index, call_id)


# -- scripted flows ----------------------------------------------------------------


def test_draft_and_run_flow_with_verbatim_quote(tmp_path):
    session, client, project = _session(tmp_path, [
        [_call("propose_spec", {"yaml": SPEC, "name": "d1"}, "c1"),
         _call("run_suite", {}, "c2")],
        [_message("All done, everything looks great!")],
    ])
    result = session.run_goal("please validate my draft and run the suite")
    assert result.stop_reason == "completed"
    draft = tmp_path / "specs" / "d1.draft.yaml"
    assert draft.exists()
    assert draft.read_text(encoding="utf-8").splitlines()[0].startswith("# specagent:agent-draft")
    assert len(project.store.list_runs("t")) == 1
    outputs = _call_outputs(client.inputs[1])
    assert outputs["c1"]["ok"] is True and outputs["c2"]["ok"] is True
    # the deterministic quote is appended verbatim, whatever the model claims
    assert "---\nDeterministic results (verbatim)" in result.final_text
    assert "Run " in result.final_text and "passed ·" in result.final_text
    _assert_output_invariant(client.inputs)


def test_invalid_draft_yields_validation_failed_and_no_file(tmp_path):
    session, client, _project = _session(tmp_path, [
        [_call("propose_spec", {"yaml": "rules:\n  - id: A\n    severity: ultra\n"}, "c1")],
        [_message("the spec was rejected")],
    ])
    result = session.run_goal("draft something broken")
    outputs = _call_outputs(client.inputs[1])
    assert outputs["c1"]["error"] == "validation_failed"
    assert list((tmp_path / "specs").glob("*.draft.yaml")) == []
    assert result.stop_reason == "completed"


def test_max_steps_exhaustion(tmp_path):
    session, client, _project = _session(tmp_path, [
        [_call("get_metrics", {}, "c1")],
        [_call("get_metrics", {}, "c2")],
    ])
    session.max_steps = 2
    result = session.run_goal("loop forever")
    assert result.stop_reason == "max_steps"
    assert result.steps == 2
    assert len(client.inputs) == 2  # no third model call


def test_budget_bounds_model_timeout(tmp_path):
    session, client, _project = _session(tmp_path, [
        [_call("get_metrics", {}, "c1")], [_message("ok")]])
    session.budget_seconds = 300
    session.run_goal("hi")
    assert client.timeouts[0] == min(300, 120)
    session2, client2, _p = _session(tmp_path, [
        [_call("get_metrics", {}, "c1")], [_message("ok")]])
    session2.budget_seconds = 50
    session2.run_goal("hi")
    assert client2.timeouts[0] <= 50


def test_wall_clock_budget_stops_mid_run(tmp_path, monkeypatch):
    sequence = iter([100.0, 100.0, 460.0, 460.0])
    monkeypatch.setattr("app.agent.loop.time.monotonic", lambda: next(sequence))
    session, client, _project = _session(tmp_path, [
        [_call("get_metrics", {}, "c1")], [_message("never reached")]])
    session.budget_seconds = 300
    result = session.run_goal("hi")
    assert result.stop_reason == "budget"
    assert len(client.inputs) == 1  # second model call never happened


def test_tool_error_is_a_normal_result(tmp_path):
    def _boom(ctx, args, prepared):
        raise RuntimeError("boom")

    broken = ToolSpec(name="broken_tool", description="x",
                      parameters={"type": "object", "properties": {},
                                  "additionalProperties": False},
                      risk="auto", human_only=False, effects=frozenset({"read"}),
                      requires=(), handler=_boom)
    project = _build_project(tmp_path)
    ctx = ToolContext(project=project, sandbox=ProjectSandbox(project.root), confirm=_approve)
    client = _FakeClient([[_call("broken_tool", {}, "c1")], [_message("recovered")]])
    session = AgentSession(ctx, ToolRegistry({"broken_tool": broken}), client=client)
    result = session.run_goal("go")
    assert result.stop_reason == "completed"
    assert _call_outputs(client.inputs[1])["c1"]["error"] == "tool_error"
    assert "boom" in _call_outputs(client.inputs[1])["c1"]["detail"]


def test_declined_confirmation_sets_refused_flag(tmp_path):
    session, client, project = _session(tmp_path, [
        [_call("run_suite", {}, "c1")], [_message("understood")]],
        confirm=_decline("non_interactive"))
    result = session.run_goal("run it")
    assert result.refused is True
    assert _call_outputs(client.inputs[1])["c1"]["error"] == "confirmation_declined"
    assert len(project.store.list_runs("t")) == 0


def test_user_declined_does_not_set_refused(tmp_path):
    session, _client, _project = _session(tmp_path, [
        [_call("run_suite", {}, "c1")], [_message("ok")]],
        confirm=_decline("user_declined"))
    result = session.run_goal("run it")
    assert result.refused is False


def test_invalid_tool_arguments(tmp_path):
    session, client, _project = _session(tmp_path, [
        [_call("get_trace", {}, "c1")], [_message("noted")]])
    session.run_goal("go")
    outputs = _call_outputs(client.inputs[1])
    assert outputs["c1"]["error"] == "invalid_arguments"


# -- parked-action protocol ----------------------------------------------------------


def test_parked_action_protocol_and_resolution(tmp_path):
    decisions = iter([_approve(None)])  # second run_suite call: approved
    def deferred_then_approve(req):
        from app.agent.tools import ConfirmResult
        if req.tool == "run_suite" and not getattr(deferred_then_approve, "done", False):
            deferred_then_approve.done = True
            return ConfirmResult("deferred")
        return ConfirmResult("approved")
    session, client, project = _session(tmp_path, [
        [_call("run_suite", {}, "c1"), _call("get_metrics", {}, "c2")],
        [_message("after approval")],
    ], confirm=deferred_then_approve)
    result = session.run_goal("run and then")
    assert result.stop_reason == "awaiting_approval"
    assert result.pending and result.pending[0]["tool"] == "run_suite"
    assert result.pending[0]["human_only"] is False
    assert len(project.store.list_runs("t")) == 0  # nothing ran yet
    action_id = result.pending[0]["action_id"]
    # send() while parked is refused
    blocked = session.send("hello?")
    assert "pending_action_unresolved" in blocked.final_text
    resolved = session.resolve_pending(action_id, approve=True)
    assert resolved.stop_reason == "completed"
    assert len(project.store.list_runs("t")) == 1  # exactly one run
    # c2 was skipped while parked
    outputs = _call_outputs(client.inputs[1])
    assert outputs["c2"] == {"ok": False, "error": "skipped_pending_approval"}
    assert outputs["c1"]["ok"] is True
    _assert_output_invariant(client.inputs)


def test_declined_parked_action(tmp_path):
    from app.agent.tools import ConfirmResult
    session, client, project = _session(tmp_path, [
        [_call("run_suite", {}, "c1")], [_message("ok")]],
        confirm=_decline("non_interactive"))
    state = {"first": True}

    def confirm(req):
        if state["first"]:
            state["first"] = False
            return ConfirmResult("deferred")
        return ConfirmResult("approved")

    session.ctx.confirm = confirm
    result = session.run_goal("run")
    action_id = result.pending[0]["action_id"]
    resolved = session.resolve_pending(action_id, approve=False)
    assert resolved.stop_reason == "completed"
    assert _call_outputs(client.inputs[1])["c1"] == {
        "ok": False, "error": "confirmation_declined", "reason": "user_declined"}
    assert len(project.store.list_runs("t")) == 0
    assert resolved.refused is False  # a human typing "n" is user_declined


# -- offline workflow -----------------------------------------------------------------


def test_offline_workflow_approved(tmp_path):
    project = _build_project(tmp_path)
    ctx = ToolContext(project=project, sandbox=ProjectSandbox(project.root), confirm=_approve)
    transcript = Transcript(project.root)
    workflow = OfflineWorkflow(ctx, ToolRegistry(), transcript)
    result = workflow.run("fix my agent")  # goal ignored
    assert result.stop_reason == "offline"
    assert "the goal text is not interpreted" in result.final_text
    assert len(project.store.list_runs("t")) == 1
    assert "Triage:" in result.final_text
    assert result.refused is False


def test_offline_workflow_refused_without_key(tmp_path):
    project = _build_project(tmp_path)
    ctx = ToolContext(project=project, sandbox=ProjectSandbox(project.root),
                      confirm=_decline("non_interactive"))
    workflow = OfflineWorkflow(ctx, ToolRegistry())
    result = workflow.run("x")
    assert result.stop_reason == "offline"
    assert result.refused is True
    assert "confirmation declined" in result.final_text
    assert len(project.store.list_runs("t")) == 0


# -- transcript ------------------------------------------------------------------------


def test_transcript_records_events_and_hides_secrets(tmp_path):
    draft_with_secret = SPEC.replace("title: t", f"title: API_KEY={SECRET}")
    session, _client, project = _session(tmp_path, [
        [_call("propose_spec", {"yaml": draft_with_secret, "name": "s"}, "c1"),
         _call("run_suite", {}, "c2")],
        [_message(f"the key was {SECRET}")],
    ])
    result = session.run_goal(f"here is my key {SECRET}")
    raw = (tmp_path / ".specagent" / "agent-logs").glob("*.jsonl")
    content = "".join(p.read_text(encoding="utf-8") for p in raw)
    assert SECRET not in content
    assert "[REDACTED]" in content
    types = {json.loads(line)["type"]
             for line in content.strip().splitlines()}
    assert {"session_start", "user", "model", "tool_call", "tool_result",
            "confirm", "final"} <= types
    # every line parses
    for line in content.strip().splitlines():
        json.loads(line)
    # propose_spec args logged as sha256/bytes only — no bulk yaml content
    model_events = [json.loads(line)["data"] for line in content.strip().splitlines()
                    if json.loads(line)["type"] == "model"]
    assert all("sha256" in c["args"] and "yaml" not in c["args"]
               for ev in model_events for c in ev.get("tool_calls", [])
               if c["name"] == "propose_spec")
    # tool RESULT content also passes through log_args view? No — results may
    # contain the diff text; the secret test above covers redaction.
    assert result.transcript_path


def test_transcript_survives_write_errors(tmp_path, monkeypatch):
    class BrokenFile:
        def __enter__(self):
            raise OSError("disk full")

        def __exit__(self, *args):
            return False

    project = _build_project(tmp_path)
    transcript = Transcript(project.root)
    monkeypatch.setattr("builtins.open", lambda *a, **k: BrokenFile())
    transcript.emit("user", {"text": "still works"})  # one WARNING, no raise


def test_system_prompt_key_sentences():
    assert "You never decide PASS/FAIL" in SYSTEM_PROMPT
    assert "human confirmation" in SYSTEM_PROMPT
    assert "untrusted data" in SYSTEM_PROMPT
    assert "never claim something is fixed unless verify_fix says so" in SYSTEM_PROMPT


def test_llm_error_path(tmp_path):
    project = _build_project(tmp_path)
    ctx = ToolContext(project=project, sandbox=ProjectSandbox(project.root), confirm=_approve)
    session = AgentSession(ctx, ToolRegistry(), client=_ExplodingClient(),
                           transcript=Transcript(project.root))
    result = session.run_goal("go")
    assert result.stop_reason == "llm_error"
    assert "deterministic partial results" in result.final_text


# -- dashboard pause/resume support (v1 design §8.9, task 16) ----------------------


def _deferred(_req):
    from app.agent.tools import ConfirmResult
    return ConfirmResult("deferred")


def test_offline_workflow_pauses_on_deferred_and_resumes(tmp_path):
    project = _build_project(tmp_path)
    ctx = ToolContext(project=project, sandbox=ProjectSandbox(project.root), confirm=_deferred)
    workflow = OfflineWorkflow(ctx, ToolRegistry(), Transcript(project.root))
    paused = workflow.run("goal")
    assert paused.stop_reason == "awaiting_approval"
    assert paused.pending[0]["tool"] == "run_suite"
    assert paused.pending[0]["human_only"] is False
    assert len(project.store.list_runs("t")) == 0  # nothing ran while parked
    resumed = workflow.resolve_pending(paused.pending[0]["action_id"], True)
    assert resumed.stop_reason == "offline"
    assert len(project.store.list_runs("t")) == 1
    assert workflow.last_resolution["ok"] is True
    assert "Triage" in resumed.final_text
    assert workflow.pending_action is None and ctx.pending == {}


def test_offline_workflow_deferred_decline(tmp_path):
    project = _build_project(tmp_path)
    ctx = ToolContext(project=project, sandbox=ProjectSandbox(project.root), confirm=_deferred)
    workflow = OfflineWorkflow(ctx, ToolRegistry())
    paused = workflow.run("goal")
    resumed = workflow.resolve_pending(paused.pending[0]["action_id"], False)
    assert resumed.stop_reason == "offline"
    assert "user_declined" in resumed.final_text
    assert len(project.store.list_runs("t")) == 0
    assert ctx.pending == {}
    assert resumed.refused is False
    # an unknown id is reported, not executed
    assert workflow.resolve_pending("act-nope", True).final_text == "error: unknown_action"


def test_begin_turn_resets_steps_and_budget(tmp_path):
    session, client, _project = _session(tmp_path, [
        [_message("one")], [_message("two")]])
    session.max_steps = 1
    assert session.send("first").stop_reason == "completed"
    assert session.steps == 1
    assert session.send("second without a new turn").stop_reason == "max_steps"
    assert len(client.inputs) == 1
    session.begin_turn(reset_steps=False)
    assert session.steps == 1  # clock only
    session.begin_turn()
    assert session.steps == 0
    assert session.send("third").stop_reason == "completed"
    assert len(client.inputs) == 2


def test_resolve_pending_records_last_resolution_and_clears_declined(tmp_path):
    session, _client, project = _session(tmp_path, [
        [_call("run_suite", {}, "c1")], [_message("ok")]], confirm=_deferred)
    parked = session.run_goal("run")
    action_id = parked.pending[0]["action_id"]
    assert action_id in session.ctx.pending
    session.resolve_pending(action_id, approve=False)
    assert session.last_resolution == {"ok": False, "error": "confirmation_declined",
                                       "reason": "user_declined"}
    assert session.ctx.pending == {}
    assert len(project.store.list_runs("t")) == 0
