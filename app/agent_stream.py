"""Live dashboard events and read-only, persistent agent transcript browsing."""
import asyncio
import json
import os
import queue
import re
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from . import agent_api
from .agent.loop import pending_view
from .agent.sandbox import redact_text
from .auth import require_agent_enabled, require_api_token, auth_mode, account_repository
from .accounts import principal_context
from .web_presenters import web_payload

router = APIRouter(prefix="/api/agent",
                   dependencies=[Depends(require_api_token), Depends(require_agent_enabled)])
LOG_ID = r"^agent-[0-9T]+-[0-9a-f]{4}$"
MAX_LOG_BYTES = 2_000_000


def _safe(value):
    def redact(item):
        if isinstance(item, str):
            return redact_text(item)
        if isinstance(item, dict):
            return {k: redact(v) for k, v in item.items()}
        if isinstance(item, list):
            return [redact(v) for v in item]
        return item
    return redact(web_payload(value))


def _stream(session, request, approve=False):
    # Reserve before sending HTTP 200. A duplicate request fails explicitly.
    if not session.lock.acquire(blocking=False):
        raise HTTPException(409, "session_busy")
    pending = agent_api._current_pending(session)
    if approve:
        error = (409, "action_already_resolved") if request.action_id in session.resolved else (
            (404, "action_not_found") if pending is None or pending.action_id != request.action_id else None)
    else:
        error = (409, "pending_action_unresolved") if pending is not None else None
    if error:
        session.lock.release()
        raise HTTPException(*error)
    events = queue.Queue(maxsize=64)
    connected = threading.Event()
    connected.set()

    def push(kind, data):
        # Backpressure is bounded. Disconnecting only detaches the view: an
        # already approved action still completes once and persists its log.
        item = (kind, _safe(data))
        while connected.is_set():
            try:
                events.put(item, timeout=0.1)
                return
            except queue.Full:
                continue

    hold = max([64] + [len(v) for k, v in os.environ.items()
                       if re.search(r"TOKEN|KEY|SECRET|PASSWORD|URL|DSN", k, re.I)])
    previews = {}

    def on_text(text, step, final):
        # Hold incomplete words and enough trailing characters to avoid leaking
        # a credential split across provider chunks. Never stream PEM content.
        cut = len(text) if final else max(0, len(text) - hold)
        if not final:
            partial = re.search(r"\S+$", text[:cut])
            if partial:
                cut = partial.start()
            pem = text.find("-----BEGIN ")
            if pem >= 0 and "-----END " not in text[pem:]:
                cut = min(cut, pem)
        preview = _safe(text[:min(cut, 20_000)])
        if preview and preview != previews.get(step):
            previews[step] = preview
            push("text_delta", {"step": step, "text": preview, "complete": final})

    def work():
        agent = session.agent
        previous = (agent.stream, agent.on_text) if agent else None
        session.transcript.listener = lambda record: push("timeline", record)
        try:
            if agent:
                agent.stream, agent.on_text = True, on_text
            if approve:
                session.transcript.emit("resume", {"action_id": request.action_id,
                                                  "decision": "approved" if request.approve else "declined"})
            operation = agent_api._approve_locked if approve else agent_api._message_locked
            push("result", operation(session, request))
        except Exception as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else "agent_stream_failed: " + type(exc).__name__
            session.transcript.emit("error", {"detail": detail})
            push("failure", {"detail": detail})
        finally:
            session.transcript.listener = None
            if agent:
                agent.stream, agent.on_text = previous
            agent_api._touch(session)
            session.lock.release()
            push("end", {})

    async def generate():
        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        try:
            while True:
                try:
                    kind, data = await asyncio.to_thread(events.get, True, 10)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                if kind == "end":
                    break
                yield "event: " + kind + "\ndata: " + json.dumps(data, ensure_ascii=False) + "\n\n"
        finally:
            connected.clear()

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                      "X-Content-Type-Options": "nosniff"})


@router.post("/sessions/{session_id}/messages/stream")
def stream_message(session_id: str, req: agent_api.MessageRequest):
    return _stream(agent_api._get_session(session_id), req)


@router.post("/sessions/{session_id}/approve/stream")
def stream_approval(session_id: str, req: agent_api.ApproveRequest):
    return _stream(agent_api._get_session(session_id), req, approve=True)


