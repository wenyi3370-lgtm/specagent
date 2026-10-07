"""方案 B: the web dashboard runs the server-configured project
(GET /api/project, POST /api/project/runs).

Access control, the strict request body, gate parity with the CLI, the
in-process run lock and mid-flight cancellation. TestClient only — no
network; agents are offline python adapters whose temp modules carry unique
names so sys.modules cannot leak between tests (known-issues U12).
"""
import json
import threading
import time
import uuid

from fastapi.testclient import TestClient

from app import main, project as project_mod
from app.project import Project, release_run, try_acquire_run
from test_api import _SlowAdapter

TOKEN = "proj-run-token-4c2e"
FIXED_MODULE = "web_run_agent_fixed"
BROKEN_MODULE = "web_run_agent_broken"

_CONFIG_TAIL = (
    "spec: specs/behavior.yaml\n"
    "run:\n  concurrency: 4\n  timeout_seconds: 30\n  repeat: 1\n"
    "gate:\n  fail_on: [critical, high]\n"
)


def _loopback_client(host="127.0.0.1", host_header="127.0.0.1:8765"):
    """TCP peer and Host header both loopback — the no-token mode checks both
    (see test_agent_api._loopback_client; the default Host is 'testserver')."""
    return TestClient(main.app, client=(host, 50000), headers={"Host": host_header})


