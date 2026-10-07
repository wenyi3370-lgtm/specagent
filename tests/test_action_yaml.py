"""Structural tests for action.yml and the action self-test workflow
(v1 design §9.1, task 17).

These checks are local only: the composite action has NOT been executed on
GitHub. They pin the properties that keep it deterministic and thin.
"""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
SELFTEST = yaml.safe_load(
    (ROOT / ".github" / "workflows" / "action-selftest.yml").read_text(encoding="utf-8"))
GATE = yaml.safe_load(
    (ROOT / ".github" / "workflows" / "specagent-gate.yml").read_text(encoding="utf-8"))

STEPS = ACTION["runs"]["steps"]


def _step(step_id):
    return next(s for s in STEPS if s.get("id") == step_id)


def _run_text(steps):
    return "\n".join(s.get("run", "") for s in steps if isinstance(s, dict))


def test_action_is_composite_with_the_designed_inputs():
    assert ACTION["runs"]["using"] == "composite"
    inputs = ACTION["inputs"]
    assert set(inputs) == {"config", "fail-on", "comment", "mode", "cache",
                           "python-version", "artifact-name"}
    assert inputs["config"]["default"] == "specagent.yaml"
    assert inputs["fail-on"]["default"] == ""
    assert inputs["comment"]["default"] == "false"
    assert inputs["mode"]["default"] == "auto"
    assert inputs["cache"]["default"] == "true"
    assert inputs["python-version"]["default"] == "3.12"
    assert inputs["artifact-name"]["default"] == "specagent-report"
    assert all(spec.get("description") for spec in inputs.values())


def test_every_run_step_declares_a_shell():
    run_steps = [s for s in STEPS if "run" in s]
    assert run_steps
    assert all(s.get("shell") for s in run_steps)


def test_action_never_invokes_the_agent_or_draft():
    text = _run_text(STEPS)
    assert "specagent agent" not in text
    assert "specagent draft" not in text
    assert "app.agent" not in text and "app/agent" not in text
    assert "specagent run" in text


def test_run_step_blanks_the_llm_key():
    env = _step("run")["env"]
    assert "OPENAI_API_KEY" in env and env["OPENAI_API_KEY"] == ""


def test_steps_follow_the_designed_order():
    uses = [s.get("uses", "") for s in STEPS]
    assert uses[0].startswith("actions/setup-python@")
    names = [s.get("id") or s.get("name") for s in STEPS]
    assert names.index("mode") < names.index("run") < names.index("report")
    assert any(u.startswith("actions/cache/restore@v4") for u in uses)
    assert any(u.startswith("actions/cache/save@v4") for u in uses)
    assert any(u.startswith("actions/upload-artifact@v4") for u in uses)
    assert "pip install" in STEPS[1]["run"] and "GITHUB_ACTION_PATH" in STEPS[1]["run"]


def test_cache_restore_uses_the_designed_keys():
    restore = next(s for s in STEPS if s.get("uses", "").startswith("actions/cache/restore"))
    assert restore["with"]["path"] == ".specagent-ci/specagent.db"
    assert restore["with"]["key"] == "specagent-db-${{ runner.os }}-${{ github.sha }}"
    assert restore["with"]["restore-keys"].strip() == "specagent-db-${{ runner.os }}-"
    assert "inputs.cache == 'true'" in restore["if"]
    save = next(s for s in STEPS if s.get("uses", "").startswith("actions/cache/save"))
    assert "always()" in save["if"] and "baseline" in save["if"]


def test_run_step_handles_modes_and_exit_codes():
    script = _step("run")["run"]
    assert "--set-baseline" in script and "--json > result.json" in script
    assert "--fail-on" in script
    assert "-eq 2" in script and "exit 2" in script           # config error propagates
    assert "::warning::" in script                            # baseline exit 1 is a warning
    assert ".specagent-ci/specagent.db" in script
    assert "code=" in script and "GITHUB_OUTPUT" in script


def test_user_inputs_reach_shell_only_through_env():
    # No `${{ inputs.* }}` / event data interpolated inside run: scripts (injection).
    for step in STEPS:
        assert "${{" not in step.get("run", ""), step.get("name")


def test_upload_and_final_gate_steps():
    upload = next(s for s in STEPS if s.get("uses", "").startswith("actions/upload-artifact"))
    assert upload["if"] == "always()"
    assert upload["with"]["name"] == "${{ inputs.artifact-name }}"
    final = STEPS[-1]
    assert final["if"] == "steps.run.outputs.code == '1'"
    assert "exit 1" in final["run"]


def test_pr_comment_step_is_opt_in_and_non_blocking():
    step = next(s for s in STEPS if "gh pr comment" in s.get("run", ""))
    assert step["continue-on-error"] is True
    assert "inputs.comment == 'true'" in step["if"]
    assert "pull_request" in step["if"]
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert "--edit-last" in step["run"] and "--create-if-none" in step["run"]


def test_helpers_are_run_by_script_path_and_exist():
    text = _run_text(STEPS)
    assert '"$GITHUB_ACTION_PATH/app/ci.py"' in text
    assert (ROOT / "app" / "ci.py").is_file()
    for sub in ("mode", "run-id", "comment"):
        assert f"ci.py\" {sub}" in text


def test_selftest_runs_both_modes_and_asserts_the_block():
    jobs = SELFTEST["jobs"]
    assert set(jobs) == {"selftest"}
    trigger = SELFTEST.get("on", SELFTEST.get(True))
    assert {"push", "pull_request", "workflow_dispatch"} <= set(trigger)
    steps = jobs["selftest"]["steps"]
    local = [s for s in steps if s.get("uses") == "./"]
    assert len(local) == 2
    baseline, candidate = local
    assert baseline["with"]["mode"] == "baseline" and baseline["with"]["cache"] == "false"
    assert candidate["with"]["mode"] == "candidate" and candidate["with"]["cache"] == "false"
    assert candidate["id"] == "blocked" and candidate["continue-on-error"] is True
    assert baseline["with"]["artifact-name"] != candidate["with"]["artifact-name"]
    assert any("steps.blocked.outcome" in str(s.get("if", "")) and "'failure'" in str(s.get("if", ""))
               for s in steps)
    assert steps.index(baseline) < steps.index(candidate)


def test_every_referenced_config_path_exists():
    steps = SELFTEST["jobs"]["selftest"]["steps"]
    configs = [s["with"]["config"] for s in steps if s.get("uses") == "./"]
    assert configs == ["examples/fincare-agent/specagent.baseline.yaml",
                       "examples/fincare-agent/specagent.yaml"]
    for config in configs:
        assert (ROOT / config).is_file(), config
    assert (ROOT / ACTION["inputs"]["config"]["default"]).name == "specagent.yaml"


def test_workflows_do_not_use_the_agent():
    for wf in (SELFTEST, GATE):
        text = _run_text([s for job in wf["jobs"].values() for s in job["steps"]])
        assert not re.search(r"specagent (agent|draft)|app\.agent", text)
