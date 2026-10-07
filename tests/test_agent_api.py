"""Dashboard agent API (v1 design §8.9, task 16): access control, the
parked-action protocol over HTTP, offline mode, session table limits and the
dashboard panel markup. Scripted fake LLM clients only — no network."""
import json
import logging
import re
import threading
import time
import uuid

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import agent_api, auth, main
from app.agent.tools import DRAFT_MARKER
from test_agent_loop import _FakeClient, _FakeResponse, _call, _message
from test_agent_tools import CONFIG, SPEC, _write_agent_draft

TOKEN = "dash-test-token-7f3a"
PENDING_KEYS = {"action_id", "tool", "human_only", "summary", "preview"}


@pytest.fixture(autouse=True)
def _fresh_sessions():
    agent_api.reset_sessions()
    yield
    agent_api.reset_sessions()


def _project(tmp_path, monkeypatch, config_extra=""):
    """A temp project whose config the server reads from SPECAGENT_PROJECT_CONFIG;
    a unique project id keeps run counts independent in the shared test Store."""
    pid = f"api-{uuid.uuid4().hex[:8]}"
    (tmp_path / "specs").mkdir(exist_ok=True)
    (tmp_path / "specs" / "behavior.yaml").write_text(SPEC, encoding="utf-8")
    cfg = tmp_path / "specagent.yaml"
    cfg.write_text(CONFIG.replace("project: t", f"project: {pid}") + config_extra,
                   encoding="utf-8")
    monkeypatch.setenv("SPECAGENT_PROJECT_CONFIG", str(cfg))
    return pid


def _runs(pid):
    return len(main.store.list_runs(pid, limit=200))


def _authed(monkeypatch):
    monkeypatch.setenv("SPECAGENT_API_TOKEN", TOKEN)
    return TestClient(main.app, headers={"Authorization": f"Bearer {TOKEN}"})


def _fake(monkeypatch, turns):
    client = _FakeClient(turns)
    monkeypatch.setattr(agent_api, "client_factory", lambda: client)
    return client


def _offline(monkeypatch):
    monkeypatch.setattr(agent_api, "client_factory", lambda: None)


def _session(c):
    r = c.post("/api/agent/sessions", json={})
    assert r.status_code == 200, r.text
    return r.json()


def _say(c, sid, text="please run the suite"):
    return c.post(f"/api/agent/sessions/{sid}/messages", json={"text": text})


def _approve(c, sid, action_id, approve=True):
    return c.post(f"/api/agent/sessions/{sid}/approve",
                  json={"action_id": action_id, "approve": approve})


def _outputs(messages):
    return {item["call_id"]: json.loads(item["output"]) for item in messages
            if isinstance(item, dict) and item.get("type") == "function_call_output"}


# -- access control -------------------------------------------------------------------