def _write_project(tmp_path, *, agent_module, project_id=None):
    """Copy the FinCare spec + agent(s) into a temp dir (repo examples stay
    untouched) and return (config_path, project_id). Both agent variants are
    always written so a test can flip the config to the broken module."""
    root = tmp_path / ("proj-" + uuid.uuid4().hex[:8])
    (root / "specs").mkdir(parents=True)
    src = main.BASE.parent / "examples" / "fincare-agent"
    (root / "specs" / "behavior.yaml").write_text(
        (src / "specs" / "behavior.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    for module in (FIXED_MODULE, BROKEN_MODULE):
        module_src = "agent_fixed.py" if module == FIXED_MODULE else "agent.py"
        (root / f"{module}.py").write_text(
            (src / module_src).read_text(encoding="utf-8"), encoding="utf-8")
    pid = project_id or f"fincare-web-{uuid.uuid4().hex[:8]}"
    cfg = root / "specagent.yaml"
    cfg.write_text(
        f"project: {pid}\n"
        "adapter:\n"
        "  type: python\n"
        f"  agent: {agent_module}:run_agent\n" + _CONFIG_TAIL,
        encoding="utf-8")
    return cfg, pid


def _post_run(client, label="web run"):
    return client.post("/api/project/runs",
                       json={"label": label})


# -- GET /api/project -----------------------------------------------------------


def test_get_project_demo_mode_when_unconfigured(tmp_path, monkeypatch):
    monkeypatch.delenv("SPECAGENT_PROJECT_CONFIG", raising=False)
    r = TestClient(main.app).get("/api/project")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert body["mode"] == "demo"
    assert "specagent.yaml" in body["reason"] and "SPECAGENT_PROJECT_CONFIG" in body["reason"]
    # no filesystem layout leaks
    assert "\\" not in r.text and str(tmp_path) not in r.text


def test_get_project_configured(tmp_path, monkeypatch):
    cfg, pid = _write_project(tmp_path, agent_module=FIXED_MODULE)
    monkeypatch.setenv("SPECAGENT_PROJECT_CONFIG", str(cfg))
    r = TestClient(main.app).get("/api/project")
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "configured": True, "project_id": pid, "mode": "project",
        "adapter": {"type": "python", "label": f"python:{FIXED_MODULE}:run_agent"},
        "spec": {"rules": 5, "cases": 43},
        "gate": {"fail_on": ["critical", "high"]},
        "run": {"concurrency": 4, "timeout_seconds": 30, "repeat": 1},
    }
    # counts and labels only — never the config path or spec filename
    assert str(tmp_path) not in r.text and "behavior.yaml" not in r.text


def test_get_project_invalid_config(tmp_path, monkeypatch):
    root = tmp_path / "bad-proj"
    (root / "specs").mkdir(parents=True)
    (root / "specs" / "behavior.yaml").write_text("rules: []\n", encoding="utf-8")
    monkeypatch.setenv("SPECAGENT_PROJECT_CONFIG", str(root / "specagent.yaml"))
    # missing the required `spec` key
    (root / "specagent.yaml").write_text("project: bad\n", encoding="utf-8")
    r = TestClient(main.app).get("/api/project")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False and body["mode"] == "error"
    assert body["errors"] and all(isinstance(e, str) for e in body["errors"])
    # unknown top-level field is rejected too (extra="forbid")
    (root / "specagent.yaml").write_text(
        "project: bad\nspec: specs/behavior.yaml\nbogus: true\n", encoding="utf-8")
    body = TestClient(main.app).get("/api/project").json()
    assert body["mode"] == "error" and any("bogus" in e for e in body["errors"])


def test_get_project_http_label_shows_host_only(tmp_path, monkeypatch):
    root = tmp_path / "http-proj"
    (root / "specs").mkdir(parents=True)
    (root / "specs" / "behavior.yaml").write_text(
        (main.BASE.parent / "examples" / "fincare-agent" / "specs" / "behavior.yaml")
        .read_text(encoding="utf-8"), encoding="utf-8")
    cfg = root / "specagent.yaml"
    cfg.write_text(
        "project: http-proj\nadapter:\n  type: http\n" + _CONFIG_TAIL, encoding="utf-8")
    monkeypatch.setenv("SPECAGENT_PROJECT_CONFIG", str(cfg))
    monkeypatch.setenv("TARGET_AGENT_URL", "http://user:secret@agent.example.com:9000/v1")
    body = TestClient(main.app).get("/api/project").json()
    assert body["configured"] is True
    assert body["adapter"] == {"type": "http", "label": "http:agent.example.com"}
    # credentials, port and path of the URL never reach the response
    text = json.dumps(body)
    for needle in ("secret", "user:", "9000", "/v1"):
        assert needle not in text


# -- POST /api/project/runs ------------------------------------------------------


def test_project_run_fixed_all_pass(tmp_path, monkeypatch):
    cfg, pid = _write_project(tmp_path, agent_module=FIXED_MODULE)
    monkeypatch.setenv("SPECAGENT_PROJECT_CONFIG", str(cfg))
    r = _post_run(_loopback_client(), "web fixed")
    assert r.status_code == 200, r.text
    body = r.json()
    run = body["run"]
    assert run["project_id"] == pid
    assert run["agent"] == f"python:{FIXED_MODULE}:run_agent"
    assert run["label"] == "web fixed"
    assert run["status"] == "completed"
    assert run["passed"] == run["total"] == 43
    # no baseline for a fresh project: not evaluated, nothing failed
    assert body["diff"] is None
    assert body["gate"]["failed"] is False
    assert body["gate"]["fail_on"] == ["critical", "high"]
    assert body["gate"]["violations"] == []


def test_project_run_gate_failed_matches_cli(tmp_path, monkeypatch):
    root_cfg, pid = _write_project(tmp_path, agent_module=FIXED_MODULE)
    monkeypatch.setenv("SPECAGENT_PROJECT_CONFIG", str(root_cfg))
    c = _loopback_client()
    first = _post_run(c).json()
    assert c.post(f"/api/runs/{first['run']['id']}/baseline").status_code == 200

    # same project dir/spec, only the agent module flips to the defective one
    broken_cfg = root_cfg.parent / "specagent.broken.yaml"
    broken_cfg.write_text(
        (root_cfg.read_text(encoding="utf-8"))
        .replace(f"agent: {FIXED_MODULE}:run_agent", f"agent: {BROKEN_MODULE}:run_agent"),
        encoding="utf-8")
    monkeypatch.setenv("SPECAGENT_PROJECT_CONFIG", str(broken_cfg))

    r = _post_run(c, "web broken")
    assert r.status_code == 200, r.text
    body = r.json()
    gate = body["gate"]
    assert gate["failed"] is True
    assert gate["fail_on"] == ["critical", "high"]
    # documented CLI result for this pair: 4 new regressions on two critical rules
    assert body["diff"]["new_regressions"] == 4
    assert len(gate["violations"]) == 4
    assert {v["diff_type"] for v in gate["violations"]} == {"NEW_REGRESSION"}
    assert {v["rule_id"] for v in gate["violations"]} == {
        "LARGE_TRANSFER_APPROVAL", "ACCOUNT_SCOPE"}


def test_run_body_rejects_extra_fields_and_wrong_content_type(tmp_path, monkeypatch):
    cfg, _pid = _write_project(tmp_path, agent_module=FIXED_MODULE)
    monkeypatch.setenv("SPECAGENT_PROJECT_CONFIG", str(cfg))
    c = _loopback_client()
    url = "/api/project/runs"
    # the browser may never choose what runs (方案 B non-goals)
    for field, value in (("spec", {"rules": []}), ("agent", "demo"),
                         ("project_id", "other"), ("set_baseline", True),
                         ("endpoint", "http://evil.example")):
        assert c.post(url, json={field: value}).status_code == 422, field
    # missing body and wrong content type are both 422 (CSRF guard + parsing)
    assert c.post(url).status_code == 422
    assert c.post(url, content=json.dumps({}),
                  headers={"Content-Type": "text/plain"}).status_code == 422


def test_run_requires_token_when_configured(tmp_path, monkeypatch):
    cfg, _pid = _write_project(tmp_path, agent_module=FIXED_MODULE)
    monkeypatch.setenv("SPECAGENT_PROJECT_CONFIG", str(cfg))
    monkeypatch.setenv("SPECAGENT_API_TOKEN", TOKEN)
    c = TestClient(main.app)
    assert c.get("/api/project").status_code == 401
    assert c.post("/api/project/runs", json={}).status_code == 401
    authed = TestClient(main.app, headers={"Authorization": f"Bearer {TOKEN}"})
    assert authed.get("/api/project").status_code == 200
    # with a token the loopback-Host rule no longer applies
    assert _post_run(authed).status_code == 200


def test_run_blocks_non_loopback_host_without_token(tmp_path, monkeypatch):
    cfg, _pid = _write_project(tmp_path, agent_module=FIXED_MODULE)
    monkeypatch.setenv("SPECAGENT_PROJECT_CONFIG", str(cfg))
    monkeypatch.delenv("SPECAGENT_API_TOKEN", raising=False)
    r = TestClient(main.app).post("/api/project/runs", json={})
    assert r.status_code == 403
    assert r.json()["detail"].startswith("project_run_requires_token")
    # a genuine loopback client gets through (200 — the run itself)
    assert _post_run(_loopback_client()).status_code == 200


def test_second_run_while_in_flight_is_409(tmp_path, monkeypatch):
    cfg, _pid = _write_project(tmp_path, agent_module=FIXED_MODULE)
    monkeypatch.setenv("SPECAGENT_PROJECT_CONFIG", str(cfg))
    lock = try_acquire_run(str(cfg))
    assert lock is not None
    try:
        r = _post_run(_loopback_client())
        assert r.status_code == 409
        assert r.json()["detail"].startswith("project_run_in_progress")
    finally:
        release_run(lock)
    # released: the next run goes through instead of queueing
    assert _post_run(_loopback_client()).status_code == 200


def test_project_run_can_be_canceled_midflight(tmp_path, monkeypatch):
    cfg, pid = _write_project(tmp_path, agent_module=FIXED_MODULE)
    monkeypatch.setenv("SPECAGENT_PROJECT_CONFIG", str(cfg))
    monkeypatch.setattr(Project, "make_adapter", lambda self: _SlowAdapter())
    result: dict = {}

    def start_run():
        with TestClient(main.app, client=("127.0.0.1", 50000),
                        headers={"Host": "127.0.0.1:8765"}) as tc:
            result["response"] = _post_run(tc, "web slow")

    thread = threading.Thread(target=start_run)
    thread.start()
    run_id = None
    deadline = time.time() + 10
    while time.time() < deadline and run_id is None:
        running = [x for x in main.store.list_runs(pid, limit=5) if x["status"] == "running"]
        if running:
            run_id = running[0]["id"]
            break
        time.sleep(0.05)
    assert run_id, "project run never appeared as 'running'"
    time.sleep(0.4)  # let a couple of cases complete
    assert _loopback_client().post(f"/api/runs/{run_id}/cancel").json()["canceled"] is True
    thread.join(timeout=30)
    run = result["response"].json()["run"]
    assert run["id"] == run_id
    assert run["status"] == "canceled"
    statuses = [x["status"] for x in run["results"]]
    assert "CANCELED" in statuses


# -- /api/health stays backward compatible ---------------------------------------


def test_health_gains_only_project_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("SPECAGENT_PROJECT_CONFIG", raising=False)
    c = TestClient(main.app)
    body = c.get("/api/health").json()
    for key in ("ok", "version", "compiler", "agent", "db", "auth_required",
                "agent_enabled"):
        assert key in body, key
    assert body["project_configured"] is False
    cfg, _pid = _write_project(tmp_path, agent_module=FIXED_MODULE)
    monkeypatch.setenv("SPECAGENT_PROJECT_CONFIG", str(cfg))
    assert c.get("/api/health").json()["project_configured"] is True


def test_dashboard_shows_visible_target_agent_label():
    """The project bar carries a *visible* "Target agent" label, not only an
    aria-label, so users can find where the tested agent is shown."""
    html = TestClient(main.app).get("/").text
    assert 'id="projectBarLabel"' in html
    assert ">Target agent</span>" in html
    assert html.index('id="projectBarLabel"') < html.index('id="projectBarText"')
