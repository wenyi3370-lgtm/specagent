"""Unit tests: CLI `agent` and `draft` + compiler injection seam
(v1 design §8.7, task 14) — scripted clients, monkeypatched TTY."""
import argparse
import json
from pathlib import Path

import pytest

import cli.specagent as cli
from app.errors import SpecValidationError
from app.llm_client import resolve_model
from app.spec_yaml import load_spec_file
from cli.specagent import (EXIT_AGENT_STOPPED, EXIT_CONFIG_ERROR, EXIT_OK, main)
from test_agent_tools import CONFIG, SPEC, _build_project

REPO_ROOT = Path(__file__).resolve().parents[1]


def _args(yes=False):
    return argparse.Namespace(yes=yes)


# -- the §8.7 confirmation callback table (8 rows) --------------------------------


def _request(tool="run_suite", human_only=False):
    from app.agent.tools import ConfirmRequest
    return ConfirmRequest(tool=tool, summary="s", preview="p", args={}, call_id="c",
                          human_only=human_only)


def _row(monkeypatch, capsys, *, yes, tty, human_only, prompt_answer="y"):
    confirm = cli._make_confirm(_args(yes=yes))
    monkeypatch.setattr(cli, "_is_interactive", lambda: tty)
    answers = iter([prompt_answer] * 5)
    monkeypatch.setattr(cli, "_prompt", lambda p: next(answers))
    return confirm(_request(human_only=human_only))


def test_table_yes_non_human_tool_auto_approves(monkeypatch, capsys):
    confirm = cli._make_confirm(_args(yes=True))
    monkeypatch.setattr(cli, "_is_interactive", lambda: False)
    decision = confirm(_request(human_only=False))
    assert decision.decision == "approved" and decision.auto is True
    assert "auto-approved" in capsys.readouterr().out  # banner printed once
    decision = confirm(_request(human_only=False))
    assert decision.decision == "approved" and decision.auto is True
    assert "auto-approved" not in capsys.readouterr().out  # only once


def test_table_yes_human_tool_with_tty_prompts(monkeypatch, capsys):
    decision = _row(monkeypatch, capsys, yes=True, tty=True, human_only=True)
    assert decision.decision == "approved" and decision.auto is False
    out = capsys.readouterr().out
    assert "--yes does not apply" in out


def test_table_yes_human_tool_without_tty_is_refused(monkeypatch, capsys):
    decision = _row(monkeypatch, capsys, yes=True, tty=False, human_only=True)
    assert decision.decision == "declined" and decision.reason == "human_required"


def test_table_tty_yes_approves(monkeypatch, capsys):
    decision = _row(monkeypatch, capsys, yes=False, tty=True, human_only=False,
                    prompt_answer="y")
    assert decision.decision == "approved"


def test_table_tty_no_declines(monkeypatch, capsys):
    decision = _row(monkeypatch, capsys, yes=False, tty=True, human_only=False,
                    prompt_answer="n")
    assert decision.decision == "declined" and decision.reason == "user_declined"


def test_table_non_tty_declines_non_interactive(monkeypatch, capsys):
    decision = _row(monkeypatch, capsys, yes=False, tty=False, human_only=False)
    assert decision.decision == "declined" and decision.reason == "non_interactive"
    assert "re-run with --yes" in capsys.readouterr().out


def test_table_human_tool_tty_prompts(monkeypatch, capsys):
    decision = _row(monkeypatch, capsys, yes=False, tty=True, human_only=True,
                    prompt_answer="y")
    assert decision.decision == "approved"


def test_table_human_tool_non_tty_refused(monkeypatch, capsys):
    decision = _row(monkeypatch, capsys, yes=False, tty=False, human_only=True)
    assert decision.decision == "declined" and decision.reason == "human_required"


def test_yes_run_suite_executes_e2e(tmp_path, monkeypatch, capsys):
    """--yes at CLI level: run_suite runs; the spec and the baseline are
    untouched (run_suite never sets a baseline)."""
    project = _build_project(tmp_path)
    monkeypatch.setattr(cli, "_client_factory", lambda: None)
    assert main(["agent", "run the suite", "--yes",
                 "--config", str(tmp_path / "specagent.yaml"),
                 "--db", str(tmp_path / "t.db")]) == EXIT_OK
    assert len(project.store.list_runs("t")) == 1
    assert project.store.get_baseline("t") is None


