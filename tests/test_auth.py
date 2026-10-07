"""API token authentication tests (v1 design §3.4, task 4)."""
import logging

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import auth, main

TOKEN = "secret-token-1"


def _client():
    return TestClient(main.app)


def test_token_set_401_without_credentials(monkeypatch):
    monkeypatch.setenv("SPECAGENT_API_TOKEN", TOKEN)
    c = _client()
    for path in ("/api/runs", "/api/compile", "/api/metrics"):
        response = c.post(path, json={"text": "x"}) if path == "/api/compile" else c.get(path)
        assert response.status_code == 401, path
        assert response.json()["detail"] == "invalid or missing API token"
        assert response.headers["WWW-Authenticate"] == "Bearer"


def test_token_set_bearer_and_api_key_both_work(monkeypatch):
    monkeypatch.setenv("SPECAGENT_API_TOKEN", TOKEN)
    c = _client()
    ok_bearer = c.get("/api/runs", headers={"Authorization": f"Bearer {TOKEN}"})
    assert ok_bearer.status_code == 200
    ok_key = c.get("/api/runs", headers={"X-API-Key": TOKEN})
    assert ok_key.status_code == 200


def test_token_set_wrong_credentials_401(monkeypatch):
    monkeypatch.setenv("SPECAGENT_API_TOKEN", TOKEN)
    c = _client()
    assert c.get("/api/runs", headers={"Authorization": f"Bearer wrong"}).status_code == 401
    assert c.get("/api/runs", headers={"Authorization": "Basic abc"}).status_code == 401
    assert c.get("/api/runs", headers={"Authorization": "Bearer  "}).status_code == 401
    assert c.get("/api/runs", headers={"X-API-Key": "  "}).status_code == 401


def test_open_endpoints_when_token_set(monkeypatch):
    monkeypatch.setenv("SPECAGENT_API_TOKEN", TOKEN)
    c = _client()
    assert c.get("/api/health").status_code == 200
    assert c.get("/").status_code == 200
    assert c.get("/static/index.html").status_code == 200


def test_token_unset_behaves_as_before(monkeypatch):
    monkeypatch.delenv("SPECAGENT_API_TOKEN", raising=False)
    c = _client()
    # one existing-style flow end to end, unauthenticated
    assert c.get("/api/health").json()["auth_required"] is False
    assert c.post("/api/compile", json={"text": "退款超过500元需要人工审批"}).status_code == 200
    run = c.post("/api/runs", json={
        "text": "电商客服Agent。退款超过500元需要人工审批。",
        "project_id": "auth-local-mode", "agent": "demo", "agent_variant": "patched",
    })
    assert run.status_code == 200
    assert run.json()["run"]["passed"] == run.json()["run"]["total"]


def test_startup_warning_logged_when_token_unset(monkeypatch, caplog):
    monkeypatch.delenv("SPECAGENT_API_TOKEN", raising=False)
    with caplog.at_level(logging.WARNING, logger="specagent.auth"):
        auth.log_startup_warning(logging.getLogger("specagent.auth"))
    assert "SPECAGENT_API_TOKEN is not set" in caplog.text
    assert TOKEN not in caplog.text


def test_no_warning_when_token_configured(monkeypatch, caplog):
    monkeypatch.setenv("SPECAGENT_API_TOKEN", TOKEN)
    with caplog.at_level(logging.WARNING, logger="specagent.auth"):
        auth.log_startup_warning(logging.getLogger("specagent.auth"))
    assert "SPECAGENT_API_TOKEN is not set" not in caplog.text


def test_health_does_not_disclose_db_url(monkeypatch):
    monkeypatch.delenv("SPECAGENT_API_TOKEN", raising=False)
    monkeypatch.setattr(main.store, "db_url",
                        "postgresql+psycopg://admin:secret@db.internal/specagent")
    body = _client().get("/api/health").json()
    assert body["db"] == "postgresql"
    text = str(body)
    for needle in ("secret", "admin", "db.internal", "psycopg"):
        assert needle not in text
    assert body["auth_required"] is False
    assert body["agent_enabled"] is False


def test_backend_name_table():
    from app.storage import backend_name
    assert backend_name("sqlite:///D:/x/specagent.db") == "sqlite"
    assert backend_name("sqlite://") == "sqlite"
    assert backend_name("postgresql+psycopg://admin:secret@db.internal/specagent") == "postgresql"
    assert backend_name("postgresql://u:p@h/db") == "postgresql"


def test_store_backend_property(tmp_path):
    from app.storage import Store
    store = Store(str(tmp_path / "b.db"))
    assert store.backend == "sqlite"


def test_health_reports_auth_required_when_token_set(monkeypatch):
    monkeypatch.setenv("SPECAGENT_API_TOKEN", TOKEN)
    body = _client().get("/api/health").json()
    assert body["auth_required"] is True
    assert TOKEN not in str(body)


def test_route_guard_covers_every_api_route():
    """Fail-closed: every /api/* route except /api/health must depend on
    require_api_token; /api/agent/* routes (added in task 16) must also depend
    on require_agent_enabled. A future unprotected route fails the suite."""
    def iter_deps(dep):
        yield dep
        for sub in dep.dependencies:
            yield from iter_deps(sub)

    checked = 0
    for route in main.app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/"):
            continue
        if route.path == "/api/health":
            continue
        calls = {d.call for d in iter_deps(route.dependant)}
        assert auth.require_api_token in calls, f"unprotected route: {route.path}"
        if route.path.startswith("/api/agent/"):
            assert auth.require_agent_enabled in calls, route.path
        checked += 1
    assert checked >= 15  # all current /api/* routes (health excluded)


def test_index_html_uses_session_storage_only():
    html = (main.BASE / "static" / "index.html").read_text(encoding="utf-8")
    assert "sessionStorage" in html
    assert "showTokenPrompt" in html
    assert "localStorage" not in html
