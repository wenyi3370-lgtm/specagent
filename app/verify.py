"""Verify counts, verdict and quote (v1 design §8.8, task 15) — pure.

Lives at the top level so ``app/project.py`` and the CLI can use it without
importing the agent package (which imports ``app.project``).
"""
from .regression import DiffSummary

# Verdict table (§8.8), evaluated in order — first match wins.
VERDICT_ORDER = ("REGRESSED", "INCOMPLETE", "NOT_FIXED", "PARTIAL", "ALL_FIXED",
                 "NO_CHANGE")
# Exit codes: ALL_FIXED / NO_CHANGE → 0; everything else → 1.
VERDICT_EXIT_OK = {"ALL_FIXED", "NO_CHANGE"}


def verify_counts(diff: DiffSummary) -> dict:
    counts = {"fixed": 0, "still_failing": 0, "new_regression": 0, "flaky": 0,
              "new_error": 0, "canceled": 0, "stable_pass": 0, "new_tests": 0,
              "new_test_failing": 0, "candidate_error": 0}
    for entry in diff.entries:
        kind = entry.diff_type
        if kind == "FIXED":
            counts["fixed"] += 1
        elif kind == "PERSISTENT_FAIL":
            counts["still_failing"] += 1
        elif kind == "NEW_REGRESSION":
            counts["new_regression"] += 1
        elif kind == "FLAKY":
            counts["flaky"] += 1
        elif kind == "NEW_ERROR":
            counts["new_error"] += 1
        elif kind == "CANCELED":
            counts["canceled"] += 1
        elif kind == "STABLE_PASS":
            counts["stable_pass"] += 1
        elif kind == "NEW_TEST":
            counts["new_tests"] += 1
            if entry.candidate_status in ("FAIL", "ERROR", "FLAKY"):
                counts["new_test_failing"] += 1
        # FAIL→ERROR is classified PERSISTENT_FAIL by regression.classify but
        # the candidate errored: counted via candidate_error (§8.8).
        if entry.candidate_status == "ERROR" and kind != "NEW_ERROR":
            counts["candidate_error"] += 1
    return counts


def verify_verdict(counts: dict) -> str:
    if counts["new_regression"] + counts["new_error"] > 0:
        return "REGRESSED"
    if counts["canceled"] > 0:
        return "INCOMPLETE"
    if (counts["new_test_failing"] > 0 or counts["candidate_error"] > 0
            or (counts["fixed"] == 0 and counts["still_failing"] > 0)):
        return "NOT_FIXED"
    if counts["still_failing"] > 0 or counts["flaky"] > 0:
        return "PARTIAL"
    if counts["fixed"] > 0:
        return "ALL_FIXED"
    return "NO_CHANGE"


def _rule_ids(diff: DiffSummary, kinds) -> list[str]:
    ids = sorted({e.rule_id for e in diff.entries if e.diff_type in kinds and e.rule_id})
    return ids or ["—"]


def format_verify_quote(pre_run_id: str, verdict: str, counts: dict,
                        diff: DiffSummary | None = None,
                        spec_changed: bool = False) -> str:
    lines = []
    if spec_changed:
        lines.append("warning: spec changed since the pre-fix run")
    lines.append(f"Verification vs pre-fix run {pre_run_id} → {verdict}")
    lines.append(
        f"fixed={counts['fixed']} still_failing={counts['still_failing']} "
        f"new_regression={counts['new_regression']} flaky={counts['flaky']} "
        f"new_error={counts['new_error']} "
        f"new_test_failing={counts['new_test_failing']} canceled={counts['canceled']}")
    if diff is not None:
        fixed = _rule_ids(diff, {"FIXED"})
        failing = _rule_ids(diff, {"PERSISTENT_FAIL", "NEW_TEST"})
        regressed = _rule_ids(diff, {"NEW_REGRESSION", "NEW_ERROR"})
        lines.append(f"Rules: fixed: {', '.join(fixed)} | "
                     f"still failing: {', '.join(failing)} | "
                     f"regressed: {', '.join(regressed)}")
    return "\n".join(lines)
