import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

from .compiler import compile_spec
from .generator import generate_tests
from .judge import judge
from .models import CompileRequest, RunAllResponse
from .agents.demo import run_demo_agent
from .agents.http_agent import run_http_agent

load_dotenv()
BASE = Path(__file__).resolve().parent

app = FastAPI(title="SpecAgent", version="0.1.0")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


@app.get("/")
def home():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "version": "0.1.0",
        "compiler": "openai" if os.getenv("OPENAI_API_KEY") else "demo",
        "agent": "external" if os.getenv("TARGET_AGENT_URL") else "demo",
    }


@app.post("/api/compile")
def compile_endpoint(req: CompileRequest):
    return compile_spec(req.text)


@app.post("/api/run-all", response_model=RunAllResponse)
async def run_all(req: CompileRequest):
    spec = compile_spec(req.text)
    tests = generate_tests(spec)
    results = []
    for case in tests:
        execution = (
            await run_http_agent(case.user_input)
            if os.getenv("TARGET_AGENT_URL")
            else run_demo_agent(case.user_input)
        )
        results.append(judge(case, execution))

    passed = sum(r.passed for r in results)
    failed = len(results) - passed
    score = round((passed / len(results) * 100) if results else 0, 1)
    return RunAllResponse(
        spec=spec, tests=tests, results=results,
        passed=passed, failed=failed, score=score,
    )