# -- one-shot / REPL / limits -------------------------------------------------------


class _FakeItem:
    def __init__(self, type, **kw):
        self.type = type
        self.__dict__.update(kw)

    def model_dump(self):
        return dict(self.__dict__)


class _FakeResponse:
    def __init__(self, output):
        self.output = output


class _FakeAgentClient:
    def __init__(self, turns):
        self._turns = list(turns)
        self.models = []
        self.responses = self

    def create(self, **kwargs):
        self.models.append(kwargs["model"])
        return _FakeResponse(self._turns.pop(0))


def _call(name, arguments, call_id):
    return _FakeItem("function_call", name=name,
                     arguments=json.dumps(arguments), call_id=call_id)


def _message(text):
    return _FakeItem("message", content=[{"type": "output_text", "text": text}])


def test_one_shot_with_scripted_client(tmp_path, monkeypatch, capsys):
    _build_project(tmp_path)
    client = _FakeAgentClient([[_call("run_suite", {}, "c1")],
                               [_message("the suite passed")]])
    monkeypatch.setattr(cli, "_client_factory", lambda: client)
    assert main(["agent", "run the suite", "--yes",
                 "--config", str(tmp_path / "specagent.yaml"),
                 "--db", str(tmp_path / "t.db")]) == EXIT_OK
    out = capsys.readouterr().out
    assert "the suite passed" in out
    assert "Deterministic results (verbatim)" in out
    assert client.models == [resolve_model()] * 2  # tool turn + summary turn


def test_repl_with_simulated_inputs(tmp_path, monkeypatch, capsys):
    _build_project(tmp_path)
    client = _FakeAgentClient([[_message("I can inspect and run tests.")]])
    monkeypatch.setattr(cli, "_client_factory", lambda: client)
    monkeypatch.setattr(cli, "_is_interactive", lambda: True)
    answers = iter(["what can you do?", "/exit"])
    monkeypatch.setattr(cli, "_prompt", lambda p: next(answers))
    assert main(["agent", "--config", str(tmp_path / "specagent.yaml"),
                 "--db", str(tmp_path / "t.db")]) == EXIT_OK
    assert "I can inspect and run tests." in capsys.readouterr().out


def test_non_tty_without_goal_is_config_error(tmp_path, monkeypatch):
    _build_project(tmp_path)
    monkeypatch.setattr(cli, "_client_factory", lambda: object())
    monkeypatch.setattr(cli, "_is_interactive", lambda: False)
    assert main(["agent", "--config", str(tmp_path / "specagent.yaml")]) == EXIT_CONFIG_ERROR


def test_non_tty_refused_confirmation_exits_4(tmp_path, monkeypatch, capsys):
    _build_project(tmp_path)
    monkeypatch.setattr(cli, "_client_factory", lambda: None)  # offline path
    runs_before = 0
    assert main(["agent", "run", "--config", str(tmp_path / "specagent.yaml"),
                 "--db", str(tmp_path / "t.db")]) == EXIT_AGENT_STOPPED
    out = capsys.readouterr().out
    assert "stopped: confirmation refused" in out
    project = _build_project(tmp_path)  # same db, run count unchanged
    assert len(project.store.list_runs("t")) == runs_before


def test_max_steps_honored(tmp_path, monkeypatch, capsys):
    _build_project(tmp_path)
    client = _FakeAgentClient([[_call("get_metrics", {}, "c1")],
                               [_call("get_metrics", {}, "c2")]])
    monkeypatch.setattr(cli, "_client_factory", lambda: client)
    assert main(["agent", "loop", "--yes", "--max-steps", "2",
                 "--config", str(tmp_path / "specagent.yaml"),
                 "--db", str(tmp_path / "t.db")]) == EXIT_AGENT_STOPPED
    assert "stopped: max_steps" in capsys.readouterr().out
    assert len(client.models) == 2


