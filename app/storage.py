"""Run persistence (roadmap v0.6 §9.1, project-ization).

The Store speaks SQLAlchemy, so the same implementation serves SQLite
(default, zero-config) and PostgreSQL (``SPECAGENT_DB=postgresql+psycopg://…``)
— the interface callers see never changes:

    Store(db) .create_run(...) .add_execution(...) .get_run(...) ...

Entities per roadmap §9.1: projects, specs (versioned, content-deduped),
runs, executions, violations (normalized, queryable), plus test-case and
trace snapshots kept on the run/execution rows so any historical run can be
re-diffed exactly (see docs/architecture.md design decisions).
"""
import hashlib
import json
import logging
import os
import time
import uuid
from pathlib import Path

from sqlalchemy import JSON, String, Text, create_engine, select, func, update
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool
from urllib.parse import urlparse

logger = logging.getLogger("specagent.storage")


def default_db_path() -> str:
    """SPECAGENT_DB may be a file path (SQLite) or a full SQLAlchemy URL
    (e.g. postgresql+psycopg://user:pass@host/db)."""
    return os.getenv("SPECAGENT_DB") or str(Path(os.getenv("SPECAGENT_CWD", ".")) / "specagent.db")


def backend_name(url: str) -> str:
    """Dialect family of a SQLAlchemy URL without credentials (v1 design
    §3.4): `postgresql+psycopg://admin:secret@host/db` -> `postgresql`. The
    full URL may embed `user:password@host` and must never leave the server."""
    return (urlparse(url).scheme.split("+")[0] or "sqlite").lower()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000):x}_{uuid.uuid4().hex[:6]}"


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    adapter_type: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")


class SpecRecord(Base):
    __tablename__ = "specs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(default=1)
    source_text: Mapped[str] = mapped_column(Text, default="")
    compiled_json: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    created_at: Mapped[str] = mapped_column(String(32), default="")


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), index=True)
    spec_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    label: Mapped[str] = mapped_column(String(256), default="")
    spec_compiler: Mapped[str] = mapped_column(String(128), default="")
    agent: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="running")
    is_baseline: Mapped[bool] = mapped_column(default=False)
    spec_json: Mapped[dict] = mapped_column(JSON)
    tests_json: Mapped[list] = mapped_column(JSON)
    prompt_hash: Mapped[str] = mapped_column(String(64), default="")
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[str] = mapped_column(String(32), default="")
    completed_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    passed: Mapped[int] = mapped_column(default=0)
    failed: Mapped[int] = mapped_column(default=0)
    errors: Mapped[int] = mapped_column(default=0)
    canceled: Mapped[int] = mapped_column(default=0)
    total: Mapped[int] = mapped_column(default=0)
    score: Mapped[float] = mapped_column(default=0.0)


class Execution(Base):
    __tablename__ = "executions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    test_case_id: Mapped[str] = mapped_column(String(128), default="")
    rule_id: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(16), default="")
    response: Mapped[str] = mapped_column(Text, default="")
    trace_json: Mapped[list] = mapped_column(JSON)
    violations_json: Mapped[list] = mapped_column(JSON)
    latency_ms: Mapped[int] = mapped_column(default=0)
    repeat_json: Mapped[list] = mapped_column(JSON)
    llm_verdict_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    review_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class RunProgress(Base):
    """Separate table so old databases gain progress without column changes."""
    __tablename__ = "run_progress"
    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    snapshot_json: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[str] = mapped_column(String(32), default="")


class Violation(Base):
    """Normalized violations (§9.1) — queryable evidence for metrics/audit."""
    __tablename__ = "violations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    test_case_id: Mapped[str] = mapped_column(String(128), default="")
    rule_id: Mapped[str] = mapped_column(String(128), default="")
    severity: Mapped[str] = mapped_column(String(16), default="medium")
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(32), default="")


