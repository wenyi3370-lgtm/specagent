import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, agent_api, orchestrator, regression
from .adapters import resolve_adapter
from .adapters.base import AgentAdapter
from .auth import (_host_header_allowed, agent_api_enabled, configured_token,
                   log_startup_warning, require_api_token)
from .cancellation import CancelRegistry
from .compiler import compile_spec
from .errors import SpecValidationError
from .generator import generate_tests
from .metrics import compute_project_metrics
from .models import (
    BehaviorSpec, CompileRequest, CreateProjectRequest, CreateRunRequest,
    DiffSummary, LLMJudgeVerdict, ProjectRunRequest, ReviewRequest, RunAllResponse,
    RunDetail, RunSummary,
)
from .project import (PROJECT_CONFIG_ENV, Project, case_count,
                      project_config_path, release_run, try_acquire_run)
from .storage import Store

load_dotenv()
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("specagent.api")

BASE = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_app: FastAPI):
    log_startup_warning(logger)
    yield


app = FastAPI(title="SpecAgent", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

# Every /api/* route except /api/health requires the token when one is
# configured (v1 design §3.4). Route paths are unchanged, so existing tests
# are unaffected in local/no-token mode.
protected = APIRouter(dependencies=[Depends(require_api_token)])

store = Store()
cancel_registry = CancelRegistry()


@app.get("/")
def home():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "version": __version__,
        "compiler": "openai" if os.getenv("OPENAI_API_KEY") else "demo",
        "agent": "external" if os.getenv("TARGET_AGENT_URL") else "demo",
        # backend name only — never the URL (it may embed user:password@host)
        "db": store.backend,
        "auth_required": configured_token() is not None,
        "agent_enabled": agent_api_enabled(),
        # file existence only — never the path or its contents (方案 B)
        "project_configured": Path(project_config_path()).is_file(),
    }


@protected.post("/api/compile")
@protected.post("/api/specs/compile")
def compile_endpoint(req: CompileRequest):
    return compile_spec(req.text)


def _adapter_for(agent: str, agent_variant: str | None) -> AgentAdapter:
    try:
        return resolve_adapter(agent=agent, agent_variant=agent_variant)
    except SpecValidationError as exc:
        raise HTTPException(status_code=422, detail="; ".join(exc.errors)) from None


@protected.post("/api/run-all", response_model=RunAllResponse)
async def run_all(req: CompileRequest):
    """Legacy v0.1 endpoint: compile from text, execute, and return everything.

    Kept for backward compatibility; it now also persists the run and diffs
    against the project baseline.
    """
    spec = compile_spec(req.text)
    tests = generate_tests(spec)
    run_id, results, diff = await orchestrator.run_with_diff(
        store, project_id="default", spec=spec, tests=tests,
        adapter=_adapter_for("auto", None), spec_source=req.text,
    )
    stats = orchestrator.summarize(results)
    return RunAllResponse(
        spec=spec, tests=tests, results=results,
        passed=stats["passed"], failed=stats["failed"], score=stats["score"],
        run_id=run_id, diff=diff,
    )


def _run_detail(run: dict) -> RunDetail:
    """Shape a raw store run dict into the API model."""
    test_input = {t["id"]: t for t in run.get("tests", [])}
    from .models import AgentExecution, TestCase, TestResult, TraceEvent
    results: list[TestResult] = []
    for row in run.get("results", []):
        case = TestCase.model_validate(test_input[row["test_case_id"]])
        execution = AgentExecution(
            response=row.get("response", ""),
            trace=[TraceEvent.model_validate(e) for e in row.get("trace", [])],
            latency_ms=row.get("latency_ms", 0),
        )
        llm_verdict = None
        if row.get("llm_verdict"):
            llm_verdict = LLMJudgeVerdict.model_validate(row["llm_verdict"])
        results.append(TestResult(
            test=case,
            passed=row["status"] == "PASS",
            status=row["status"],
            violations=row.get("violations", []),
            execution=execution,
            latency_ms=row.get("latency_ms", 0),
            execution_id=row.get("id"),
            llm_verdict=llm_verdict,
            review=row.get("review"),
        ))
    return RunDetail(
        id=run["id"], project_id=run["project_id"], spec_id=run.get("spec_id"),
        label=run.get("label", ""),
        spec_compiler=run.get("spec_compiler", ""), status=run["status"],
        is_baseline=run["is_baseline"], started_at=run["started_at"],
        completed_at=run.get("completed_at") or "",
        passed=run["passed"], failed=run["failed"], errors=run["errors"],
        canceled=run.get("canceled", 0),
        total=run["total"], score=run["score"], commit_sha=run.get("commit_sha"),
        agent=run.get("agent", ""),
        spec=BehaviorSpec.model_validate(run.get("spec", {})),
        tests=[TestCase.model_validate(t) for t in run.get("tests", [])],
        results=results,
    )


