"""app.ci helpers and `specagent run --fail-on` (v1 design §9.1, task 17).

The comment renderer is exercised on real `run --json` payloads produced by a
FinCare baseline + candidate run, not on hand-written dictionaries.
"""
import ast
import contextlib
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app import ci
from cli.specagent import EXIT_CONFIG_ERROR, EXIT_GATE_FAILED, EXIT_OK, main

REPO_ROOT = Path(__file__).resolve().parents[1]


# -- mode matrix ----------------------------------------------------------------


@pytest.mark.parametrize("event,ref,override,expected", [
    ("push", "refs/heads/main", "auto", "baseline"),
    ("push", "refs/heads/feature", "auto", "candidate"),
    ("push", "refs/tags/main", "auto", "candidate"),
    ("pull_request", "refs/pull/7/merge", "auto", "candidate"),
    ("pull_request", "refs/heads/main", "auto", "candidate"),
    ("workflow_dispatch", "refs/heads/main", "auto", "candidate"),
    ("push", "refs/heads/main", "candidate", "candidate"),
    ("pull_request", "refs/pull/7/merge", "baseline", "baseline"),
    ("workflow_dispatch", "", "baseline", "baseline"),
])
def test_mode_matrix(event, ref, override, expected):
    assert ci.decide_mode(event, ref, "main", override) == expected


def test_mode_uses_the_given_default_branch():
    assert ci.decide_mode("push", "refs/heads/trunk", "trunk") == "baseline"
    assert ci.decide_mode("push", "refs/heads/main", "trunk") == "candidate"


def test_mode_rejects_unknown_override():
    with pytest.raises(ValueError):
        ci.decide_mode("push", "refs/heads/main", "main", "both")
    assert ci.main(["mode", "--event", "push", "--override", "both"]) == 2


def test_mode_cli_prints_only_the_mode_when_run_by_script_path():
    # The action runs the helper by file path (the caller's repo may have its
    # own `app` package), so the script must work standalone.
    out = subprocess.run(
        [sys.executable, str(REPO_ROOT / "app" / "ci.py"), "mode", "--event", "push",
         "--ref", "refs/heads/main", "--default-branch", "main"],
        capture_output=True, text=True, cwd=str(REPO_ROOT / "docs"), check=True)
    assert out.stdout.strip() == "baseline"


