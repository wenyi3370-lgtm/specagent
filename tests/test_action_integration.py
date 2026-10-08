"""Evidence checks must reject a green job that did not exercise the gate."""
import contextlib
import importlib.util
import io
import json
import shutil
from pathlib import Path

import pytest
import yaml

from app.ci import load_result
from cli.specagent import main as cli_main

ROOT = Path(__file__).resolve().parents[1]
MODULE = importlib.util.spec_from_file_location("action_integration_check", ROOT / "scripts/check_action_integration.py")
checker = importlib.util.module_from_spec(MODULE)
MODULE.loader.exec_module(checker)


@pytest.fixture(scope="module")
def candidates(tmp_path_factory):
    root = tmp_path_factory.mktemp("action-integration")
    project = root / "fincare-agent"
    shutil.copytree(ROOT / "examples/fincare-agent", project,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    db = root / "baseline.db"

    def run(config, *extra):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli_main(["run", "--config", str(project / config), "--db", str(db), "--json", *extra])
        return code, load_result(output.getvalue())

    assert run("specagent.baseline.yaml", "--set-baseline")[0] == 0
    blocked = run("specagent.yaml")
    assert blocked[0] == 1
    db = root / "first-run.db"
    first = run("specagent.yaml")
    assert first[0] == 0
    return blocked[1], first[1]


def test_real_gate_survives_readonly_comment_failure(candidates):
    evidence = checker.check("readonly", candidates[0], "1", "failure", comment_status="unavailable")
    assert evidence["status"] == "verified" and evidence["new_regressions"] == 4


@pytest.mark.parametrize("gate_exit,outcome,status", [
    ("", "failure", "unavailable"), ("2", "failure", "unavailable"),
    ("1", "success", "unavailable"), ("0", "success", "unavailable"),
    ("1", "failure", ""), ("1", "failure", "posted"),
])
def test_unexercised_or_mismatched_permission_evidence_is_rejected(candidates, gate_exit, outcome, status):
    with pytest.raises(ValueError):
        checker.check("readonly", candidates[0], gate_exit, outcome, comment_status=status)


def test_restored_cache_requires_actual_new_regressions(candidates):
    assert checker.check("cache", candidates[0], "1", "failure", cache_key="specagent-db-Linux-old")["status"] == "verified"
    with pytest.raises(ValueError):
        checker.check("cache", candidates[1], "0", "success", cache_key="specagent-db-Linux-old")


def test_cache_miss_is_pending_never_a_verified_cache(candidates):
    assert checker.check("cache", candidates[1], "0", "success")["status"] == "pending-main-baseline"
    with pytest.raises(ValueError):
        checker.check("cache", candidates[0], "1", "failure")


def test_cache_miss_after_the_base_contains_the_workflow_is_a_failure(candidates):
    with pytest.raises(ValueError, match="no baseline cache was restored"):
        checker.check("cache", candidates[1], "0", "success", require_cache=True)


def test_missing_result_does_not_create_passing_evidence(tmp_path, monkeypatch):
    output = tmp_path / "evidence.json"
    assert checker.main(["readonly", "--result", str(tmp_path / "missing.json"), "--out", str(output)]) == 1
    assert not output.exists()


def test_cli_evidence_reports_the_real_ref_and_status(tmp_path, monkeypatch, candidates):
    result, output = tmp_path / "result.json", tmp_path / "evidence.json"
    result.write_text(json.dumps(candidates[0]), encoding="utf-8")
    for key, value in {"SA_GATE_EXIT": "1", "SA_ACTION_OUTCOME": "failure",
                       "SA_COMMENT_STATUS": "unavailable", "GITHUB_REF": "refs/pull/24/merge",
                       "GITHUB_EVENT_NAME": "pull_request"}.items():
        monkeypatch.setenv(key, value)
    assert checker.main(["readonly", "--result", str(result), "--out", str(output)]) == 0
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["ref"] == "refs/pull/24/merge" and data["status"] == "verified"


def test_integration_does_not_elevate_pr_code_or_token_permissions():
    workflow = yaml.safe_load((ROOT / ".github/workflows/action-integration.yml").read_text(encoding="utf-8"))
    trigger = workflow.get("on", workflow.get(True))
    assert "pull_request_target" not in trigger
    assert workflow["permissions"]["pull-requests"] == "read"
    readonly = workflow["jobs"]["readonly-comment"]
    assert readonly["permissions"]["pull-requests"] == "read"
    assert all("secrets." not in str(step) for step in readonly["steps"])
    producer = workflow["jobs"]["baseline-cache"]
    assert "repository.default_branch" in producer["if"]
    action = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
    assert action["outputs"]["gate-exit-code"]["value"] == "${{ steps.run.outputs.code }}"
    assert action["outputs"]["comment-status"]["value"] == "${{ steps.comment.outputs.status }}"
