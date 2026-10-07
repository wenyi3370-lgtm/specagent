"""Dashboard agent API (v1 design §8.9, task 16).

Three sync, non-streaming endpoints under ``/api/agent`` drive one
:class:`AgentSession` (or, without an LLM client, one
:class:`OfflineWorkflow`) per dashboard session. The router depends on
``require_api_token`` first (401) and ``require_agent_enabled`` second (403):
without a token the whole agent API is 403 unless the explicit loopback-only
opt-in is set.

Confirmations are always ``deferred``: a confirm-tier call parks a pending
action (§8.6 parked-action protocol) and nothing runs until
``POST …/approve`` with ``approve: true`` — that endpoint is the human path
for ``human_only`` tools. The browser never supplies paths: the project is
the server-side ``SPECAGENT_PROJECT_CONFIG`` (default ``./specagent.yaml``)
and ``allow_source`` comes only from that config's ``agent.allow_source``.

Sessions live in memory only (max 8, 1-hour idle TTL, least recently used
evicted first); a per-session lock serializes requests.
"""
import logging
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .agent.loop import AgentResult, AgentSession, OfflineWorkflow, Transcript
from .agent.sandbox import ProjectSandbox
from .agent.tools import ConfirmResult, ToolContext, ToolRegistry
from .auth import require_agent_enabled, require_api_token
from .errors import SpecValidationError
from .llm_client import make_client, resolve_model
# The config-path resolution is shared with the project-run API; the names
# stay importable from here for backward compatibility.
from .project import (DEFAULT_PROJECT_CONFIG, PROJECT_CONFIG_ENV, Project,
                      project_config_path)
from .storage import Store

logger = logging.getLogger("specagent.agent_api")

MAX_SESSIONS = 8
SESSION_TTL_SECONDS = 3600
MAX_MESSAGE_CHARS = 4000

# Module-level hooks: tests replace them with monkeypatch.setattr.
client_factory = make_client
_clock = time.monotonic

_store: Store | None = None


def bind_store(store: Store) -> None:
    """``main.py`` passes the shared Store so dashboard runs show up in the
    dashboard's Run history (§8.9)."""
    global _store
    _store = store


router = APIRouter(prefix="/api/agent",
                   dependencies=[Depends(require_api_token), Depends(require_agent_enabled)])


# -- request bodies: extra fields are rejected (the browser cannot enable
# allow_source or pick a path) ------------------------------------------------


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: str = Field(min_length=1, max_length=64)
    approve: StrictBool


# -- in-memory session table ---------------------------------------------------


@dataclass
class _DashSession:
    session_id: str
    mode: str                       # "llm" | "offline"
    model: str | None
    project_id: str
    ctx: ToolContext
    registry: ToolRegistry
    transcript: Transcript
    agent: AgentSession | None = None
    offline: OfflineWorkflow | None = None
    resolved: set = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_used: float = 0.0


_sessions: "OrderedDict[str, _DashSession]" = OrderedDict()
_sessions_lock = threading.Lock()


def reset_sessions() -> None:
    """Drop every session (tests)."""
    with _sessions_lock:
        _sessions.clear()


def _purge_expired(now: float) -> None:
    """Caller holds ``_sessions_lock``. A session with a request in flight is
    never purged."""
    for sid in [sid for sid, s in _sessions.items()
                if now - s.last_used > SESSION_TTL_SECONDS and not s.lock.locked()]:
        del _sessions[sid]
        logger.info("agent session %s expired (idle TTL)", sid)


def _register(session: _DashSession) -> None:
    """Insert a session, evicting the least recently used ones first.

    A session with a request in flight is never evicted: dropping it would make
    the follow-up approve/message 404 and lose the parked action (review #3).
    When every slot is busy the new session is rejected instead of silently
    killing someone's work.
    """
    with _sessions_lock:
        now = _clock()
        _purge_expired(now)
        while len(_sessions) >= MAX_SESSIONS:
            idle = [sid for sid, s in _sessions.items() if not s.lock.locked()]
            if not idle:
                logger.warning("agent session %s rejected: all %d sessions busy",
                               session.session_id, MAX_SESSIONS)
                raise HTTPException(429, "too_many_sessions: every agent session is busy, retry shortly")
            evicted = idle[0]
            del _sessions[evicted]
            logger.info("agent session %s evicted (max %d sessions, %d busy)",
                        evicted, MAX_SESSIONS, len(_sessions))
        session.last_used = now
        _sessions[session.session_id] = session


def _get_session(session_id: str) -> _DashSession:
    with _sessions_lock:
        _purge_expired(_clock())
        session = _sessions.get(session_id)
        if session is None:
            raise HTTPException(404, "session_not_found")
        session.last_used = _clock()
        _sessions.move_to_end(session_id)
        return session


def _touch(session: _DashSession) -> None:
    with _sessions_lock:
        session.last_used = _clock()
        if session.session_id in _sessions:
            _sessions.move_to_end(session.session_id)


