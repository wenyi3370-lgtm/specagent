"""Observability metrics (roadmap v0.6 §9.2).

Pure functions over run dicts (as returned by Store.get_run) so they are
unit-testable without a database. The dashboard's four questions (§9.3) map
onto these numbers: how much broke, what newly broke, which rule, which event.
"""
from .models import DiffSummary


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, round(p * (len(sorted_values) - 1)))
    return sorted_values[index]


def compute_run_metrics(run: dict) -> dict:
    total = run.get("total", 0)
    passed = run.get("passed", 0)
    failed = run.get("failed", 0)
    errors = run.get("errors", 0)
    canceled = run.get("canceled", 0)
    results = run.get("results", [])

    severity_by_rule = {r.get("id"): r.get("severity") for r in (run.get("spec") or {}).get("rules", [])}
    critical_tests = [t for t in run.get("tests", [])
                      if severity_by_rule.get(t.get("rule_id")) == "critical"]
    critical_fails = sum(
        1 for r in results
        if r.get("status") in ("FAIL", "FLAKY")
        and severity_by_rule.get(r.get("rule_id")) == "critical"
    )
    flaky = sum(1 for r in results if r.get("status") == "FLAKY")
    latencies = sorted(float(r.get("latency_ms") or 0) for r in results)
    executed = total - errors - canceled

    return {
        "run_id": run.get("id"),
        "total": total,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "canceled": canceled,
        "behavior_pass_rate": round(passed / total, 4) if total else 0.0,
        "critical_violation_rate": round(critical_fails / len(critical_tests), 4) if critical_tests else 0.0,
        "flaky_rate": round(flaky / total, 4) if total else 0.0,
        # Tool Accuracy (§9.2): required/forbidden/gate assertions are all the
        # deterministic judge checks, so accuracy = pass rate among executed tests.
        "tool_accuracy": round(passed / executed, 4) if executed > 0 else 0.0,
        "latency_ms": {
            "median": _percentile(latencies, 0.5),
            "p95": _percentile(latencies, 0.95),
        },
    }


def compute_project_metrics(runs: list[dict], latest_run: dict | None,
                            diff: DiffSummary | None = None) -> dict:
    """Project-level view: latest-run metrics + cross-run indicators."""
    if latest_run is None:
        return {
            "runs": len(runs), "behavior_pass_rate": 0.0, "critical_violation_rate": 0.0,
            "new_regression_count": 0, "flaky_rate": 0.0, "tool_accuracy": 0.0,
            "latency_ms": {"median": 0.0, "p95": 0.0}, "score_history": [],
        }
    metrics = compute_run_metrics(latest_run)
    history = [
        {"run_id": r.get("id"), "score": r.get("score", 0.0),
         "started_at": r.get("started_at", ""), "label": r.get("label", "")}
        for r in sorted(runs, key=lambda x: x.get("started_at", ""))[-50:]
    ]
    metrics.update({
        "runs": len(runs),
        "new_regression_count": diff.new_regressions if diff else 0,
        "score_history": history,
    })
    return metrics
