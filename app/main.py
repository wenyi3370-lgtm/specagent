import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, orchestrator, regression
from .adapters import resolve_adapter
from .adapters.base import AgentAdapter
from .compiler import compile_spec
from .errors import SpecValidationError
from .generator import generate_tests
from .models import (
    BehaviorSpec, CompileRequest, CreateRunRequest, DiffSummary, LLMJudgeVerdict,
    ReviewRequest, RunAllResponse, RunDetail, RunSummary,
)
from .storage import Store

load_dotenv()
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("specagent.api")

BASE = Path(__file__).resolve().parent
app = FastAPI(title="SpecAgent", version=__version__)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

store = Store()


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
        "db": store.db_path,
    }


@app.post("/api/compile")
@app.post("/api/specs/compile")
def compile_endpoint(req: CompileRequest):
    return compile_spec(req.text)


def _adapter_for(agent: str, agent_variant: str | None) -> AgentAdapter:
    try:
        return resolve_adapter(agent=agent, agent_variant=agent_variant)
    except SpecValidationError as exc:
        raise HTTPException(status_code=422, detail="; ".join(exc.errors)) from None


@app.post("/api/run-all", response_model=RunAllResponse)
async def run_all(req: CompileRequest):
    """Legacy v0.1 endpoint: compile from text, execute, and return everything.

    Kept for backward compatibility; it now also persists the run and diffs
    against the project baseline.
    """
    spec = compile_spec(req.text)
    tests = generate_tests(spec)
    run_id, results, diff = await orchestrator.run_with_diff(
        store, project_id="default", spec=spec, tests=tests,
        adapter=_adapter_for("auto", None),
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
        id=run["id"], project_id=run["project_id"], label=run.get("label", ""),
        spec_compiler=run.get("spec_compiler", ""), status=run["status"],
        is_baseline=run["is_baseline"], started_at=run["started_at"],
        completed_at=run.get("completed_at") or "",
        passed=run["passed"], failed=run["failed"], errors=run["errors"],
        total=run["total"], score=run["score"], commit_sha=run.get("commit_sha"),
        agent=run.get("agent", ""),
        spec=BehaviorSpec.model_validate(run.get("spec", {})),
        tests=[TestCase.model_validate(t) for t in run.get("tests", [])],
        results=results,
    )


@app.post("/api/runs")
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
        set_baseline=req.set_baseline,
    )
    return {"run": _run_detail(store.get_run(run_id)), "diff": diff}


@app.get("/api/runs", response_model=list[RunSummary])
def list_runs(project_id: str | None = None, limit: int = Query(default=50, ge=1, le=200)):
    return store.list_runs(project_id, limit)


@app.get("/api/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: str):
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    return _run_detail(run)


@app.get("/api/runs/{run_id}/report")
def get_report(run_id: str):
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    baseline = store.get_baseline(run["project_id"])
    diff = regression.diff_runs(baseline, run) if baseline and baseline["id"] != run_id else None
    return {"run": run, "diff": diff}


@app.post("/api/runs/{run_id}/baseline")
def set_baseline(run_id: str):
    try:
        store.set_baseline(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}") from None
    return {"ok": True, "baseline_run_id": run_id}


@app.get("/api/diff", response_model=DiffSummary)
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


@app.get("/api/executions/{execution_id}/trace")
def get_execution_trace(execution_id: str):
    execution = store.get_execution(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail=f"execution not found: {execution_id}")
    return execution


@app.post("/api/executions/{execution_id}/review")
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
