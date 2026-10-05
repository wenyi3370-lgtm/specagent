"""Run persistence (roadmap v0.2 "Run Persist").

SQLite keeps the tool dependency-free; the Store interface is the only thing
callers see, so v0.6 can swap in PostgreSQL without touching judge/diff/CLI.
Spec and test cases are snapshotted onto the run so any historical run can be
re-inspected and re-diffed even after the spec changes.
"""
import json
import logging
import os
import sqlite3
import time
import uuid
from pathlib import Path

logger = logging.getLogger("specagent.storage")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    spec_compiler TEXT NOT NULL DEFAULT '',
    agent TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'completed',
    is_baseline INTEGER NOT NULL DEFAULT 0,
    spec_json TEXT NOT NULL DEFAULT '{}',
    tests_json TEXT NOT NULL DEFAULT '[]',
    prompt_hash TEXT NOT NULL DEFAULT '',
    commit_sha TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    passed INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    score REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS executions (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    test_case_id TEXT NOT NULL,
    rule_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    response TEXT NOT NULL DEFAULT '',
    trace_json TEXT NOT NULL DEFAULT '[]',
    violations_json TEXT NOT NULL DEFAULT '[]',
    latency_ms INTEGER NOT NULL DEFAULT 0,
    repeat_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_executions_run ON executions(run_id);
"""


def default_db_path() -> str:
    return os.getenv("SPECAGENT_DB") or str(Path(os.getenv("SPECAGENT_CWD", ".")) / "specagent.db")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000):x}_{uuid.uuid4().hex[:6]}"


class Store:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or default_db_path()
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # -- runs ---------------------------------------------------------------

    def create_run(self, *, project_id: str, spec: dict, tests: list[dict],
                   label: str = "", spec_compiler: str = "", agent: str = "",
                   commit_sha: str | None = None) -> str:
        run_id = _new_id("run")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO runs (id, project_id, label, spec_compiler, agent, status,"
                " spec_json, tests_json, prompt_hash, commit_sha, started_at)"
                " VALUES (?,?,?,?,?,'running',?,?,?,?,?)",
                (run_id, project_id, label, spec_compiler, agent,
                 json.dumps(spec, ensure_ascii=False),
                 json.dumps(tests, ensure_ascii=False),
                 "", commit_sha,
                 time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())),
            )
        return run_id

    def complete_run(self, run_id: str, *, passed: int, failed: int, errors: int,
                     total: int, score: float, status: str = "completed") -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE runs SET status=?, passed=?, failed=?, errors=?, total=?, score=?,"
                " completed_at=? WHERE id=?",
                (status, passed, failed, errors, total, score,
                 time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()), run_id),
            )

    def add_execution(self, run_id: str, result: dict) -> str:
        exec_id = _new_id("exec")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO executions (id, run_id, test_case_id, rule_id, status, response,"
                " trace_json, violations_json, latency_ms, repeat_json)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (exec_id, run_id, result["test"]["id"], result["test"].get("rule_id", ""),
                 result["status"], result.get("execution", {}).get("response", ""),
                 json.dumps(result.get("execution", {}).get("trace", []), ensure_ascii=False),
                 json.dumps(result.get("violations", []), ensure_ascii=False),
                 result.get("execution", {}).get("latency_ms", 0),
                 json.dumps(result.get("repeat", []), ensure_ascii=False)),
            )
        return exec_id

    def get_run(self, run_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return None
            run = dict(row)
            run["spec"] = json.loads(run.pop("spec_json"))
            run["tests"] = json.loads(run.pop("tests_json"))
            execs = conn.execute(
                "SELECT * FROM executions WHERE run_id=? ORDER BY test_case_id, id", (run_id,)
            ).fetchall()
            results = []
            for e in execs:
                e = dict(e)
                e["trace"] = json.loads(e.pop("trace_json"))
                e["violations"] = json.loads(e.pop("violations_json"))
                e["repeat"] = json.loads(e.pop("repeat_json"))
                results.append(e)
            run["results"] = results
            run["is_baseline"] = bool(run["is_baseline"])
            return run

    def list_runs(self, project_id: str | None = None, limit: int = 50) -> list[dict]:
        query = ("SELECT id, project_id, label, spec_compiler, agent, status, is_baseline,"
                 " started_at, completed_at, passed, failed, errors, total, score, commit_sha"
                 " FROM runs")
        params: tuple = ()
        if project_id:
            query += " WHERE project_id=?"
            params = (project_id,)
        query += " ORDER BY started_at DESC, id DESC LIMIT ?"
        rows = None
        with self._connect() as conn:
            rows = conn.execute(query, params + (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["is_baseline"] = bool(d["is_baseline"])
            out.append(d)
        return out

    def set_baseline(self, run_id: str) -> None:
        with self._connect() as conn:
            row = conn.execute("SELECT project_id FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(f"run not found: {run_id}")
            conn.execute("UPDATE runs SET is_baseline=0 WHERE project_id=?", (row["project_id"],))
            conn.execute("UPDATE runs SET is_baseline=1 WHERE id=?", (run_id,))

    def get_baseline(self, project_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM runs WHERE project_id=? AND is_baseline=1"
                " ORDER BY started_at DESC LIMIT 1", (project_id,),
            ).fetchone()
        return self.get_run(row["id"]) if row else None

    def get_execution(self, execution_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM executions WHERE id=?", (execution_id,)
            ).fetchone()
        if row is None:
            return None
        e = dict(row)
        e["trace"] = json.loads(e.pop("trace_json"))
        e["violations"] = json.loads(e.pop("violations_json"))
        e["repeat"] = json.loads(e.pop("repeat_json"))
        return e
