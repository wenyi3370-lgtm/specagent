"""Unit tests: SQLite run persistence + baseline management."""
import pytest

from app.storage import Store


@pytest.fixture()
def store(tmp_path):
    return Store(str(tmp_path / "runs.db"))


SPEC = {"agent_name": "A", "rules": [{"id": "R1", "title": "r", "action": "refund", "severity": "critical"}]}
TESTS = [{"id": "R1-01", "rule_id": "R1", "category": "normal", "user_input": "x"}]


def _result(status="PASS"):
    return {
        "test": TESTS[0], "status": status, "violations": [],
        "execution": {"response": "ok", "trace": [{"seq": 1, "type": "tool_call", "name": "refund", "args": {}}],
                      "latency_ms": 12, "error": None},
        "repeat": [],
    }


def test_run_roundtrip(store):
    run_id = store.create_run(project_id="p", spec=SPEC, tests=TESTS, agent="demo:vulnerable")
    store.add_execution(run_id, _result("PASS"))
    store.add_execution(run_id, _result("FAIL"))
    store.complete_run(run_id, passed=1, failed=1, errors=0, total=2, score=50.0)

    run = store.get_run(run_id)
    assert run["id"] == run_id
    assert run["status"] == "completed"
    assert run["spec"]["rules"][0]["id"] == "R1"
    assert [t["id"] for t in run["tests"]] == ["R1-01"]
    assert len(run["results"]) == 2
    assert run["results"][0]["trace"][0]["name"] == "refund"
    assert run["passed"] == 1 and run["score"] == 50.0


def test_list_runs_filtered_by_project(store):
    a = store.create_run(project_id="a", spec=SPEC, tests=TESTS)
    b = store.create_run(project_id="b", spec=SPEC, tests=TESTS)
    store.complete_run(a, passed=1, failed=0, errors=0, total=1, score=100)
    store.complete_run(b, passed=0, failed=1, errors=0, total=1, score=0)
    ids = {r["id"] for r in store.list_runs("a")}
    assert ids == {a} and b not in ids


def test_baseline_is_unique_per_project(store):
    r1 = store.create_run(project_id="p", spec=SPEC, tests=TESTS)
    r2 = store.create_run(project_id="p", spec=SPEC, tests=TESTS)
    r3 = store.create_run(project_id="other", spec=SPEC, tests=TESTS)
    store.set_baseline(r1)
    assert store.get_baseline("p")["id"] == r1
    store.set_baseline(r2)
    assert store.get_baseline("p")["id"] == r2  # replaced, still exactly one
    store.set_baseline(r3)
    assert store.get_baseline("p")["id"] == r2  # other project untouched


def test_set_baseline_unknown_run_raises(store):
    with pytest.raises(KeyError):
        store.set_baseline("run_nope")


def test_get_execution(store):
    run_id = store.create_run(project_id="p", spec=SPEC, tests=TESTS)
    exec_id = store.add_execution(run_id, _result("FAIL"))
    execution = store.get_execution(exec_id)
    assert execution["status"] == "FAIL"
    assert execution["violations"] == []
    assert execution["trace"][0]["type"] == "tool_call"


def test_get_run_missing_returns_none(store):
    assert store.get_run("run_missing") is None


def test_review_roundtrip(store):
    run_id = store.create_run(project_id="p", spec=SPEC, tests=TESTS)
    exec_id = store.add_execution(run_id, _result("FAIL"))
    review = {"verdict": "pass", "reviewer": "alice", "note": "误报", "reviewed_at": "2026-10-05T00:00:00"}
    store.add_review(exec_id, review)
    assert store.get_execution(exec_id)["review"] == review


def test_add_review_unknown_execution_raises(store):
    import pytest
    with pytest.raises(KeyError):
        store.add_review("exec_nope", {"verdict": "pass"})


def test_v05_columns_migrate_existing_database(tmp_path):
    """A v0.3-era database (without llm_verdict_json/review_json) keeps working."""
    import sqlite3
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE runs (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, label TEXT NOT NULL DEFAULT '',"
        " spec_compiler TEXT NOT NULL DEFAULT '', agent TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,"
        " is_baseline INTEGER NOT NULL DEFAULT 0, spec_json TEXT NOT NULL, tests_json TEXT NOT NULL,"
        " prompt_hash TEXT NOT NULL DEFAULT '', commit_sha TEXT, started_at TEXT NOT NULL, completed_at TEXT,"
        " passed INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0, errors INTEGER NOT NULL DEFAULT 0,"
        " total INTEGER NOT NULL DEFAULT 0, score REAL NOT NULL DEFAULT 0);"
        "CREATE TABLE executions (id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),"
        " test_case_id TEXT NOT NULL, rule_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,"
        " response TEXT NOT NULL DEFAULT '', trace_json TEXT NOT NULL DEFAULT '[]',"
        " violations_json TEXT NOT NULL DEFAULT '[]', latency_ms INTEGER NOT NULL DEFAULT 0,"
        " repeat_json TEXT NOT NULL DEFAULT '[]');"
        "INSERT INTO runs (id, project_id, status, spec_json, tests_json, started_at)"
        " VALUES ('run_old', 'p', 'completed', '{}', '[]', '2026-10-01T00:00:00');"
        "INSERT INTO executions (id, run_id, test_case_id, rule_id, status)"
        " VALUES ('exec_old', 'run_old', 'R1-01', 'R1', 'PASS');"
    )
    conn.commit()
    conn.close()

    migrated = Store(db)
    run = migrated.get_run("run_old")
    assert run["results"][0]["review"] is None
    assert run["results"][0]["llm_verdict"] is None
    migrated.add_review("exec_old", {"verdict": "fail"})  # migration allows writes too
    assert migrated.get_execution("exec_old")["review"] == {"verdict": "fail"}