# Columns added after the initial v0.3 schema — existing SQLite databases are
# migrated in place (create_all cannot add columns to existing tables).
_MIGRATIONS = {
    "runs": {"spec_id": "TEXT", "canceled": "INTEGER DEFAULT 0"},
    "executions": {"llm_verdict_json": "TEXT", "review_json": "TEXT"},
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


def _install_sqlite_pragmas(engine) -> None:
    """WAL journal + busy timeout: concurrent web + CLI access on one SQLite
    file must not trip 'database is locked' (roadmap §11.3)."""
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def _content_hash(compiled: dict) -> str:
    """Version identity for a compiled spec. v0.9-added keys are dropped while
    they hold their defaults, so every pre-v0.9 spec keeps its exact digest
    (v1 design §5.4) — otherwise the upgrade would create one spurious spec
    version per project."""
    import copy
    normalized = copy.deepcopy(compiled)
    if normalized.get("locale") == "zh":
        normalized.pop("locale", None)
    for rule in normalized.get("rules") or []:
        if isinstance(rule, dict):
            if rule.get("constraints") == []:
                rule.pop("constraints", None)
            if rule.get("probes") == []:
                rule.pop("probes", None)
    return hashlib.sha256(json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Store:
    def __init__(self, db_path: str | None = None):
        self.db_url = self._to_url(db_path or default_db_path())
        self.db_path = self.db_url  # kept for backward compatibility; no endpoint returns it
        if self.db_url == "sqlite://" or self.db_url.startswith("sqlite:///:memory:"):
            self._engine = create_engine(
                "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        elif self.db_url.startswith("sqlite:///"):
            self._engine = create_engine(
                self.db_url,
                # WAL + busy_timeout let the dashboard (web) and the CLI work on
                # the same database concurrently (roadmap §11.3).
                connect_args={"check_same_thread": False, "timeout": 15},
            )
            _install_sqlite_pragmas(self._engine)
        else:  # postgresql+psycopg://… — driver imported lazily by SQLAlchemy
            self._engine = create_engine(self.db_url)
        Base.metadata.create_all(self._engine)
        self._migrate()
        self._session = sessionmaker(bind=self._engine, expire_on_commit=False)
        self._backfill_projects()

    @property
    def backend(self) -> str:
        """Dialect family name, safe to expose (no credentials)."""
        return backend_name(self.db_url)

    @staticmethod
    def _to_url(path_or_url: str) -> str:
        if "://" in path_or_url:
            if path_or_url.startswith("postgres://"):
                return path_or_url.replace("postgres://", "postgresql+psycopg://", 1)
            return path_or_url
        if path_or_url == ":memory:":
            return "sqlite://"
        path = Path(path_or_url).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{path.as_posix()}"

    def _migrate(self) -> None:
        from sqlalchemy import inspect, text
        inspector = inspect(self._engine)
        with self._engine.begin() as conn:
            for table, columns in _MIGRATIONS.items():
                if not inspector.has_table(table):
                    continue
                existing = {c["name"] for c in inspector.get_columns(table)}
                for column, ddl in columns.items():
                    if column not in existing:
                        logger.info("migrating: adding %s.%s", table, column)
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))

    def _backfill_projects(self) -> None:
        """Pre-v0.6 databases have runs without project rows; register them."""
        with self._session() as s:
            existing = {p.id for p in s.execute(select(Project)).scalars()}
            used = {row[0] for row in s.execute(select(Run.project_id).distinct())}
            for pid in sorted(used - existing):
                s.add(Project(id=pid, name=pid, created_at=_now()))
            if used - existing:
                s.commit()

    # -- projects / specs (§9.1) --------------------------------------------

    def ensure_project(self, project_id: str, name: str | None = None, adapter_type: str = "") -> None:
        with self._session() as s:
            if s.get(Project, project_id) is None:
                s.add(Project(id=project_id, name=name or project_id,
                              adapter_type=adapter_type, created_at=_now()))
                s.commit()

    def create_project(self, project_id: str, name: str = "", description: str = "",
                       adapter_type: str = "") -> dict:
        pid = project_id or name.lower().replace(" ", "-")
        with self._session() as s:
            if s.get(Project, pid) is not None:
                raise KeyError(f"project already exists: {pid}")
            project = Project(id=pid, name=name or pid, description=description,
                              adapter_type=adapter_type, created_at=_now())
            s.add(project)
            s.commit()
            return {"id": project.id, "name": project.name, "description": project.description,
                    "adapter_type": project.adapter_type, "created_at": project.created_at}

    def list_projects(self) -> list[dict]:
        with self._session() as s:
            rows = s.execute(
                select(Project, func.count(Run.id), func.max(Run.started_at))
                .outerjoin(Run, Run.project_id == Project.id)
                .group_by(Project.id)
                .order_by(Project.id)
            ).all()
            return [{
                "id": p.id, "name": p.name, "description": p.description,
                "adapter_type": p.adapter_type, "created_at": p.created_at,
                "runs": count or 0, "last_run_at": last,
            } for p, count, last in rows]

    def save_spec(self, project_id: str, source_text: str, compiled: dict) -> str:
        """Persist a spec version; identical content reuses the latest version."""
        digest = _content_hash(compiled)
        with self._session() as s:
            latest = s.execute(
                select(SpecRecord).where(SpecRecord.project_id == project_id)
                .order_by(SpecRecord.version.desc()).limit(1)
            ).scalars().first()
            if latest is not None and latest.content_hash == digest:
                return latest.id
            record = SpecRecord(
                id=_new_id("spec"), project_id=project_id,
                version=(latest.version + 1) if latest else 1,
                source_text=source_text, compiled_json=compiled,
                content_hash=digest, created_at=_now(),
            )
            s.add(record)
            s.commit()
            return record.id

    def list_specs(self, project_id: str) -> list[dict]:
        with self._session() as s:
            rows = s.execute(
                select(SpecRecord).where(SpecRecord.project_id == project_id)
                .order_by(SpecRecord.version.desc())
            ).scalars().all()
            return [{
                "id": r.id, "project_id": r.project_id, "version": r.version,
                "compiler": (r.compiled_json or {}).get("compiler", ""),
                "rules": len((r.compiled_json or {}).get("rules", [])),
                "source_chars": len(r.source_text or ""),
                "created_at": r.created_at,
            } for r in rows]

    def get_spec(self, spec_id: str) -> dict | None:
        with self._session() as s:
            record = s.get(SpecRecord, spec_id)
            if record is None:
                return None
            runs = s.execute(select(Run.id, Run.label, Run.status, Run.started_at)
                .where(Run.spec_id == spec_id).order_by(Run.started_at.desc(), Run.id.desc()).limit(50)).all()
            count = s.scalar(select(func.count()).select_from(Run).where(Run.spec_id == spec_id)) or 0
            return {"id": record.id, "project_id": record.project_id, "version": record.version,
                    "source": record.source_text, "compiled": record.compiled_json,
                    "created_at": record.created_at, "content_hash": record.content_hash,
                    "run_count": count, "runs": [dict(zip(("id", "label", "status", "started_at"), row)) for row in runs]}

    # -- runs ---------------------------------------------------------------

    def create_run(self, *, project_id: str, spec: dict, tests: list[dict],
                   label: str = "", spec_compiler: str = "", agent: str = "",
                   commit_sha: str | None = None, spec_id: str | None = None) -> str:
        run_id = _new_id("run")
        with self._session() as s:
            s.add(Run(
                id=run_id, project_id=project_id, spec_id=spec_id, label=label,
                spec_compiler=spec_compiler, agent=agent, status="running",
                spec_json=spec, tests_json=tests, prompt_hash="",
                commit_sha=commit_sha, started_at=_now(),
            ))
            s.commit()
        return run_id

    def _save_progress(self, run_id: str, snapshot: dict) -> None:
        with self._session() as s:
            if s.get(Run, run_id) is None:
                raise KeyError("run_not_found")
            row = s.get(RunProgress, run_id)
            if row is None:
                row = RunProgress(run_id=run_id)
                s.add(row)
            row.snapshot_json = dict(snapshot)
            row.updated_at = _now()
            s.commit()

    def get_progress(self, run_id: str) -> dict | None:
        with self._session() as s:
            run = s.get(Run, run_id)
            if run is None:
                return None
            row = s.get(RunProgress, run_id)
            if row is not None:
                return {"run_id": run_id, "project_id": run.project_id,
                        "updated_at": row.updated_at, **row.snapshot_json}
            final = run.status in ("completed", "canceled")
            return {"run_id": run_id, "project_id": run.project_id,
                    "available": False, "cancellable": False,
                    "phase": "completed" if final else "unavailable",
                    "final": final, "completed": run.total if final else 0,
                    "total": run.total if final else len(run.tests_json or []),
                    "passed": run.passed, "failed": run.failed, "errors": run.errors,
                    "flaky": s.scalar(select(func.count()).select_from(Execution).where(
                        Execution.run_id == run_id, Execution.status == "FLAKY")) or 0,
                    "canceled": run.canceled, "last_case": None,
                    "updated_at": run.completed_at or run.started_at}

    def complete_run(self, run_id: str, *, passed: int, failed: int, errors: int,
                     total: int, score: float, status: str = "completed",
                     canceled: int = 0) -> None:
        with self._session() as s:
            run = s.get(Run, run_id)
            if run is None:
                raise KeyError(f"run not found: {run_id}")
            run.status = status
            run.passed, run.failed, run.errors = passed, failed, errors
            run.canceled, run.total, run.score = canceled, total, score
            run.completed_at = _now()
            s.commit()

    def add_execution(self, run_id: str, result: dict) -> str:
        exec_id = _new_id("exec")
        test = result.get("test", {})
        execution = result.get("execution", {})
        with self._session() as s:
            s.add(Execution(
                id=exec_id, run_id=run_id, test_case_id=test.get("id", ""),
                rule_id=test.get("rule_id", ""), status=result.get("status", ""),
                response=execution.get("response", ""),
                trace_json=execution.get("trace", []),
                violations_json=result.get("violations", []),
                latency_ms=execution.get("latency_ms", 0),
                repeat_json=result.get("repeat", []),
                llm_verdict_json=result.get("llm_verdict"),
                review_json=result.get("review"),
            ))
            for row in result.get("violation_rows", []):
                s.add(Violation(
                    id=_new_id("viol"), execution_id=exec_id, run_id=run_id,
                    test_case_id=test.get("id", ""), rule_id=row.get("rule_id", ""),
                    severity=row.get("severity", "medium"), reason=row.get("reason", ""),
                    evidence_json=row.get("evidence", {}), created_at=_now(),
                ))
            s.commit()
        return exec_id

    def add_review(self, execution_id: str, review: dict) -> None:
        """Human review of an uncertain/failing critical case (roadmap §8.3 layer 4)."""
        with self._session() as s:
            execution = s.get(Execution, execution_id)
            if execution is None:
                raise KeyError(f"execution not found: {execution_id}")
            execution.review_json = review
            s.commit()

    @staticmethod
    def _execution_row(e: Execution) -> dict:
        return {
            "id": e.id, "run_id": e.run_id, "test_case_id": e.test_case_id,
            "rule_id": e.rule_id, "status": e.status, "response": e.response,
            "trace": e.trace_json or [], "violations": e.violations_json or [],
            "latency_ms": e.latency_ms, "repeat": e.repeat_json or [],
            "llm_verdict": e.llm_verdict_json, "review": e.review_json,
        }

    def get_run(self, run_id: str) -> dict | None:
        with self._session() as s:
            run = s.get(Run, run_id)
            if run is None:
                return None
            execs = s.execute(
                select(Execution).where(Execution.run_id == run_id)
                .order_by(Execution.test_case_id, Execution.id)
            ).scalars().all()
            out = {
                "id": run.id, "project_id": run.project_id, "spec_id": run.spec_id,
                "label": run.label, "spec_compiler": run.spec_compiler, "agent": run.agent,
                "status": run.status, "is_baseline": bool(run.is_baseline),
                "spec": run.spec_json or {}, "tests": run.tests_json or [],
                "prompt_hash": run.prompt_hash, "commit_sha": run.commit_sha,
                "started_at": run.started_at, "completed_at": run.completed_at,
                "passed": run.passed, "failed": run.failed, "errors": run.errors,
                "canceled": run.canceled, "total": run.total, "score": run.score,
                "results": [self._execution_row(e) for e in execs],
            }
        return out

    def list_runs(self, project_id: str | None = None, limit: int = 50) -> list[dict]:
        query = select(
            Run.id, Run.project_id, Run.spec_id, Run.label, Run.spec_compiler,
            Run.agent, Run.status, Run.is_baseline, Run.started_at, Run.completed_at,
            Run.passed, Run.failed, Run.errors, Run.canceled, Run.total, Run.score,
            Run.commit_sha,
        )
        if project_id:
            query = query.where(Run.project_id == project_id)
        query = query.order_by(Run.started_at.desc(), Run.id.desc()).limit(limit)
        with self._session() as s:
            rows = s.execute(query).all()
        keys = ("id", "project_id", "spec_id", "label", "spec_compiler", "agent",
                "status", "is_baseline", "started_at", "completed_at",
                "passed", "failed", "errors", "canceled", "total", "score", "commit_sha")
        out = []
        for row in rows:
            d = dict(zip(keys, row))
            d["is_baseline"] = bool(d["is_baseline"])
            out.append(d)
        return out

    def set_baseline(self, run_id: str) -> None:
        with self._session() as s:
            run = s.get(Run, run_id)
            if run is None:
                raise KeyError(f"run not found: {run_id}")
            s.execute(
                update(Run)
                .where(Run.project_id == run.project_id).values(is_baseline=False)
            )
            run.is_baseline = True
            s.commit()

    def get_baseline(self, project_id: str) -> dict | None:
        with self._session() as s:
            row = s.execute(
                select(Run.id).where(Run.project_id == project_id, Run.is_baseline.is_(True))
                .order_by(Run.started_at.desc()).limit(1)
            ).first()
        return self.get_run(row[0]) if row else None

    def get_execution(self, execution_id: str) -> dict | None:
        with self._session() as s:
            e = s.get(Execution, execution_id)
            return self._execution_row(e) if e else None

    def list_violations(self, run_id: str) -> list[dict]:
        with self._session() as s:
            rows = s.execute(
                select(Violation).where(Violation.run_id == run_id)
                .order_by(Violation.test_case_id, Violation.id)
            ).scalars().all()
            return [{
                "id": v.id, "execution_id": v.execution_id, "run_id": v.run_id,
                "test_case_id": v.test_case_id, "rule_id": v.rule_id,
                "severity": v.severity, "reason": v.reason,
                "evidence": v.evidence_json or {}, "created_at": v.created_at,
            } for v in rows]