@protected.post("/api/runs")
async def create_run(req: CreateRunRequest):
    """Execute a behavior test run (roadmap §14.4 POST /api/runs)."""
    spec = (
        req.spec
        if req.spec is not None
        else compile_spec(req.text) if req.text else None
    )
    if spec is None:
        raise HTTPException(status_code=422, detail="either 'text' or a full 'spec' is required")
    tests = generate_tests(spec)
    if req.llm_expand:
        from .expander import expand_tests
        tests = await expand_tests(spec, tests)
    run_id, results, diff = await orchestrator.run_with_diff(
        store,
        project_id=req.project_id,
        spec=spec,
        tests=tests,
        label=req.label,
        agent=req.agent,
        agent_variant=req.agent_variant,
        adapter=_adapter_for(req.agent, req.agent_variant),
        concurrency=req.concurrency,
        timeout_seconds=req.timeout_seconds,
        repeat=req.repeat,
        retries=req.retries,
        max_trace_events=req.max_trace_events,
        max_response_chars=req.max_response_chars,
        set_baseline=req.set_baseline,
        spec_source=req.text or None,
        cancel_registry=cancel_registry,
    )
    return {"run": _run_detail(store.get_run(run_id)), "diff": diff}


@protected.get("/api/runs", response_model=list[RunSummary])
def list_runs(project_id: str | None = None, limit: int = Query(default=50, ge=1, le=200)):
    return store.list_runs(project_id, limit)


# -- server-configured project (方案 B): the dashboard runs what the server's
#    SPECAGENT_PROJECT_CONFIG points at; the browser never picks the spec,
#    adapter, project or endpoint ----------------------------------------------


def _project_config_errors(exc: SpecValidationError) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"error": "project_config_invalid", "errors": list(exc.errors)},
    )


def _load_server_project() -> Project:
    """Reload the project on every request so edits to the config or the spec
    take effect immediately (same file the agent panel uses)."""
    path = project_config_path()
    if not Path(path).is_file():
        raise HTTPException(
            status_code=422,
            detail={"error": "project_config_missing",
                    "errors": [f"project config not found: {Path(path).name} "
                               f"(set {PROJECT_CONFIG_ENV})"]},
        )
    try:
        return Project.load(path, store=store)
    except SpecValidationError as exc:
        raise _project_config_errors(exc) from None


@protected.get("/api/project")
def get_project():
    """Describe the configured target (read-only). The payload carries counts
    and labels only — no paths, no env values, no URLs with credentials."""
    path = project_config_path()
    if not Path(path).is_file():
        return {"configured": False, "mode": "demo",
                "reason": f"no {Path(path).name} (set {PROJECT_CONFIG_ENV})"}
    try:
        project = Project.load(path, store=store)
        spec = project.spec
        run = project.run_settings
        return {
            "configured": True,
            "project_id": project.project_id,
            "adapter": {"type": project.adapter_type, "label": project.adapter_label},
            "spec": {"rules": len(spec.rules), "cases": case_count(spec)},
            "gate": {"fail_on": list(project.gate_fail_on)},
            "run": {"concurrency": run.concurrency, "timeout_seconds": run.timeout_seconds,
                    "repeat": run.repeat},
            "mode": "project",
        }
    except SpecValidationError as exc:
        return {"configured": False, "mode": "error", "errors": list(exc.errors)}


def _project_run_guard(request: Request) -> None:
    """POST /api/project/runs executes the *configured* agent on this server,
    so it carries two protections the legacy demo endpoint never had:
    - CSRF: a cross-site form post cannot set Content-Type: application/json
      without a preflight this app never answers for /api/*;
    - DNS rebinding in no-token local mode: the same loopback-Host rule as the
      agent API (a rebound page keeps the attacker's own Host header)."""
    ctype = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    if ctype != "application/json":
        raise HTTPException(422, detail="Content-Type: application/json is required")
    if configured_token() is None and not _host_header_allowed(request):
        raise HTTPException(
            403,
            detail="project_run_requires_token: set SPECAGENT_API_TOKEN "
                   "(non-loopback Host header)",
        )