def _logs_dir():
    root = Path(agent_api._config_path()).resolve().parent
    state = root / ".specagent"
    logs = state / "agent-logs"
    if state.is_symlink() or logs.is_symlink() or not logs.resolve().is_relative_to(root):
        raise HTTPException(403, "transcript_path_denied")
    return logs


def _records(path):
    logs = _logs_dir()
    if path.is_symlink() or not path.resolve().is_relative_to(logs.resolve()):
        raise HTTPException(403, "transcript_path_denied")
    if not path.is_file():
        raise HTTPException(404, "transcript_not_found")
    if path.stat().st_size > MAX_LOG_BYTES:
        raise HTTPException(413, "transcript_too_large")
    records = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # A crash or a concurrent append may leave a partial line.
            if isinstance(record, dict) and isinstance(record.get("seq"), int):
                records.append(record)
    except (OSError, UnicodeError):
        raise HTTPException(422, "transcript_unreadable") from None
    return records


def _live(log_id):
    with agent_api._sessions_lock:
        agent_api._purge_expired(agent_api._clock())
        sessions = list(agent_api._sessions.values())
    for session in sessions:
        if auth_mode() == 'multiuser':
            principal = principal_context.get()
            if principal is None or session.owner_user != principal.user_id or session.owner_login != principal.login_id:
                continue
        if session.transcript.session_id == log_id and session.transcript.path.parent.resolve() == _logs_dir().resolve():
            return session
    return None


def _summary(path, records):
    meta = next((r["data"] for r in records if r.get("type") == "dashboard_session"), {})
    users = [r.get("data", {}).get("text", "") for r in records if r.get("type") == "user"]
    last = next((r.get("data", {}) for r in reversed(records) if r.get("type") == "dashboard_result"), {})
    live = _live(path.stem)
    return {"id": path.stem, "started_at": records[0].get("ts") if records else None,
            "updated_at": records[-1].get("ts") if records else None,
            "title": users[0][:120] if users else "New session", "mode": meta.get("mode", "cli"),
            "model": meta.get("model"), "project": meta.get("project"),
            "events": len(records), "stop_reason": last.get("stop_reason"),
            "active_session_id": live.session_id if live else None,
            "busy": bool(live and live.lock.locked())}


@router.get("/logs")
def list_logs(limit: int = Query(30, ge=1, le=100), before: str | None = Query(None, pattern=LOG_ID)):
    paths = sorted((p for p in _logs_dir().glob("*.jsonl") if re.fullmatch(LOG_ID, p.stem)), reverse=True)
    paths = [p for p in paths if before is None or p.stem < before]
    if auth_mode() == 'multiuser':
        entries = []
        for path in paths:
            try:
                records = _records(path)
                if _allowed_log(records):
                    entries.append(_summary(path, records))
            except HTTPException:
                continue
            if len(entries) > limit:
                break
        return _safe({'logs': entries[:limit], 'next_before': entries[limit - 1]['id'] if len(entries) > limit else None})
    entries = []
    for path in paths[:limit]:
        try:
            entries.append(_summary(path, _records(path)))
        except HTTPException:
            # Oversized/unsafe logs aren't followed and do not break history.
            entries.append({"id": path.stem, "title": "Transcript unavailable", "unavailable": True})
    return _safe({"logs": entries, "next_before": paths[limit - 1].stem if len(paths) > limit else None})


def _allowed_log(records):
    principal = principal_context.get()
    if principal is None:
        return False
    meta = next((r.get('data', {}) for r in records if r.get('type') == 'dashboard_session'), {})
    owner = meta.get('owner_user')
    if owner != principal.user_id:
        return owner is None and principal.admin
    return bool(account_repository().role(principal, meta.get('project', '')))


@router.get("/logs/{log_id}")
def read_log(log_id: str, after: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)):
    if not re.fullmatch(LOG_ID, log_id):
        raise HTTPException(422, "invalid_transcript_id")
    path = _logs_dir() / (log_id + ".jsonl")
    records = _records(path)
    if auth_mode() == 'multiuser' and not _allowed_log(records):
        raise HTTPException(404, 'transcript_not_found')
    page = [r for r in records if r["seq"] > after][:limit]
    live = _live(log_id)
    pending = []
    if live and live.lock.acquire(blocking=False):
        try:
            action = agent_api._current_pending(live)
            if action:
                pending = [pending_view(action)]
        finally:
            live.lock.release()
    return _safe({"session": _summary(path, records), "events": page,
                  "next_after": page[-1]["seq"] if page and page[-1]["seq"] < records[-1]["seq"] else None,
                  "pending": pending})
