"""Regression diff (roadmap v0.2 §5.3) and the CI gate policy (§6.4).

The core question SpecAgent answers is not "what is the score" but
"what did this change newly break" — so diff entries that matter are
surfaced first and severity-sorted.
"""
from .models import DiffEntry, DiffSummary

_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}

# Display + gate ordering: new regressions first, stable passes last.
_TYPE_RANK = {
    "NEW_REGRESSION": 0, "NEW_ERROR": 1, "FLAKY": 2, "CANCELED": 3,
    "FIXED": 4, "PERSISTENT_FAIL": 5, "NEW_TEST": 6, "STABLE_PASS": 7,
}


def classify(baseline_status: str | None, candidate_status: str) -> str:
    if baseline_status is None:
        return "NEW_TEST"
    if candidate_status == "CANCELED":
        return "CANCELED"  # no data — never treated as a regression (§10.2)
    if candidate_status == "FLAKY":
        return "FLAKY"
    passed_b = baseline_status == "PASS"
    passed_c = candidate_status == "PASS"
    if passed_b and not passed_c:
        return "NEW_ERROR" if candidate_status == "ERROR" else "NEW_REGRESSION"
    if not passed_b and passed_c:
        return "FIXED"
    if not passed_b and not passed_c:
        return "PERSISTENT_FAIL"
    return "STABLE_PASS"


def _severity_of(spec: dict, rule_id: str) -> str:
    for rule in (spec or {}).get("rules", []):
        if rule.get("id") == rule_id:
            return rule.get("severity", "medium")
    return "medium"


def diff_runs(baseline_run: dict | None, candidate_run: dict) -> DiffSummary:
    """Compare a candidate run against a baseline run (or nothing on first run).

    Both arguments are run dicts as returned by Store.get_run(): they carry
    ``results`` keyed by test_case_id, plus ``tests`` and ``spec`` snapshots.
    """
    baseline_results = {r["test_case_id"]: r for r in (baseline_run or {}).get("results", [])}
    baseline_tests = {t["id"]: t for t in (baseline_run or {}).get("tests", [])}
    candidate_tests = {t["id"]: t for t in candidate_run.get("tests", [])}
    spec = candidate_run.get("spec", {})

    summary = DiffSummary(
        baseline_run_id=(baseline_run or {}).get("id"),
        candidate_run_id=candidate_run["id"],
    )
    entries: list[DiffEntry] = []

    for result in candidate_run.get("results", []):
        tc_id = result["test_case_id"]
        candidate_status = result["status"]
        baseline = baseline_results.get(tc_id)
        baseline_status = baseline["status"] if baseline else None
        diff_type = classify(baseline_status, candidate_status)
        test = candidate_tests.get(tc_id, {})
        entry = DiffEntry(
            test_case_id=tc_id,
            rule_id=test.get("rule_id", result.get("rule_id", "")),
            severity=_severity_of(spec, test.get("rule_id", "")),
            diff_type=diff_type,
            baseline_status=baseline_status,
            candidate_status=candidate_status,
            input=test.get("user_input", ""),
            expected=list(test.get("expected_calls", [])),
            violations=list(result.get("violations", [])),
            candidate_trace=result.get("trace", []),
            baseline_trace=baseline.get("trace", []) if baseline else [],
        )
        entries.append(entry)
        if diff_type == "NEW_REGRESSION":
            summary.new_regressions += 1
        elif diff_type == "FIXED":
            summary.fixed += 1
        elif diff_type == "PERSISTENT_FAIL":
            summary.persistent_fail += 1
        elif diff_type == "STABLE_PASS":
            summary.stable_pass += 1
        elif diff_type == "NEW_TEST":
            summary.new_tests += 1
        elif diff_type == "FLAKY":
            summary.flaky += 1
        elif diff_type == "CANCELED":
            summary.canceled += 1

    entries.sort(key=lambda e: (_TYPE_RANK[e.diff_type], _SEVERITY_RANK.get(e.severity, 9), e.test_case_id))
    summary.entries = entries
    return summary


def gate_violations(diff: DiffSummary, fail_on: list[str] | None = None) -> list[DiffEntry]:
    """Entries that must fail CI (roadmap §6.4): new regressions at or above the
    configured severities. PERSISTENT_FAIL and FLAKY never block a PR by default."""
    fail_on = [s.lower() for s in (fail_on or ["critical", "high"])]
    return [
        e for e in diff.entries
        if e.diff_type == "NEW_REGRESSION" and e.severity in fail_on
    ]