@protected.post("/api/project/runs", dependencies=[Depends(_project_run_guard)])
async def run_project_suite(req: ProjectRunRequest):
    """Run the configured project (adapters/settings/spec from the config file).

    Gate verdict = regression.gate_violations(diff, gate.fail_on) — the exact
    helper the CLI gate uses. One run per project at a time (in-process lock,
    shared with run_project): a second concurrent POST gets 409.
    """
    path = project_config_path()
    lock = try_acquire_run(path)
    if lock is None:
        raise HTTPException(
            409,
            detail="project_run_in_progress: a run for this project is already in flight",
        )
    try:
        project = _load_server_project()
        try:
            adapter = project.make_adapter()
        except SpecValidationError as exc:
            raise _project_config_errors(exc) from None
        except Exception as exc:  # noqa: BLE001 — e.g. the agent module fails to import
            raise HTTPException(
                422,
                detail={"error": "agent_import_failed",
                        "errors": [f"adapter.agent: {type(exc).__name__}: {exc}"]},
            ) from None
        if project.adapter_type == "http" and not os.getenv(project.endpoint_env):
            raise _project_config_errors(SpecValidationError([
                f"Configuration error: adapter.type=http but env "
                f"{project.endpoint_env} is not set."]))
        run = project.run_settings
        spec = project.spec
        tests = generate_tests(spec)
        run_id, _results, diff = await orchestrator.run_with_diff(
            store,
            project_id=project.project_id,
            spec=spec,
            tests=tests,
            label=req.label,
            adapter=adapter,
            concurrency=run.concurrency,
            timeout_seconds=run.timeout_seconds,
            repeat=run.repeat,
            retries=run.retries,
            max_trace_events=run.max_trace_events,
            max_response_chars=run.max_response_chars,
            spec_source=project.spec_bytes.decode("utf-8"),
            cancel_registry=cancel_registry,
        )
        fail_on = list(project.gate_fail_on)
        violations = regression.gate_violations(diff, fail_on) if diff else []
        return {"run": _run_detail(store.get_run(run_id)), "diff": diff,
                "gate": {"failed": bool(violations), "fail_on": fail_on,
                         "violations": violations}}
    finally:
        release_run(lock)


# -- projects / specs / metrics (roadmap v0.6 §9) -----------------------------


@protected.post("/api/projects")
def create_project(req: CreateProjectRequest):
    try:
        return store.create_project(req.id or "", req.name, req.description, req.adapter_type)
    except KeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@protected.get("/api/projects")
def list_projects():
    return store.list_projects()


@protected.get("/api/specs")
def list_specs(project_id: str = "default"):
    return store.list_specs(project_id)


@protected.get("/api/metrics")
def get_metrics(project_id: str = "default"):
    """Observability metrics (§9.2) for the dashboard's four questions (§9.3)."""
    runs = store.list_runs(project_id, limit=100)
    if not runs:
        return {"project_id": project_id, **compute_project_metrics([], None)}
    latest = store.get_run(runs[0]["id"])
    baseline = store.get_baseline(project_id)
    diff = None
    if baseline and baseline["id"] != latest["id"]:
        diff = regression.diff_runs(baseline, latest)
    return {"project_id": project_id, **compute_project_metrics(runs, latest, diff)}


@protected.get("/api/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: str):
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    return _run_detail(run)


@protected.get("/api/runs/{run_id}/report")
def get_report(run_id: str):
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    baseline = store.get_baseline(run["project_id"])
    diff = regression.diff_runs(baseline, run) if baseline and baseline["id"] != run_id else None
    return {"run": run, "diff": diff}


@protected.post("/api/runs/{run_id}/baseline")
def set_baseline(run_id: str):
    try:
        store.set_baseline(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}") from None
    return {"ok": True, "baseline_run_id": run_id}


@protected.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    """Cancel a run in flight (roadmap §10.2): already-executed cases keep
    their results; cases not yet started become CANCELED."""
    if not cancel_registry.cancel(run_id):
        raise HTTPException(status_code=404, detail=f"run {run_id} is not running")
    return {"ok": True, "run_id": run_id, "canceled": True}


@protected.get("/api/diff", response_model=DiffSummary)
def get_diff(baseline: str | None = None, candidate: str | None = None, project_id: str = "default"):
    if candidate is None:
        raise HTTPException(status_code=422, detail="query parameter 'candidate' is required")
    candidate_run = store.get_run(candidate)
    if candidate_run is None:
        raise HTTPException(status_code=404, detail=f"run not found: {candidate}")
    baseline_run = store.get_run(baseline) if baseline else store.get_baseline(project_id)
    if baseline is None and baseline_run is None:
        raise HTTPException(
            status_code=404,
            detail=f"no baseline found for project '{project_id}'; set one first",
        )
    if baseline_run["id"] == candidate_run["id"]:
        raise HTTPException(status_code=422, detail="baseline and candidate are the same run")
    return regression.diff_runs(baseline_run, candidate_run)


@protected.get("/api/executions/{execution_id}/trace")
def get_execution_trace(execution_id: str):
    execution = store.get_execution(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail=f"execution not found: {execution_id}")
    return execution


@protected.post("/api/executions/{execution_id}/review")
def review_execution(execution_id: str, req: ReviewRequest):
    """Human review of a critical-rule result (roadmap §8.3 layer 4).

    Records the verdict permanently on the execution; does not change the
    deterministic status — the review trail is kept alongside it.
    """
    if store.get_execution(execution_id) is None:
        raise HTTPException(status_code=404, detail=f"execution not found: {execution_id}")
    review = {
        "verdict": req.verdict,
        "reviewer": req.reviewer,
        "note": req.note,
        "reviewed_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
    }
    store.add_review(execution_id, review)
    return {"ok": True, "review": review}


app.include_router(protected)

# Dashboard agent panel (v1 design §8.9): token check (401) first, then the
# enablement check (403); shares this process's Store.
agent_api.bind_store(store)
app.include_router(agent_api.router)
