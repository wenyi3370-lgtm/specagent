"""Real event ordering with fake providers, persistence and transcript isolation."""
import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import agent_api, agent_stream, main
from test_agent_api import _authed, _offline, _project, _session, TOKEN
from test_agent_loop import _FakeResponse, _call, _message, _assert_output_invariant


@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    agent_api.reset_sessions()
    pid = _project(tmp_path, monkeypatch)
    _offline(monkeypatch)
    yield _authed(monkeypatch), pid, tmp_path
    agent_api.reset_sessions()


def frames(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    out = []
    for block in response.text.split("\n\n"):
        lines = block.splitlines()
        kind = next((line[7:] for line in lines if line.startswith("event: ")), None)
        data = next((line[6:] for line in lines if line.startswith("data: ")), None)
        if kind and data:
            out.append((kind, json.loads(data)))
    return out


def result(events):
    return next(data for kind, data in events if kind == "result")


def send(client, sid, text="Run tests"):
    return frames(client.post(f"/api/agent/sessions/{sid}/messages/stream", json={"text": text}))


def approve(client, sid, action, decision=True):
    return frames(client.post(f"/api/agent/sessions/{sid}/approve/stream",
                              json={"action_id": action, "approve": decision}))


def test_offline_timeline_parks_and_resumes_once(dashboard):
    c, pid, root = dashboard
    sid = _session(c)["session_id"]
    events = send(c, sid)
    records = [d for k, d in events if k == "timeline"]
    assert [r["data"]["tool"] for r in records if r["type"] == "tool_call"] == ["inspect_project", "run_suite"]
    assert any(r["type"] == "confirm" and r["data"]["decision"] == "deferred" for r in records)
    assert main.store.list_runs(pid) == []
    action = result(events)["pending"][0]["action_id"]
    assert c.post(f"/api/agent/sessions/{sid}/messages/stream", json={"text": "more"}).status_code == 409
    resumed = approve(c, sid, action)
    assert result(resumed)["action"]["ok"]
    assert len(main.store.list_runs(pid)) == 1
    assert any(k == "timeline" and r["type"] == "resume" for k, r in resumed)
    assert any(k == "timeline" and r["type"] == "tool_result" and r["data"]["tool"] == "triage_run" for k, r in resumed)
    assert c.post(f"/api/agent/sessions/{sid}/approve/stream", json={"action_id": action, "approve": True}).status_code == 409
    assert len(main.store.list_runs(pid)) == 1
    assert str(root) not in json.dumps(resumed)


def test_decline_stream_does_not_execute(dashboard):
    c, pid, _ = dashboard
    sid = _session(c)["session_id"]
    action = result(send(c, sid))["pending"][0]["action_id"]
    reply = result(approve(c, sid, action, False))
    assert reply["action"]["decision"] == "declined"
    assert "confirmation declined" in reply["reply"]
    assert main.store.list_runs(pid) == []


class StreamClient:
    def __init__(self, turns, release=None):
        self.responses = self
        self.turns = iter(turns)
        self.inputs = []
        self.closed = 0
        self.release = release

    def create(self, **kwargs):
        assert kwargs["stream"] is True
        self.inputs.append(list(kwargs["input"]))
        text, output = next(self.turns)
        owner = self

        class Events:
            def __iter__(self):
                for chunk in text:
                    yield SimpleNamespace(type="response.output_text.delta", delta=chunk)
                    if owner.release is not None:
                        assert owner.release.wait(10)
                yield SimpleNamespace(type="response.completed", response=_FakeResponse(output))

            def close(self):
                owner.closed += 1

        return Events()


def test_provider_stream_visible_before_completion(dashboard, monkeypatch):
    c, _, _ = dashboard
    release = threading.Event()
    text = "Here is the live explanation of the tests. " * 5
    fake = StreamClient([([text], [_message(text)])], release)
    monkeypatch.setattr(agent_api, "client_factory", lambda: fake)
    sid = _session(c)["session_id"]
    session = agent_api._sessions[sid]
    response = agent_stream._stream(session, agent_api.MessageRequest(text="explain"))

    async def consume():
        seen = []
        try:
            async for chunk in response.body_iterator:
                seen.append(chunk)
                if "event: text_delta" in chunk and not release.is_set():
                    assert session.lock.locked(), "the model must still be waiting"
                    assert fake.closed == 0
                    assert "live explanation" in chunk
                    release.set()
        finally:
            release.set()
        return "".join(seen)

    output = asyncio.run(consume())
    assert "event: result" in output and fake.closed == 1
    assert not session.lock.locked() and not session.agent.stream


def test_stream_approval_preserves_function_call_protocol(dashboard, monkeypatch):
    c, _, _ = dashboard
    fake = StreamClient([([], [_call("inspect_project", {}, "c1"), _call("run_suite", {}, "c2")]),
                         (["The deterministic results follow."], [_message("The deterministic results follow.")])])
    monkeypatch.setattr(agent_api, "client_factory", lambda: fake)
    sid = _session(c)["session_id"]
    parked = result(send(c, sid))
    final = result(approve(c, sid, parked["pending"][0]["action_id"]))
    assert "Deterministic results (verbatim)" in final["reply"]
    assert final["action"]["quote"] in final["reply"]
    _assert_output_invariant(fake.inputs)
    assert fake.closed == 2


def test_split_credentials_never_enter_stream_or_history(dashboard, monkeypatch):
    c, _, _ = dashboard
    secret = "fake-super-secret-value-91"
    monkeypatch.setenv("PRIVATE_TEST_SECRET", secret)
    text = "API_KEY=" + secret + " " + "ordinary safe words " * 12
    fake = StreamClient([(["API_KEY=fake-super-", "secret-value-91 ", "ordinary safe words " * 12], [_message(text)])])
    monkeypatch.setattr(agent_api, "client_factory", lambda: fake)
    sid = _session(c)["session_id"]
    events = send(c, sid)
    for prefix in (secret, "fake-super-", "secret-value-91"):
        assert prefix not in json.dumps(events)
    log = c.get("/api/agent/logs").json()["logs"][0]["id"]
    assert secret not in c.get(f"/api/agent/logs/{log}").text


def test_history_survives_session_reset_and_is_paginated(dashboard):
    c, _, _ = dashboard
    sid = _session(c)["session_id"]
    send(c, sid, "Explain this project")
    log = c.get("/api/agent/logs").json()["logs"][0]
    assert log["title"] == "Explain this project"
    first = c.get(f"/api/agent/logs/{log['id']}?limit=2").json()
    assert first["session"]["active_session_id"] == sid
    assert first["pending"][0]["tool"] == "run_suite"
    second = c.get(f"/api/agent/logs/{log['id']}?after={first['next_after']}&limit=100").json()
    assert first["events"][-1]["seq"] < second["events"][0]["seq"]
    agent_api.reset_sessions()
    saved = c.get(f"/api/agent/logs/{log['id']}").json()
    assert saved["session"]["active_session_id"] is None
    assert saved["pending"] == []
    assert any(r["type"] == "dashboard_result" and r["data"]["pending"] for r in saved["events"])


@pytest.mark.parametrize("path", ["/api/agent/logs", "/api/agent/logs/agent-20261007T130000-abcd"])
def test_log_auth(dashboard, path):
    assert TestClient(main.app).get(path).status_code == 401


def test_stream_auth_and_strict_requests(dashboard):
    c, _, _ = dashboard
    sid = _session(c)["session_id"]
    path = f"/api/agent/sessions/{sid}/messages/stream"
    assert TestClient(main.app).post(path, json={"text": "hi"}).status_code == 401
    assert c.post(path, json={"text": "hi", "config": "somewhere"}).status_code == 422
    assert c.get("/api/agent/logs?before=invalid&limit=0").status_code == 422
    assert c.get("/api/agent/logs/not-an-id").status_code == 422
    assert c.get("/api/agent/logs/agent-20261007T130000-abcd").status_code == 404


def test_logs_containment_and_size_limit(dashboard):
    c, _, root = dashboard
    _session(c)
    logs = root / ".specagent" / "agent-logs"
    oversized = logs / "agent-20261007T130000-abcd.jsonl"
    oversized.write_bytes(b"x" * (agent_stream.MAX_LOG_BYTES + 1))
    assert c.get(f"/api/agent/logs/{oversized.stem}").status_code == 413
    assert any(item.get("unavailable") for item in c.get("/api/agent/logs").json()["logs"])
    outside = root / "outside.jsonl"
    outside.write_text('{"seq":1,"data":{"text":"private"}}', encoding="utf-8")
    link = logs / "agent-20261007T130001-abcd.jsonl"
    try:
        link.symlink_to(outside)
    except OSError:
        return  # Windows accounts without CreateSymbolicLink privilege.
    assert c.get(f"/api/agent/logs/{link.stem}").status_code == 403
    assert "private" not in c.get("/api/agent/logs").text


def test_stream_busy_and_cleanup_on_provider_failure(dashboard, monkeypatch):
    c, _, _ = dashboard
    sid = _session(c)["session_id"]
    session = agent_api._sessions[sid]
    session.lock.acquire()
    try:
        assert c.post(f"/api/agent/sessions/{sid}/messages/stream", json={"text": "hi"}).status_code == 409
    finally:
        session.lock.release()
    class Broken:
        responses = None
        def create(self, **kwargs):
            raise RuntimeError("provider error")
    broken = Broken()
    broken.responses = broken
    monkeypatch.setattr(agent_api, "client_factory", lambda: broken)
    sid = _session(c)["session_id"]
    final = result(send(c, sid))
    assert final["stop_reason"] == "llm_error"
    assert not agent_api._sessions[sid].lock.locked()


def test_disconnect_keeps_approved_action_running_exactly_once(dashboard, monkeypatch):
    c, pid, _ = dashboard
    sid = _session(c)["session_id"]
    action = result(send(c, sid))["pending"][0]["action_id"]
    session = agent_api._sessions[sid]
    release, entered = threading.Event(), threading.Event()
    execute = session.registry.execute_approved

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return execute(*args, **kwargs)

    monkeypatch.setattr(session.registry, "execute_approved", slow)
    response = agent_stream._stream(session, agent_api.ApproveRequest(action_id=action, approve=True), True)

    async def disconnect():
        try:
            chunk = await anext(response.body_iterator)
            assert "resume" in chunk
        finally:
            await response.body_iterator.aclose()

    try:
        asyncio.run(disconnect())
        assert entered.wait(5)
    finally:
        release.set()
    deadline = time.monotonic() + 10
    while session.lock.locked() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not session.lock.locked()
    assert len(main.store.list_runs(pid)) == 1
    assert action in session.resolved
    assert c.post(f"/api/agent/sessions/{sid}/approve/stream", json={"action_id": action, "approve": True}).status_code == 409
    log = c.get(f"/api/agent/logs/{session.transcript.session_id}").json()
    assert any(r["type"] == "dashboard_result" and r["data"]["action"] for r in log["events"])
