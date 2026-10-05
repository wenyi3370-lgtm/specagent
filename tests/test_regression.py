"""Unit tests: Regression Diff classification (roadmap §5.3) + gate policy (§6.4)."""
from app.regression import classify, diff_runs, gate_violations


def test_classify_covers_the_four_core_classes():
    assert classify("PASS", "FAIL") == "NEW_REGRESSION"
    assert classify("FAIL", "PASS") == "FIXED"
    assert classify("FAIL", "FAIL") == "PERSISTENT_FAIL"
    assert classify("PASS", "PASS") == "STABLE_PASS"


def test_classify_edges():
    assert classify(None, "PASS") == "NEW_TEST"
    assert classify(None, "FAIL") == "NEW_TEST"
    assert classify("PASS", "FLAKY") == "FLAKY"
    assert classify("PASS", "ERROR") == "NEW_ERROR"
    assert classify("FAIL", "ERROR") == "PERSISTENT_FAIL"


def _run(run_id, results, rules, tests):
    return {
        "id": run_id,
        "spec": {"rules": rules},
        "tests": tests,
        "results": results,
    }


def _result(tc_id, status, violations=None):
    return {"test_case_id": tc_id, "status": status, "violations": violations or [], "trace": []}


def _test(tc_id, rule_id):
    return {"id": tc_id, "rule_id": rule_id, "user_input": f"input {tc_id}", "expected_calls": []}


RULES = [
    {"id": "LARGE_REFUND_APPROVAL", "severity": "critical"},
    {"id": "ADDRESS_CONFIRM", "severity": "high"},
]
TESTS = [_test("LARGE_REFUND_APPROVAL-04", "LARGE_REFUND_APPROVAL"),
         _test("ADDRESS_CONFIRM-03", "ADDRESS_CONFIRM")]


def test_diff_counts_and_severity_lookup():
    baseline = _run("run_b", [
        _result("LARGE_REFUND_APPROVAL-04", "PASS"),
        _result("ADDRESS_CONFIRM-03", "PASS"),
    ], RULES, TESTS)
    candidate = _run("run_c", [
        _result("LARGE_REFUND_APPROVAL-04", "FAIL", ["refund executed before human approval"]),
        _result("ADDRESS_CONFIRM-03", "PASS"),
    ], RULES, TESTS)

    diff = diff_runs(baseline, candidate)
    assert diff.new_regressions == 1 and diff.stable_pass == 1
    assert diff.baseline_run_id == "run_b" and diff.candidate_run_id == "run_c"
    regression = diff.entries[0]
    assert regression.diff_type == "NEW_REGRESSION"
    assert regression.severity == "critical"
    assert regression.input == "input LARGE_REFUND_APPROVAL-04"
    assert regression.violations == ["refund executed before human approval"]


def test_diff_new_tests_and_fixed():
    baseline = _run("run_b", [_result("LARGE_REFUND_APPROVAL-04", "FAIL")], RULES,
                    [TESTS[0]])
    candidate = _run("run_c", [
        _result("LARGE_REFUND_APPROVAL-04", "PASS"),
        _result("ADDRESS_CONFIRM-03", "FAIL"),
    ], RULES, TESTS)
    diff = diff_runs(baseline, candidate)
    assert diff.fixed == 1 and diff.new_tests == 1


def test_new_regressions_sort_before_fixed_and_critical_first():
    baseline = _run("run_b", [
        _result("LARGE_REFUND_APPROVAL-04", "PASS"),
        _result("ADDRESS_CONFIRM-03", "PASS"),
    ], RULES, TESTS)
    candidate = _run("run_c", [
        _result("ADDRESS_CONFIRM-03", "FAIL"),
        _result("LARGE_REFUND_APPROVAL-04", "FAIL"),
    ], RULES, TESTS)
    diff = diff_runs(baseline, candidate)
    types = [e.diff_type for e in diff.entries]
    assert types == ["NEW_REGRESSION", "NEW_REGRESSION"]
    assert diff.entries[0].severity == "critical"  # critical pinned first


def test_gate_blocks_critical_and_high_new_regressions_only():
    baseline = _run("run_b", [
        _result("LARGE_REFUND_APPROVAL-04", "PASS"),
        _result("ADDRESS_CONFIRM-03", "PASS"),
    ], RULES, TESTS)
    candidate = _run("run_c", [
        _result("LARGE_REFUND_APPROVAL-04", "FAIL"),
        _result("ADDRESS_CONFIRM-03", "FAIL"),
    ], RULES, TESTS)
    diff = diff_runs(baseline, candidate)
    assert len(gate_violations(diff, ["critical", "high"])) == 2
    assert len(gate_violations(diff, ["critical"])) == 1

    # PERSISTENT_FAIL never blocks — but the high NEW_REGRESSION on the second
    # test still does, at exactly the configured severities.
    baseline2 = _run("run_b", [
        _result("LARGE_REFUND_APPROVAL-04", "FAIL"),
        _result("ADDRESS_CONFIRM-03", "PASS"),
    ], RULES, TESTS)
    diff2 = diff_runs(baseline2, candidate)
    assert gate_violations(diff2, ["critical", "high"]) == [diff2.entries[0]]
    assert gate_violations(diff2, ["critical"]) == []
    assert {e.diff_type for e in diff2.entries} == {"PERSISTENT_FAIL", "NEW_REGRESSION"}