def test_agent_api_403_without_token(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    _offline(monkeypatch)
    c = TestClient(main.app)
    responses = [
        c.post("/api/agent/sessions", json={}),
        c.post("/api/agent/sessions/dash-x/messages", json={"text": "hi"}),
        c.post("/api/agent/sessions/dash-x/approve", json={"action_id": "a", "approve": True}),
    ]
    for r in responses:
        assert r.status_code == 403, r.text
        assert r.json()["detail"].startswith("agent_api_requires_token")
    assert agent_api._sessions == {}
    assert c.get("/api/health").json()["agent_enabled"] is False


def _loopback_client(host="127.0.0.1", host_header="127.0.0.1:8765"):
    """A TestClient whose TCP peer *and* Host header are loopback.

    The opt-in mode checks both (DNS rebinding keeps the attacker's Host), so
    tests must set the Host header explicitly — the default is "testclient".
    The Host header is set by hand because starlette's TestClient cannot parse
    an IPv6 base_url.
    """
    return TestClient(main.app, client=(host, 50000),
                      headers={"Host": host_header})


def test_insecure_optin_loopback_works_and_warns(tmp_path, monkeypatch, caplog):
    pid = _project(tmp_path, monkeypatch)
    _offline(monkeypatch)
    monkeypatch.setenv("SPECAGENT_AGENT_API_INSECURE", "1")
    c = _loopback_client()
    with caplog.at_level(logging.WARNING, logger="specagent.auth"):
        body = _session(c)
    assert body["mode"] == "offline" and body["project"] == pid
    assert "agent API used WITHOUT authentication (loopback opt-in)" in caplog.text
    assert c.get("/api/health").json()["agent_enabled"] is True
    assert _loopback_client("::1", "[::1]:8765").post(
        "/api/agent/sessions", json={}).status_code == 200


def test_insecure_optin_host_header_must_be_loopback(tmp_path, monkeypatch):
    """A rebound attacker keeps its own Host, so a loopback peer is not enough
    (review #4). Without this check a hostile page could reach approve."""
    _project(tmp_path, monkeypatch)
    _offline(monkeypatch)
    monkeypatch.setenv("SPECAGENT_AGENT_API_INSECURE", "1")
    for host_header in ("evil.example", "localhost.evil.com:8765",
                        "127.0.0.1.evil.com", "192.168.1.20:8765", "",
                        "[::2]:8765", "127.0.0.1:notaport"):
        c = _loopback_client("127.0.0.1", host_header)
        for path, body in (("/api/agent/sessions", {}),
                           ("/api/agent/sessions/dash-x/messages", {"text": "hi"}),
                           ("/api/agent/sessions/dash-x/approve",
                            {"action_id": "a", "approve": True})):
            r = c.post(path, json=body)
            assert r.status_code == 403, (host_header, path)
            assert r.json()["detail"].startswith("agent_api_requires_token")
    assert agent_api._sessions == {}
    # every accepted loopback spelling, with and without a port
    for host_header in ("127.0.0.1:8765", "127.0.0.1", "localhost", "localhost:9999",
                        "[::1]:8765", "[::1]", "::1", "LOCALHOST:1", "LocalHost"):
        assert _loopback_client("127.0.0.1", host_header).post(
            "/api/agent/sessions", json={}).status_code == 200, host_header


def test_insecure_optin_non_loopback_403(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    _offline(monkeypatch)
    monkeypatch.setenv("SPECAGENT_AGENT_API_INSECURE", "1")
    for host in ("testclient", "10.0.0.5", "192.168.1.20"):
        r = TestClient(main.app, client=(host, 50000)).post("/api/agent/sessions", json={})
        assert r.status_code == 403, host
        assert r.json()["detail"].startswith("agent_api_requires_token")
    # anything but exactly "1" is not an opt-in
    monkeypatch.setenv("SPECAGENT_AGENT_API_INSECURE", "true")
    assert _loopback_client().post("/api/agent/sessions", json={}).status_code == 403
    # a configured token needs no Host check: the peer check is skipped entirely
    monkeypatch.setenv("SPECAGENT_AGENT_API_INSECURE", "")
    monkeypatch.setenv("SPECAGENT_API_TOKEN", TOKEN)
    ok = TestClient(main.app, headers={"Authorization": f"Bearer {TOKEN}",
                                       "Host": "dashboard.example"}).post(
        "/api/agent/sessions", json={})
    assert ok.status_code == 200


def test_token_set_401_without_credentials_200_with_bearer_or_api_key(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    _offline(monkeypatch)
    monkeypatch.setenv("SPECAGENT_API_TOKEN", TOKEN)
    c = TestClient(main.app)
    for path, body in (("/api/agent/sessions", {}),
                       ("/api/agent/sessions/dash-x/messages", {"text": "hi"}),
                       ("/api/agent/sessions/dash-x/approve",
                        {"action_id": "a", "approve": True})):
        r = c.post(path, json=body)
        assert r.status_code == 401, path  # the token check runs first: 401, not 403
        assert r.headers.get("www-authenticate") == "Bearer"
    assert c.post("/api/agent/sessions", json={},
                  headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.post("/api/agent/sessions", json={},
                  headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert c.post("/api/agent/sessions", json={},
                  headers={"X-API-Key": TOKEN}).status_code == 200
    assert c.get("/api/health").json()["agent_enabled"] is True


def test_router_dependency_order():
    order = [d.dependency for d in agent_api.router.dependencies]
    assert order == [auth.require_api_token, auth.require_agent_enabled]
    agent_routes = [r for r in main.app.routes
                    if isinstance(r, APIRoute) and r.path.startswith("/api/agent/")]
    assert {r.path for r in agent_routes} >= {
        "/api/agent/sessions", "/api/agent/sessions/{session_id}/messages",
        "/api/agent/sessions/{session_id}/approve"}
    for route in agent_routes:
        calls = [d.call for d in route.dependant.dependencies]
        assert calls.index(auth.require_api_token) < calls.index(auth.require_agent_enabled)


def test_startup_warning_for_insecure_optin(monkeypatch, caplog):
    log = logging.getLogger("specagent.auth")
    monkeypatch.setenv("SPECAGENT_AGENT_API_INSECURE", "1")
    with caplog.at_level(logging.WARNING, logger="specagent.auth"):
        auth.log_startup_warning(log)
    assert "SPECAGENT_AGENT_API_INSECURE=1" in caplog.text
    assert "SPECAGENT_API_TOKEN is not set" in caplog.text  # the original warning is kept
    caplog.clear()
    monkeypatch.setenv("SPECAGENT_AGENT_API_INSECURE", "")
    with caplog.at_level(logging.WARNING, logger="specagent.auth"):
        auth.log_startup_warning(log)
    assert "SPECAGENT_AGENT_API_INSECURE" not in caplog.text


# -- parked-action protocol over HTTP ------------------------------------------------------


def test_run_suite_parks_until_approved(tmp_path, monkeypatch):
    pid = _project(tmp_path, monkeypatch)
    client = _fake(monkeypatch, [[_call("run_suite", {}, "c1")], [_message("done")]])
    c = _authed(monkeypatch)
    created = _session(c)
    assert created["mode"] == "llm" and created["model"] and created["project"] == pid
    assert created["session_id"].startswith("dash-")
    sid = created["session_id"]

    parked = _say(c, sid).json()
    assert parked["stop_reason"] == "awaiting_approval"
    assert parked["action"] is None
    assert len(parked["pending"]) == 1
    pending = parked["pending"][0]
    assert set(pending) == PENDING_KEYS
    assert pending["tool"] == "run_suite" and pending["human_only"] is False
    assert pending["summary"]
    assert _runs(pid) == 0  # nothing ran before approval
    assert len(client.inputs) == 1  # the parked call produced no output yet

    done = _approve(c, sid, pending["action_id"]).json()
    assert done["stop_reason"] == "completed"
    assert done["pending"] == []
    assert _runs(pid) == 1  # exactly one run
    assert "Deterministic results (verbatim)" in done["reply"]
    assert done["action"]["decision"] == "approved" and done["action"]["ok"] is True
    assert done["action"]["tool"] == "run_suite"
    assert done["action"]["quote"].startswith("Run ")
    assert _outputs(client.inputs[1])["c1"]["ok"] is True

    again = _approve(c, sid, pending["action_id"])
    assert again.status_code == 409
    assert again.json()["detail"] == "action_already_resolved"
    assert _runs(pid) == 1


def test_decline_creates_no_run(tmp_path, monkeypatch):
    pid = _project(tmp_path, monkeypatch)
    client = _fake(monkeypatch, [[_call("run_suite", {}, "c1")], [_message("ok")]])
    c = _authed(monkeypatch)
    sid = _session(c)["session_id"]
    action_id = _say(c, sid).json()["pending"][0]["action_id"]
    assert _runs(pid) == 0
    body = _approve(c, sid, action_id, approve=False).json()
    assert _runs(pid) == 0
    assert body["stop_reason"] == "completed"
    assert body["action"]["decision"] == "declined"
    assert body["action"]["error"] == "confirmation_declined"
    assert _outputs(client.inputs[1])["c1"]["reason"] == "user_declined"
    assert agent_api._sessions[sid].ctx.pending == {}
    assert _approve(c, sid, action_id).status_code == 409


def test_message_while_pending_409(tmp_path, monkeypatch):
    pid = _project(tmp_path, monkeypatch)
    _fake(monkeypatch, [[_call("run_suite", {}, "c1")], [_message("ok")]])
    c = _authed(monkeypatch)
    sid = _session(c)["session_id"]
    action_id = _say(c, sid).json()["pending"][0]["action_id"]
    blocked = _say(c, sid, "hello?")
    assert blocked.status_code == 409
    assert blocked.json()["detail"].startswith("pending_action_unresolved")
    assert _runs(pid) == 0
    assert _approve(c, sid, action_id).json()["stop_reason"] == "completed"


def test_unknown_session_and_action_404(tmp_path, monkeypatch):
    pid = _project(tmp_path, monkeypatch)
    _fake(monkeypatch, [[_call("run_suite", {}, "c1")], [_message("ok")]])
    c = _authed(monkeypatch)
    r = _say(c, "dash-does-not-exist")
    assert r.status_code == 404 and r.json()["detail"] == "session_not_found"
    r = _approve(c, "dash-does-not-exist", "act-1")
    assert r.status_code == 404 and r.json()["detail"] == "session_not_found"
    sid = _session(c)["session_id"]
    r = _approve(c, sid, "act-nothing-parked")
    assert r.status_code == 404 and r.json()["detail"] == "action_not_found"
    action_id = _say(c, sid).json()["pending"][0]["action_id"]
    r = _approve(c, sid, "act-wrong")
    assert r.status_code == 404 and r.json()["detail"] == "action_not_found"
    assert _runs(pid) == 0
    assert _approve(c, sid, action_id).status_code == 200  # still parked, still approvable
    assert _runs(pid) == 1


def test_replace_spec_stale_confirmation_leaves_spec_unchanged(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    path, sha = _write_agent_draft(tmp_path)
    spec_path = tmp_path / "specs" / "behavior.yaml"
    spec_before = spec_path.read_bytes()
    _fake(monkeypatch, [[_call("replace_spec", {"draft_name": "d1", "draft_sha256": sha}, "c1")],
                        [_message("ok")]])
    c = _authed(monkeypatch)
    sid = _session(c)["session_id"]
    pending = _say(c, sid, "replace the spec with my draft").json()["pending"][0]
    assert pending["tool"] == "replace_spec" and pending["human_only"] is True
    assert "Drafted" in pending["preview"]
    path.write_text(DRAFT_MARKER + "\n" + SPEC.replace("agent: T", "agent: Changed"),
                    encoding="utf-8")
    body = _approve(c, sid, pending["action_id"]).json()
    assert body["action"]["ok"] is False
    assert body["action"]["error"] == "stale_confirmation"
    assert body["action"]["changed"] == ["draft"]
    assert spec_path.read_bytes() == spec_before
    assert list((tmp_path / "specs").glob("*.bak-*")) == []


def test_human_only_replace_spec_approved_via_dashboard(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    _path, sha = _write_agent_draft(tmp_path)
    spec_path = tmp_path / "specs" / "behavior.yaml"
    _fake(monkeypatch, [[_call("replace_spec", {"draft_name": "d1", "draft_sha256": sha}, "c1")],
                        [_message("replaced")]])
    c = _authed(monkeypatch)
    sid = _session(c)["session_id"]
    pending = _say(c, sid).json()["pending"][0]
    assert "agent: Drafted" not in spec_path.read_text(encoding="utf-8")
    body = _approve(c, sid, pending["action_id"]).json()
    assert body["action"]["ok"] is True and body["action"]["decision"] == "approved"
    assert "agent: Drafted" in spec_path.read_text(encoding="utf-8")
    assert list((tmp_path / "specs").glob("behavior.yaml.bak-*"))


# -- offline mode ---------------------------------------------------------------------------


def test_offline_mode_flow(tmp_path, monkeypatch):
    pid = _project(tmp_path, monkeypatch)
    _offline(monkeypatch)
    c = _authed(monkeypatch)
    created = _session(c)
    assert created["mode"] == "offline" and created["model"] is None
    sid = created["session_id"]
    parked = _say(c, sid, "anything").json()
    assert parked["stop_reason"] == "awaiting_approval"
    assert parked["pending"][0]["tool"] == "run_suite"
    assert set(parked["pending"][0]) == PENDING_KEYS
    assert _runs(pid) == 0
    assert _say(c, sid).status_code == 409  # same pending mechanism
    done = _approve(c, sid, parked["pending"][0]["action_id"]).json()
    assert done["stop_reason"] == "offline"
    assert "Offline mode" in done["reply"]
    assert done["action"]["ok"] is True
    assert done["pending"] == []
    assert _runs(pid) == 1
    # the session is reusable: a second message parks a fresh run_suite
    again = _say(c, sid).json()
    assert again["stop_reason"] == "awaiting_approval"
    assert again["pending"][0]["action_id"] != parked["pending"][0]["action_id"]


def test_offline_mode_decline(tmp_path, monkeypatch):
    pid = _project(tmp_path, monkeypatch)
    _offline(monkeypatch)
    c = _authed(monkeypatch)
    sid = _session(c)["session_id"]
    action_id = _say(c, sid).json()["pending"][0]["action_id"]
    body = _approve(c, sid, action_id, approve=False).json()
    assert body["stop_reason"] == "offline"
    assert "user_declined" in body["reply"]
    assert body["action"]["decision"] == "declined"
    assert _runs(pid) == 0


# -- request-body boundaries ------------------------------------------------------------------


def test_allow_source_cannot_be_enabled_from_request(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    client = _fake(monkeypatch, [[_call("read_file", {"path": "specs/behavior.yaml"}, "c1")],
                                 [_message("no access")]])
    c = _authed(monkeypatch)
    assert c.post("/api/agent/sessions", json={"allow_source": True}).status_code == 422
    assert c.post("/api/agent/sessions",
                  json={"config": str(tmp_path / "x.yaml")}).status_code == 422
    sid = _session(c)["session_id"]
    assert c.post(f"/api/agent/sessions/{sid}/messages",
                  json={"text": "hi", "allow_source": True}).status_code == 422
    assert agent_api._sessions[sid].ctx.allow_source is False
    assert _say(c, sid, "read my spec").json()["stop_reason"] == "completed"
    assert _outputs(client.inputs[1])["c1"]["error"] == "source_access_disabled"


def test_allow_source_comes_from_server_config(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, config_extra="agent:\n  allow_source: true\n")
    _offline(monkeypatch)
    c = _authed(monkeypatch)
    sid = _session(c)["session_id"]
    assert agent_api._sessions[sid].ctx.allow_source is True


def test_session_config_missing_or_invalid_409(tmp_path, monkeypatch):
    _offline(monkeypatch)
    c = _authed(monkeypatch)
    monkeypatch.setenv("SPECAGENT_PROJECT_CONFIG", str(tmp_path / "missing.yaml"))
    r = c.post("/api/agent/sessions", json={})
    assert r.status_code == 409
    assert "project_config_invalid" in r.json()["detail"]
    assert "config file not found" in r.json()["detail"]
    bad = tmp_path / "bad.yaml"
    bad.write_text("project: x\nspec: specs/behavior.yaml\nbogus_field: 1\n", encoding="utf-8")
    monkeypatch.setenv("SPECAGENT_PROJECT_CONFIG", str(bad))
    r = c.post("/api/agent/sessions", json={})
    assert r.status_code == 409
    assert "bogus_field" in r.json()["detail"]
    assert agent_api._sessions == {}


def test_message_validation_422(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    _offline(monkeypatch)
    c = _authed(monkeypatch)
    sid = _session(c)["session_id"]
    assert _say(c, sid, "").status_code == 422
    assert _say(c, sid, "x" * 4001).status_code == 422
    assert c.post(f"/api/agent/sessions/{sid}/messages", json={}).status_code == 422
    for body in ({"action_id": "a", "approve": "yes"}, {"action_id": "a", "approve": 1},
                 {"approve": True}, {"action_id": "", "approve": True},
                 {"action_id": "a", "approve": True, "extra": 1}):
        assert c.post(f"/api/agent/sessions/{sid}/approve", json=body).status_code == 422, body
    assert _say(c, sid, "x" * 4000).status_code == 200


# -- session table -------------------------------------------------------------------------------


def test_session_cap_and_ttl(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    _offline(monkeypatch)
    now = [1000.0]
    monkeypatch.setattr(agent_api, "_clock", lambda: now[0])
    c = _authed(monkeypatch)
    ids = []
    for _ in range(agent_api.MAX_SESSIONS + 1):
        now[0] += 1
        ids.append(_session(c)["session_id"])
    assert len(agent_api._sessions) == agent_api.MAX_SESSIONS
    assert _say(c, ids[0]).status_code == 404  # the oldest was evicted
    # touching a session makes it "recent": the next eviction takes ids[2]
    assert _approve(c, ids[1], "act-x").status_code == 404  # action_not_found, but touched
    now[0] += 1
    _session(c)
    assert ids[1] in agent_api._sessions and ids[2] not in agent_api._sessions
    # idle TTL: one hour without use ⇒ gone
    now[0] += agent_api.SESSION_TTL_SECONDS + 1
    r = _say(c, ids[1])
    assert r.status_code == 404 and r.json()["detail"] == "session_not_found"
    assert len(agent_api._sessions) == 0


class _BusyLock:
    """Stands in for a session lock held by a request in flight. A real
    threading.Lock cannot be used here: the test thread would have to release
    it from the portal thread that serves the HTTP request."""

    def locked(self):
        return True

    def acquire(self, *a, **kw):  # pragma: no cover — never taken
        raise AssertionError("test stub must not be acquired")

    def release(self):  # pragma: no cover
        raise AssertionError("test stub must not be released")

    def __enter__(self):
        raise AssertionError("test stub must not be entered")

    def __exit__(self, *exc):
        return False


def test_eviction_skips_sessions_with_a_request_in_flight(tmp_path, monkeypatch):
    """A running run_suite must never be evicted out from under itself: the
    follow-up approve would 404 and the parked action would be lost (review #3)."""
    _project(tmp_path, monkeypatch)
    _offline(monkeypatch)
    c = _authed(monkeypatch)
    ids = [_session(c)["session_id"] for _ in range(agent_api.MAX_SESSIONS)]
    assert len(agent_api._sessions) == agent_api.MAX_SESSIONS

    # the LRU entry is busy ⇒ the next idle one is evicted instead
    busy_id = ids[0]
    agent_api._sessions[busy_id].lock = _BusyLock()
    r = c.post("/api/agent/sessions", json={})
    assert r.status_code == 200, r.text
    survivors = list(agent_api._sessions)
    assert busy_id in agent_api._sessions            # survived
    assert ids[1] not in agent_api._sessions          # evicted instead
    assert len(agent_api._sessions) == agent_api.MAX_SESSIONS

    # every slot busy ⇒ reject the newcomer rather than kill someone's work
    for s in agent_api._sessions.values():
        s.lock = _BusyLock()
    full = c.post("/api/agent/sessions", json={})
    assert full.status_code == 429, full.text
    assert full.json()["detail"].startswith("too_many_sessions")
    assert list(agent_api._sessions) == survivors    # nothing was evicted

    # once the requests finish the LRU order applies again
    for s in agent_api._sessions.values():
        s.lock = threading.Lock()
    for _ in range(agent_api.MAX_SESSIONS):
        assert c.post("/api/agent/sessions", json={}).status_code == 200
    assert busy_id not in agent_api._sessions


def test_busy_sessions_are_never_purged_by_ttl(tmp_path, monkeypatch):
    """The TTL sweep already skipped locked sessions; keep it that way."""
    _project(tmp_path, monkeypatch)
    _offline(monkeypatch)
    now = [1000.0]
    monkeypatch.setattr(agent_api, "_clock", lambda: now[0])
    c = _authed(monkeypatch)
    sid = _session(c)["session_id"]
    agent_api._sessions[sid].lock = _BusyLock()
    now[0] += agent_api.SESSION_TTL_SECONDS + 1
    assert _session(c)["session_id"] != sid
    assert sid in agent_api._sessions      # not purged: a request is in flight
    agent_api._sessions[sid].lock = threading.Lock()
    now[0] += agent_api.SESSION_TTL_SECONDS + 1
    assert _say(c, sid).status_code == 404     # the sweep runs on the next request
    assert sid not in agent_api._sessions


class _SlowClient:
    """Counts concurrent model calls; each takes 50 ms."""

    def __init__(self):
        self.responses = self
        self.active = 0
        self.max_active = 0
        self.calls = 0
        self._guard = threading.Lock()

    def create(self, **kwargs):
        with self._guard:
            self.active += 1
            self.calls += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.05)
        with self._guard:
            self.active -= 1
        return _FakeResponse([_message("ok")])


def test_per_session_lock_serializes(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    slow = _SlowClient()
    monkeypatch.setattr(agent_api, "client_factory", lambda: slow)
    c = _authed(monkeypatch)
    sid = _session(c)["session_id"]
    statuses = []

    def worker():
        with TestClient(main.app, headers={"Authorization": f"Bearer {TOKEN}"}) as tc:
            statuses.append(_say(tc, sid, "hello").status_code)

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert statuses == [200, 200, 200]
    assert slow.calls == 3
    assert slow.max_active == 1


# -- dashboard markup -------------------------------------------------------------------------------


def test_dashboard_agent_panel_markup():
    html = (main.BASE / "static" / "index.html").read_text(encoding="utf-8")
    for needle in ('id="agentCard"', 'id="agentSection"', "/api/agent/sessions", "needs you",
                   "agent_enabled", 'role="log"', 'aria-live="polite"', "isComposing"):
        assert needle in html, needle
    start = html.index("// -- agent panel (design §8.9)")
    end = html.index("// -- end agent panel --")
    panel = html[start:end]
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
                      "fetch(", "eval(", "new Function"):
        assert forbidden not in panel, forbidden
    assert "textContent" in panel and "api(" in panel
    assert "localStorage" not in html
    # zero external resources (no CDN scripts, styles, fonts or images)
    assert not re.search(r"""(src|href)\s*=\s*["']https?://""", html)
    assert "@import" not in html and "url(http" not in html


def test_approval_failure_paths_are_distinguished():
    """Review #1/#2: a retried approve that gets 409 must never be labelled
    "not executed", and a 404 must only drop the session when the *session* is
    gone (action_not_found leaves a live session behind)."""
    html = (main.BASE / "static" / "index.html").read_text(encoding="utf-8")
    resolve = html[html.index("async function agentResolve"):html.index("function agentResetConfirm")]
    assert "'该动作已失效,未执行'" in resolve
    assert "可能已执行" in resolve, "409 must say the action may have run"
    assert "e.status===409" in resolve and "e.status===404" in resolve
    # the two branches are distinguishable: the 409 one refreshes Run history
    assert resolve.index("e.status===409") < resolve.index("e.status===404")
    assert "agentRefreshRuns(" in resolve
    # the old wording for both statuses is gone
    assert not re.search(r"404\|\|e\.status===409", resolve)
    assert "agentSessionGone(e)" in html
    assert "e.status===404&&e.detail!=='action_not_found'" in html
    fail = html[html.index("function agentFail"):html.index("async function ensureAgentSession")]
    assert "agentSessionGone(e)" in fail and "e.status===404" not in fail


def test_reset_uses_an_inline_prompt_and_chips_are_generic():
    """Review #5/#6: no native confirm(), and the example prompts must not be
    tied to one project's domain."""
    html = (main.BASE / "static" / "index.html").read_text(encoding="utf-8")
    panel = html[html.index("// -- agent panel (design §8.9)"):
                 html.index("// -- end agent panel --")]
    # no blocking browser dialog anywhere in the panel
    assert not re.search(r"(?<!\w)(?:window\.)?confirm\(", panel)
    assert "丢弃并新建" in panel and "取消" in panel
    assert "agentResetConfirm" in panel
    prompts = re.findall(r'class="chip" data-prompt="([^"]+)"', html)
    assert len(prompts) == 3
    for p in prompts:
        assert "退款" not in p, p