# -- helpers ---------------------------------------------------------------------


def _deferred_confirm(_request) -> ConfirmResult:
    """Every confirmation parks (§8.9): the human answers via /approve."""
    return ConfirmResult("deferred")


def _config_path() -> str:
    return project_config_path()


def _load_project() -> Project:
    try:
        return Project.load(_config_path(), store=_store)
    except SpecValidationError as exc:
        raise HTTPException(409, "project_config_invalid: " + "; ".join(exc.errors)) from None


def _current_pending(session: _DashSession):
    if session.agent is not None:
        return session.agent.pending_action
    if session.offline is not None:
        return session.offline.pending_action
    return None


def _response(result: AgentResult, action: dict | None) -> dict:
    return {"reply": result.final_text, "stop_reason": result.stop_reason,
            "steps": result.steps, "pending": list(result.pending), "action": action}


# -- endpoints ------------------------------------------------------------------


@router.post("/sessions")
def create_session(req: CreateSessionRequest | None = None):
    project = _load_project()
    settings = project.agent_settings
    allow_source = bool(settings.allow_source)  # config only, never the request
    transcript = Transcript(project.root)
    ctx = ToolContext(project=project, sandbox=ProjectSandbox(project.root, allow_source),
                      allow_source=allow_source, confirm=_deferred_confirm,
                      transcript=transcript)
    registry = ToolRegistry()
    try:
        client = client_factory()
    except Exception as exc:  # noqa: BLE001 — e.g. the openai SDK is not installed
        logger.error("agent LLM client unavailable: %s", type(exc).__name__)
        raise HTTPException(409, f"llm_client_unavailable: {type(exc).__name__}") from None
    session_id = "dash-" + uuid.uuid4().hex
    if client is None:
        session = _DashSession(session_id, "offline", None, project.project_id,
                               ctx, registry, transcript)
    else:
        model = resolve_model()
        agent = AgentSession(ctx, registry, client=client, model=model,
                             max_steps=settings.max_steps,
                             budget_seconds=settings.budget_seconds, transcript=transcript)
        session = _DashSession(session_id, "llm", model, project.project_id,
                               ctx, registry, transcript, agent=agent)
    _register(session)
    transcript.emit("dashboard_session", {"session_id": session_id, "mode": session.mode,
                                            "model": session.model, "project": session.project_id})
    logger.info("agent session %s created (mode=%s, project=%s)",
                session_id, session.mode, session.project_id)
    return {"session_id": session_id, "mode": session.mode, "model": session.model,
            "project": session.project_id}


@router.post("/sessions/{session_id}/messages")
def post_message(session_id: str, req: MessageRequest):
    session = _get_session(session_id)
    with session.lock:
        return _message_locked(session, req)


def _message_locked(session, req):
    if _current_pending(session) is not None:
        raise HTTPException(
            409, "pending_action_unresolved: approve or decline the pending action first")
    if session.agent is not None:
        session.agent.begin_turn()
        result = session.agent.send(req.text)
    else:
        session.transcript.emit("user", {"text": req.text})
        workflow = OfflineWorkflow(session.ctx, session.registry, session.transcript)
        result = workflow.run(req.text)
        session.offline = workflow if workflow.pending_action is not None else None
    _touch(session)
    response = _response(result, None)
    session.transcript.emit("dashboard_result", response)
    return response



@router.post("/sessions/{session_id}/approve")
def approve_action(session_id: str, req: ApproveRequest):
    session = _get_session(session_id)
    with session.lock:
        return _approve_locked(session, req)


def _approve_locked(session, req):
    if req.action_id in session.resolved:
        raise HTTPException(409, "action_already_resolved")
    pending = _current_pending(session)
    if pending is None or pending.action_id != req.action_id:
        raise HTTPException(404, "action_not_found")
    tool = pending.tool
    if session.agent is not None:
        session.agent.begin_turn(reset_steps=False)
        result = session.agent.resolve_pending(req.action_id, req.approve)
        outcome = session.agent.last_resolution or {}
    else:
        workflow = session.offline
        result = workflow.resolve_pending(req.action_id, req.approve)
        outcome = workflow.last_resolution or {}
        if workflow.pending_action is None:
            session.offline = None
    session.resolved.add(req.action_id)
    _touch(session)
    logger.info("agent session %s: action %s (%s) %s", session.session_id, req.action_id, tool,
                "approved" if req.approve else "declined")
    response = _response(result, {
        "action_id": req.action_id, "tool": tool,
        "decision": "approved" if req.approve else "declined",
        "ok": bool(outcome.get("ok")), "error": outcome.get("error"),
        "changed": outcome.get("changed"), "quote": outcome.get("quote"),
        **({"suggestion_id": outcome["id"]} if tool == "write_fix_suggestion" and outcome.get("ok") and outcome.get("id") else {})})
    session.transcript.emit("dashboard_result", response)
    return response
