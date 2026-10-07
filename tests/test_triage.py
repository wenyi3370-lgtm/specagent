"""Unit tests: deterministic triage (v1 design §8.5, task 12)."""
import ast
import json
from pathlib import Path

import pytest

from app.agent.tools import REGISTRY, ToolRegistry, ToolContext
from app.agent.sandbox import ProjectSandbox
from app.agent.triage import triage_run
from app.regression import diff_runs
from cli.specagent import EXIT_CONFIG_ERROR, EXIT_OK, main
from test_fincare_example import _copy_example, _load, _run_dict, _suite

REPO_ROOT = Path(__file__).resolve().parents[1]


def _fincare_runs(tmp_path):
    proj = _copy_example("fincare-agent", tmp_path / "proj")
    _proj, _config, spec, fixed = _load(tmp_path, "specagent.baseline.yaml")
    _p2, _c2, _spec2, vuln = _load(tmp_path, "specagent.yaml")
    fixed_tests, fixed_results = _suite(spec, fixed)
    vuln_tests, vuln_results = _suite(spec, vuln)
    baseline = _run_dict("base", spec, fixed_tests, fixed_results)
    candidate = _run_dict("cand", spec, vuln_tests, vuln_results)
    candidate["project_id"] = "fincare-agent"
    return baseline, candidate, diff_runs(baseline, candidate)


def test_fincare_vulnerable_categories(tmp_path):
    _baseline, candidate, diff = _fincare_runs(tmp_path)
    report = triage_run(candidate, diff)
    assert report["summary"].startswith("2 rules failing")
    by_rule = {r["rule_id"]: r for r in report["rules"]}
    large = by_rule["LARGE_TRANSFER_APPROVAL"]
    assert {f["category"] for f in large["findings"]} >= {"missing_approval", "approval_denied"}
    scope = by_rule["ACCOUNT_SCOPE"]
    scope_findings = [f for f in scope["findings"] if f["category"] == "scope_violation"]
    assert scope_findings and all(f["arg"] in ("account_id", "from_account")
                                  for f in scope_findings)
    # diff attached per rule
    assert set(large["diff"]) == {"NEW_REGRESSION"}


def test_findings_merged_by_category_tool_arg():
    violation = ("[scope_violation] lookup_balance(account_id=ACC-2) "
                 "account_id=ACC-2 does not match actor.account_id=ACC-1 [evidence: evt_1]")
    run = {
        "id": "r", "project_id": "p",
        "spec": {"rules": [{"id": "R", "title": "t", "severity": "critical"}]},
        "tests": [{"id": "R-01", "rule_id": "R"}, {"id": "R-02", "rule_id": "R"}],
        "results": [
            {"test_case_id": "R-01", "rule_id": "R", "status": "FAIL",
             "violations": [violation], "trace": []},
            {"test_case_id": "R-02", "rule_id": "R", "status": "FLAKY",
             "violations": [violation], "trace": []},
        ],
    }
    report = triage_run(run)
    (rule,) = report["rules"]
    (finding,) = rule["findings"]
    assert finding["count"] == 2
    assert finding["cases"] == ["R-01", "R-02"]
    assert finding["evidence_event_ids"] == ["evt_1"]


def test_all_pass_summary():
    run = {"id": "r", "project_id": "p", "spec": {"rules": []}, "tests": [],
           "results": [{"test_case_id": "A-01", "rule_id": "A", "status": "PASS",
                        "violations": [], "trace": []}]}
    report = triage_run(run)
    assert report["summary"] == "All 1 cases passed; nothing to triage."
    assert report["rules"] == [] and report["errors"] == []


def test_errors_are_separate_never_merged():
    run = {"id": "r", "project_id": "p",
           "spec": {"rules": [{"id": "R", "title": "t", "severity": "high"}]},
           "tests": [{"id": "R-01", "rule_id": "R"}],
           "results": [
               {"test_case_id": "R-01", "rule_id": "R", "status": "ERROR",
                "violations": [], "response": "boom", "trace": []},
               {"test_case_id": "C-99", "rule_id": "C", "status": "CANCELED",
                "violations": [], "trace": []},
           ]}
    report = triage_run(run)
    assert report["counts"] == {"pass": 0, "fail": 0, "flaky": 0, "error": 1, "canceled": 1}
    assert report["rules"] == []  # ERROR is not a behavior violation
    assert report["errors"] == [{"case": "R-01", "rule_id": "R", "message": "boom"}]


def test_hostile_violation_text_does_not_crash():
    for text in ("[unclosed [bracket", "[]", "\x00\n]]", "[not_a_kind] x(y) z",
                 "[scope_violation] t(a=b) " + "x" * 5000):
        run = {"id": "r", "project_id": "p",
               "spec": {"rules": [{"id": "R", "title": "t", "severity": "low"}]},
               "tests": [{"id": "R-01", "rule_id": "R"}],
               "results": [{"test_case_id": "R-01", "rule_id": "R", "status": "FAIL",
                            "violations": [text], "trace": []}]}
        report = triage_run(run)  # must not raise
        assert report["rules"][0]["findings"][0]["example_violation"] == text


def test_legacy_violation_falls_back_to_trace_tool_calls():
    run = {"id": "r", "project_id": "p",
           "spec": {"rules": [{"id": "R", "title": "t", "severity": "high"}]},
           "tests": [{"id": "R-01", "rule_id": "R"}],
           "results": [{"test_case_id": "R-01", "rule_id": "R", "status": "FAIL",
                        "violations": ["Missing required call: request_human_approval"],
                        "trace": [{"type": "tool_call", "name": "request_human_approval",
                                   "id": "evt_1"},
                                  {"type": "tool_call", "name": "refund", "id": "evt_2"}]}]}
    (finding,) = triage_run(run)["rules"][0]["findings"]
    assert finding["category"] == "missing_required_call"
    assert finding["evidence_event_ids"] == ["evt_1"]  # events named the parsed tool


def test_triage_module_imports_only_violations():
    tree = ast.parse((REPO_ROOT / "app" / "agent" / "triage.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            assert node.module == "app.violations", node.module
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("app."), alias.name


def test_triage_tool_registered():
    spec = REGISTRY["triage_run"]
    assert spec.risk == "auto" and spec.human_only is False and spec.effects == {"read"}


def test_cli_triage_json_and_exit_codes(tmp_path, capsys):
    proj = _copy_example("fincare-agent", tmp_path / "proj")
    db = str(tmp_path / "t.db")
    assert main(["run", "--config", str(proj / "specagent.baseline.yaml"),
                 "--set-baseline", "--db", db]) == EXIT_OK
    capsys.readouterr()
    assert main(["run", "--config", str(proj / "specagent.yaml"), "--db", db]) == 1
    capsys.readouterr()
    assert main(["triage", "--db", db, "--project", "fincare-agent", "--json"]) == EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert set(report) == {"run_id", "project_id", "counts", "summary", "rules", "errors"}
    assert report["summary"].startswith("2 rules failing")
    # readable output mentions hints and examples
    assert main(["triage", "--db", db, "--project", "fincare-agent"]) == EXIT_OK
    text = capsys.readouterr().out
    assert "hint:" in text and "e.g." in text
    # no run for the project → exit 2
    assert main(["triage", "--db", db, "--project", "no-such-project"]) == EXIT_CONFIG_ERROR
