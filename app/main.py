import json
import re
import logging
import os
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
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
    RunDetail, RunSummary, ProjectValidateRequest, ProjectVerifyRequest, ProjectDraftRequest,
)
from .project import (PROJECT_CONFIG_ENV, Project, case_count,
                      project_config_path, release_run, try_acquire_run, run_project_unlocked, verify_project)
from .presenters import run_summary, validate_report, verify_view, diff_views
from .exporters import build_junit, build_json
from .drafts import server_draft
from .web_presenters import web_payload
from .storage import Store

if os.getenv("SPECAGENT_SKIP_DOTENV") != "1":
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


@app.exception_handler(RequestValidationError)
async def project_request_error(request: Request, exc: RequestValidationError):
    route_path = getattr(request.scope.get("route"), "path", request.url.path)
    if route_path in {"/api/project/runs", "/api/project/validate",
                      "/api/project/verify", "/api/project/draft",
                      "/api/runs/{run_id}/export"}:
        # Pydantic normally echoes inputs; paths/credentials are unnecessary in
        # field-level diagnostics. Keep legacy endpoint behavior unchanged.
        errors = [{k: v for k, v in error.items() if k not in {"input", "ctx"}}
                  for error in exc.errors()]
        return JSONResponse(status_code=422, content=web_payload({"detail": errors}))
    return await request_validation_exception_handler(request, exc)


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
    return {"run": _run_detail(store.get_run(run_id)), "diff": diff,
            "diff_views": diff_views(diff)}


@protected.get("/api/runs", response_model=list[RunSummary])
def list_runs(project_id: str | None = None, limit: int = Query(default=50, ge=1, le=200)):
    return store.list_runs(project_id, limit)


# -- server-configured project (方案 B): the dashboard runs what the server's
#    SPECAGENT_PROJECT_CONFIG points at; the browser never picks the spec,
#    adapter, project or endpoint ----------------------------------------------


def _project_config_errors(exc: SpecValidationError) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"error": "project_config_invalid", "errors": web_payload(list(exc.errors))},
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
        return {"configured": False, "mode": "error", "errors": web_payload(list(exc.errors))}


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


@contextmanager
def _project_operation():
    lock = try_acquire_run(project_config_path())
    if lock is None:
        raise HTTPException(409, detail="project_run_in_progress: a run for this project is already in flight")
    try:
        yield
    except SpecValidationError as exc:
        raise _project_config_errors(exc) from None
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, detail=web_payload({
            "error": "project_operation_failed", "errors": [f"adapter.agent: {type(exc).__name__}: {exc}"]})) from None
    finally:
        release_run(lock)


@protected.post("/api/project/runs", dependencies=[Depends(_project_run_guard)])
async def run_project_suite(req: ProjectRunRequest):
    with _project_operation():
        project = _load_server_project()
        warnings = []
        outcome = await run_project_unlocked(
            project, label=req.label, baseline=req.baseline,
            set_baseline=req.set_baseline, llm_expand=req.llm_expand,
            cancel_registry=cancel_registry,
            announce=lambda code, text: warnings.append(text) if code == 0 else None)
        gate = {"failed": bool(outcome.gate), "fail_on": list(project.gate_fail_on),
                "violations": outcome.gate}
        summary = run_summary(project.project_id, outcome.run, outcome.diff, gate,
                              set_baseline=req.set_baseline)
        return web_payload({"run": _run_detail(outcome.run), "diff": outcome.diff,
                            "gate": gate, "summary": summary, "warnings": warnings})


@protected.post("/api/project/validate", dependencies=[Depends(_project_run_guard)])
def validate_project_endpoint(req: ProjectValidateRequest):
    with _project_operation():
        report = validate_report(project_config_path())
        return web_payload(report.to_dict())


@protected.post("/api/project/verify", dependencies=[Depends(_project_run_guard)])
def verify_project_endpoint(req: ProjectVerifyRequest):
    # Sync endpoints run in FastAPI's threadpool: verify_project may asyncio.run.
    with _project_operation():
        project = _load_server_project()
        suggestion = None
        if req.suggestion:
            path = project.root / ".specagent" / "suggestions" / req.suggestion / "suggestion.json"
            # Do not follow a symlink outside the configured project.
            if not path.resolve().is_relative_to(project.root.resolve()):
                raise SpecValidationError(["suggestion: path must stay inside the project"])
            if not path.is_file():
                raise SpecValidationError([f"suggestion not found: {req.suggestion}"])
            suggestion = json.loads(path.read_text(encoding="utf-8"))
        return web_payload(verify_view(verify_project(
            project, pre_run_id=req.pre_run_id, suggestion=suggestion, _lock_held=True)))


@protected.post("/api/project/draft", dependencies=[Depends(_project_run_guard)])
def draft_project_endpoint(req: ProjectDraftRequest):
    with _project_operation():
        _load_server_project()
        draft = server_draft(req.text)
        return web_payload({k: draft[k] for k in ("yaml", "compiler", "warnings")})


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


def _run_and_diff(run_id: str):
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(404, detail=web_payload(f"run not found: {run_id}"))
    baseline = store.get_baseline(run["project_id"])
    diff = regression.diff_runs(baseline, run) if baseline and baseline["id"] != run_id else None
    return run, diff


@protected.get("/api/runs/{run_id}/triage")
def get_triage(run_id: str):
    from .agent.triage import triage_run
    run, diff = _run_and_diff(run_id)
    return web_payload(triage_run(run, diff))


def _download(content: str, run_id: str, extension: str, media_type: str):
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", run_id)[:120]
    # Match CLI --out bytes, including native newlines on Windows.
    return Response(content.replace("\n", os.linesep).encode("utf-8"), media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="specagent-{safe_id}.{extension}"',
                             "X-Content-Type-Options": "nosniff"})


@protected.get("/api/runs/{run_id}/export")
def export_run(run_id: str, format: str = Query(default="junit", pattern="^(junit|json)$")):
    run, diff = _run_and_diff(run_id)
    run = web_payload(run)
    diff = web_payload(diff) if diff else None
    content = build_junit(run, diff) if format == "junit" else build_json(run, diff)
    return _download(content, run_id, "xml" if format == "junit" else "json",
                     "application/xml" if format == "junit" else "application/json")


@protected.get("/api/runs/{run_id}/report.html")
def download_report(run_id: str):
    from .metrics import compute_run_metrics
    from .report import build_html_report
    from .models import DiffSummary
    run, diff = _run_and_diff(run_id)
    run = web_payload(run)
    diff = DiffSummary.model_validate(web_payload(diff)) if diff else None
    return _download(build_html_report(run, diff, compute_run_metrics(run)), run_id, "html", "text/html")


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


@protected.get("/api/diff")
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
    if baseline_run is None:
        raise HTTPException(404, detail=f"run not found: {baseline}")
    if baseline_run["id"] == candidate_run["id"]:
        raise HTTPException(status_code=422, detail="baseline and candidate are the same run")
    diff = regression.diff_runs(baseline_run, candidate_run)
    return {**diff.model_dump(), "views": diff_views(diff)}


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
