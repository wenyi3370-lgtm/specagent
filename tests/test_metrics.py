"""Unit tests: observability metrics (roadmap v0.6 §9.2)."""
from app.metrics import compute_project_metrics, compute_run_metrics


def _run(run_id, results, tests, rules, total=None, passed=None, **kw):
    return {
        "id": run_id,
        "total": total if total is not None else len(results),
        "passed": passed if passed is not None else sum(1 for r in results if r["status"] == "PASS"),
        "failed": kw.get("failed", sum(1 for r in results if r["status"] in ("FAIL", "FLAKY"))),
        "errors": kw.get("errors", sum(1 for r in results if r["status"] == "ERROR")),
        "canceled": kw.get("canceled", 0),
        "spec": {"rules": rules},
        "tests": tests,
        "results": results,
        "started_at": kw.get("started_at", ""),
        "score": kw.get("score", 0.0),
    }


RULES = [{"id": "CRIT", "severity": "critical"}, {"id": "HIGH", "severity": "high"}]
TESTS = [{"id": "CRIT-01", "rule_id": "CRIT"}, {"id": "CRIT-02", "rule_id": "CRIT"},
         {"id": "HIGH-01", "rule_id": "HIGH"}]


def test_run_metrics_core_rates():
    run = _run("r1", [
        {"status": "PASS", "latency_ms": 100, "rule_id": "CRIT"},
        {"status": "FAIL", "latency_ms": 200, "rule_id": "CRIT"},
        {"status": "FLAKY", "latency_ms": 300, "rule_id": "HIGH"},
    ], TESTS, RULES)
    m = compute_run_metrics(run)
    assert abs(m["behavior_pass_rate"] - 1 / 3) < 1e-3
    # 1 of 2 critical tests violated
    assert m["critical_violation_rate"] == 0.5
    assert abs(m["flaky_rate"] - 1 / 3) < 1e-3
    assert abs(m["tool_accuracy"] - 1 / 3) < 1e-3  # 1 passed / 3 executed (no errors)
    assert m["latency_ms"]["median"] == 200 and m["latency_ms"]["p95"] == 300


def test_run_metrics_excludes_errors_from_tool_accuracy():
    run = _run("r1", [
        {"status": "PASS", "latency_ms": 10, "rule_id": "CRIT"},
        {"status": "ERROR", "latency_ms": 0, "rule_id": "HIGH"},
    ], TESTS, RULES, total=2, passed=1, errors=1)
    m = compute_run_metrics(run)
    assert m["tool_accuracy"] == 1.0  # passed / executed (errors excluded)
    assert m["critical_violation_rate"] == 0.0


def test_run_metrics_no_critical_tests_zero_rate():
    run = _run("r1", [{"status": "FAIL", "latency_ms": 1, "rule_id": "HIGH"}],
               [{"id": "HIGH-01", "rule_id": "HIGH"}], RULES)
    assert compute_run_metrics(run)["critical_violation_rate"] == 0.0


def test_project_metrics_uses_latest_run_and_diff():
    latest = _run("r2", [{"status": "FAIL", "latency_ms": 5, "rule_id": "CRIT"}],
                  TESTS, RULES, started_at="2026-10-05T10:00:00", score=66.7)
    older = _run("r1", [{"status": "PASS", "latency_ms": 5, "rule_id": "CRIT"}],
                 TESTS, RULES, started_at="2026-10-04T10:00:00", score=100.0)
    diff = type("D", (), {"new_regressions": 2})()
    m = compute_project_metrics([latest, older], latest, diff)
    assert m["runs"] == 2
    assert m["new_regression_count"] == 2
    assert m["behavior_pass_rate"] == 0.0
    assert [h["run_id"] for h in m["score_history"]] == ["r1", "r2"]  # oldest → newest


def test_project_metrics_empty():
    m = compute_project_metrics([], None)
    assert m["runs"] == 0 and m["behavior_pass_rate"] == 0.0 and m["score_history"] == []