def test_offline_banner_and_completion(tmp_path, monkeypatch, capsys):
    project = _build_project(tmp_path)
    monkeypatch.setattr(cli, "_client_factory", lambda: None)
    assert main(["agent", "fix everything", "--yes",
                 "--config", str(tmp_path / "specagent.yaml"),
                 "--db", str(tmp_path / "t.db")]) == EXIT_OK
    out = capsys.readouterr().out
    assert "No OPENAI_API_KEY: running the fixed deterministic workflow" in out
    assert "the goal text is not interpreted" in out
    assert len(project.store.list_runs("t")) == 1  # --yes auto-approved run_suite


# -- draft ----------------------------------------------------------------------------


def test_draft_deterministic_fallback_round_trip(tmp_path, capsys):
    out = tmp_path / "draft.yaml"
    assert main(["draft", "退款超过800元需要人工审批，不得删除用户账户。",
                 "--out", str(out)]) == EXIT_OK
    text = capsys.readouterr().out
    assert "deterministic compiler" in text
    spec = load_spec_file(str(out))  # what was written loads back
    assert "LARGE_REFUND_APPROVAL" in [r.id for r in spec.rules]


def test_draft_refuses_existing_out_without_force(tmp_path, capsys):
    out = tmp_path / "draft.yaml"
    out.write_text("keep me\n", encoding="utf-8")
    assert main(["draft", "退款需要人工审批", "--out", str(out)]) == EXIT_CONFIG_ERROR
    assert out.read_text(encoding="utf-8") == "keep me\n"
    assert main(["draft", "退款需要人工审批", "--out", str(out), "--force"]) == EXIT_OK
    assert out.read_text(encoding="utf-8") != "keep me\n"


def test_draft_with_injected_fake_client(tmp_path, monkeypatch, capsys):
    class _FakeCompileClient:
        def __init__(self):
            self.models = []
            self.responses = self

        def create(self, **kwargs):
            self.models.append(kwargs["model"])
            spec = {
                "agent_name": "Compiled",
                "locale": "en",
                "rules": [{
                    "id": "TRANSFER_LIMIT", "title": "t", "action": "transfer",
                    "severity": "critical",
                    "constraints": [{"type": "arg_range", "tool": "transfer",
                                     "arg": "amount", "max": 10000}],
                    "probes": [{"template": "transfer {amount}",
                                "actor": {"account_id": "ACC-1"}}],
                }],
            }
            return type("R", (), {"output_text": json.dumps(spec)})()

    fake = _FakeCompileClient()
    monkeypatch.setattr(cli, "_client_factory", lambda: fake)
    out = tmp_path / "compiled.yaml"
    assert main(["draft", "转账不得超过一万元", "--out", str(out)]) == EXIT_OK
    spec = load_spec_file(str(out))
    rule = spec.rules[0]
    assert rule.constraints[0].type == "arg_range"
    assert rule.probes[0].template == "transfer {amount}"
    assert fake.models == [resolve_model()]  # SPECAGENT_AGENT_MODEL honored
    assert "compiler: LLM" in capsys.readouterr().out


def test_compile_seam_defaults_unchanged():
    from app.compiler import compile_demo, compile_spec
    assert compile_spec("退款超过800元需要人工审批。").compiler == "deterministic-demo"
    demo = compile_demo("退款")
    assert demo.rules  # existing behavior intact


def test_compile_with_llm_uses_injected_client():
    from app.compiler import compile_with_llm

    class _R:
        output_text = json.dumps({
            "agent_name": "A",
            "rules": [{"id": "R1", "title": "t", "action": "refund",
                       "severity": "high"}],
        })

    class _C:
        def __init__(self):
            self.models = []
            self.responses = self

        def create(self, **kwargs):
            self.models.append(kwargs["model"])
            return _R()

    fake = _C()
    spec = compile_with_llm("退款需要人工审批", client=fake, model="test-model")
    assert spec.compiler == "openai:test-model"
    assert spec.rules[0].id == "R1"
    assert fake.models == ["test-model"]