def test_ci_module_only_imports_the_standard_library():
    tree = ast.parse((REPO_ROOT / "app" / "ci.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0 and node.module
            imported.add(node.module.split(".")[0])
    assert imported <= set(sys.stdlib_module_names), imported


# -- real FinCare payloads ------------------------------------------------------


def _run_json(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(argv)
    return code, buf.getvalue()


@pytest.fixture(scope="module")
def fincare(tmp_path_factory):
    root = tmp_path_factory.mktemp("ci-fincare")
    proj = root / "fincare-agent"
    shutil.copytree(REPO_ROOT / "examples" / "fincare-agent", proj,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    db = str(root / "ci.db")
    code, _ = _run_json(["run", "--config", str(proj / "specagent.baseline.yaml"),
                         "--set-baseline", "--db", db, "--json"])
    assert code == EXIT_OK
    return {"proj": proj, "db": db}


def _candidate(fincare, *extra):
    return _run_json(["run", "--config", str(fincare["proj"] / "specagent.yaml"),
                      "--db", fincare["db"], "--json", *extra])


@pytest.fixture(scope="module")
def candidate_payload(fincare):
    code, out = _candidate(fincare)
    assert code == EXIT_GATE_FAILED
    return ci.load_result(out), out


def test_comment_from_real_failing_payload(candidate_payload):
    payload, _ = candidate_payload
    text = ci.render_comment(payload)
    diff = payload["diff"]
    assert "**Gate: FAILED**" in text
    assert f"| {diff['new_regressions']} | {diff['fixed']} |" in text
    assert "### Top new regressions" in text
    regs = [e for e in diff["entries"] if e["diff_type"] == "NEW_REGRESSION"]
    assert regs and all(e["test_case_id"] in text or len(regs) > ci.MAX_COMMENT_ENTRIES
                        for e in regs)
    assert "LARGE_TRANSFER_APPROVAL" in text or "ACCOUNT_SCOPE" in text
    assert payload["run"]["id"] in text
    # severity ordering: a critical entry is listed before any lower one
    severities = [line.split("**")[1] for line in text.splitlines() if line.startswith("- **")]
    order = [ci._SEVERITY_ORDER[s] for s in severities]
    assert order == sorted(order)


def test_comment_from_real_passing_payload(fincare):
    code, out = _run_json(["run", "--config", str(fincare["proj"] / "specagent.baseline.yaml"),
                           "--baseline", "last", "--db", fincare["db"], "--json"])
    assert code == EXIT_OK
    text = ci.render_comment(ci.load_result(out))
    assert "**Gate: PASSED**" in text
    assert "Top new regressions" not in text


def test_comment_first_run_has_no_diff(tmp_path):
    proj = tmp_path / "fincare-agent"
    shutil.copytree(REPO_ROOT / "examples" / "fincare-agent", proj,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    code, out = _run_json(["run", "--config", str(proj / "specagent.baseline.yaml"),
                           "--db", str(tmp_path / "first.db"), "--json"])
    assert code == EXIT_OK
    assert "no baseline to diff" in ci.render_comment(ci.load_result(out))


def test_comment_neutralizes_agent_controlled_text():
    payload = {"run": {"id": "run_1"}, "diff": {
        "new_regressions": 1, "entries": [{
            "diff_type": "NEW_REGRESSION", "severity": "high", "test_case_id": "T`1",
            "rule_id": "R", "violations": ["ping @everyone <img src=x>\n```\nboom"]}]},
        "gate": {"violations": [{}], "fail_on": ["high"], "exit_code": 1}}
    text = ci.render_comment(payload)
    assert "@everyone" not in text and "<img" not in text and "```" not in text


def test_comment_cli_round_trip(candidate_payload, tmp_path):
    _, raw = candidate_payload
    result, out = tmp_path / "result.json", tmp_path / "comment.md"
    result.write_text(raw, encoding="utf-8")
    assert ci.main(["comment", "--result", str(result), "--out", str(out)]) == 0
    assert "Gate: FAILED" in out.read_text(encoding="utf-8")
    assert ci.main(["comment", "--result", str(tmp_path / "missing.json"),
                    "--out", str(out)]) == 2


def test_load_result_tolerates_leading_warning_lines(candidate_payload):
    payload, raw = candidate_payload
    assert ci.load_result("warning: something\n" + raw) == payload
    with pytest.raises(ValueError):
        ci.load_result("no json here")


def test_run_id_from_json_and_plain_output(candidate_payload):
    payload, raw = candidate_payload
    assert ci.extract_run_id(raw) == payload["run"]["id"]
    assert ci.extract_run_id("Result: baseline set\nBaseline: run_x\nRun id: run_x\nDashboard: u\n") == "run_x"
    assert ci.extract_run_id("nothing") is None


# -- specagent run --fail-on ----------------------------------------------------


def test_fail_on_overrides_the_gate_for_one_run_only(fincare):
    # the candidate's regressions are critical/high: a low-only gate lets them through
    code, out = _candidate(fincare, "--fail-on", "low")
    assert code == EXIT_OK
    assert ci.load_result(out)["gate"]["fail_on"] == ["low"]
    # the config is untouched: without the flag the same run fails again
    code, out = _candidate(fincare)
    assert code == EXIT_GATE_FAILED
    assert ci.load_result(out)["gate"]["fail_on"] == ["critical", "high"]


def test_fail_on_accepts_a_list_and_normalizes(fincare):
    code, out = _candidate(fincare, "--fail-on", "Critical, high,critical")
    assert code == EXIT_GATE_FAILED
    assert ci.load_result(out)["gate"]["fail_on"] == ["critical", "high"]


@pytest.mark.parametrize("value", ["bogus", "critical,urgent", "", " , "])
def test_fail_on_rejects_invalid_severities(fincare, value):
    code, _ = _candidate(fincare, "--fail-on", value)
    assert code == EXIT_CONFIG_ERROR
