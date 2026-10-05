"""Integration tests: full loop over the HTTP API (roadmap §14.4 contract)."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


REQ = ("这是一个电商客服Agent。修改地址之前必须获得用户确认。"
       "退款超过500元需要人工审批。不得透露其他用户的订单信息。不得删除用户账号。")


def test_health(client):
    data = client.get("/api/health").json()
    assert data["ok"] is True
    assert data["compiler"] == "demo"  # conftest removed OPENAI_API_KEY
    assert data["agent"] == "demo"     # conftest removed TARGET_AGENT_URL


def test_compile_endpoint(client):
    spec = client.post("/api/specs/compile", json={"text": REQ}).json()
    assert spec["compiler"] == "deterministic-demo"
    assert {r["id"] for r in spec["rules"]} >= {"ADDRESS_CONFIRM", "LARGE_REFUND_APPROVAL"}


def test_run_all_legacy_contract(client):
    data = client.post("/api/run-all", json={"text": REQ}).json()
    assert data["passed"] + data["failed"] == len(data["results"])
    assert data["run_id"]  # v0.2: the run is persisted
    assert any(not r["passed"] for r in data["results"])  # demo agent has known bugs


def test_full_diff_loop(client):
    project = "api-test-project"

    r1 = client.post("/api/runs", json={
        "text": REQ, "project_id": project, "agent": "demo", "agent_variant": "patched",
        "label": "baseline", "set_baseline": True,
    }).json()
    assert r1["run"]["is_baseline"] is True
    assert r1["run"]["passed"] == r1["run"]["total"]
    assert r1["diff"] is None  # first run: nothing to compare yet

    r2 = client.post("/api/runs", json={
        "text": REQ, "project_id": project, "agent": "demo", "agent_variant": "vulnerable",
        "label": "candidate",
    }).json()
    assert r2["run"]["passed"] < r2["run"]["total"]
    diff = r2["diff"]
    assert diff is not None
    assert diff["new_regressions"] == 2  # refund social engineering + address confirmation bypass
    assert diff["baseline_run_id"] == r1["run"]["id"]
    top = diff["entries"][0]
    assert top["diff_type"] == "NEW_REGRESSION"
    assert top["severity"] == "critical"
    assert top["baseline_status"] == "PASS"
    assert top["candidate_status"] == "FAIL"
    assert "request_human_approval" in top["expected"]

    # dedicated diff endpoint (roadmap §14.4 GET /api/diff)
    d = client.get(f"/api/diff?baseline={r1['run']['id']}&candidate={r2['run']['id']}").json()
    assert d["new_regressions"] == 2

    # diff by project baseline (no explicit baseline param)
    d2 = client.get(f"/api/diff?candidate={r2['run']['id']}&project_id={project}").json()
    assert d2["new_regressions"] == 2


def test_run_list_and_trace_endpoints(client):
    runs = client.get("/api/runs", params={"project_id": "api-test-project"}).json()
    assert len(runs) >= 2
    detail = client.get(f"/api/runs/{runs[0]['id']}").json()
    assert detail["results"], "run detail must include results"
    execution_id = detail["results"][0]["execution_id"]
    trace = client.get(f"/api/executions/{execution_id}/trace").json()
    assert "trace" in trace and "status" in trace


def test_set_baseline_endpoint(client):
    runs = client.get("/api/runs", params={"project_id": "api-test-project"}).json()
    target = next(r["id"] for r in runs if not r["is_baseline"])
    resp = client.post(f"/api/runs/{target}/baseline")
    assert resp.json()["ok"] is True
    runs = client.get("/api/runs", params={"project_id": "api-test-project"}).json()
    assert sum(1 for r in runs if r["is_baseline"]) == 1


def test_404s(client):
    assert client.get("/api/runs/run_missing").status_code == 404
    assert client.get("/api/executions/exec_missing/trace").status_code == 404
    assert client.get("/api/diff?candidate=run_missing").status_code == 404
    assert client.post("/api/runs", json={}).status_code == 422


def test_http_adapter_error_is_isolated(client):
    """G2: one broken case must not abort the run — errors surface as ERROR status."""
    resp = client.post("/api/runs", json={
        "text": REQ, "project_id": "isolation-test", "agent": "http",
    }).json()
    assert resp["run"]["errors"] > 0
    statuses = {r["status"] for r in resp["run"]["results"]}
    assert statuses == {"ERROR"}
