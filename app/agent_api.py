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

Sessions have durable private checkpoints (max 8 per project config, 1-hour idle
TTL). OS session locks serialize workers. Interrupted operations fail closed
rather than automatically replaying a possibly completed action.
"""
import logging
import threading
import time
import uuid
import hashlib
import os
from pathlib import Path
from collections import OrderedDict
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .agent.loop import AgentResult, AgentSession, OfflineWorkflow, Transcript
from .agent.sandbox import ProjectSandbox
from .agent.tools import ConfirmResult, ToolContext, ToolRegistry
from .auth import require_agent_enabled, require_api_token, auth_mode, account_repository
from .accounts import principal_context
from .errors import SpecValidationError
from .llm_client import make_client, resolve_model
# The config-path resolution is shared with the project-run API; the names
# stay importable from here for backward compatibility.
from .project import (DEFAULT_PROJECT_CONFIG, PROJECT_CONFIG_ENV, Project,
                      project_config_path)
from .storage import Store
from .baseline_audit import baseline_context, web_identity
from .agent_state import Checkpoints, snapshot, restore
from .process_lock import ProcessLock, lock_path

logger = logging.getLogger("specagent.agent_api")

MAX_SESSIONS = 8
SESSION_TTL_SECONDS = 3600
MAX_MESSAGE_CHARS = 4000

# Module-level hooks: tests replace them with monkeypatch.setattr.
client_factory = make_client
_clock = time.time  # Durable idle timestamps must survive process/OS restarts.

_store: Store | None = None
_checkpoints = None


def bind_store(store: Store) -> None:
    """``main.py`` passes the shared Store so dashboard runs show up in the
    dashboard's Run history (§8.9)."""
    global _store, _checkpoints
    _store = store
    _checkpoints = Checkpoints(store)


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


# -- local cache of durable sessions ---------------------------------------------------


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
    owner_user: str | None = None
    owner_login: str | None = None
    owner_username: str | None = None
    config_key: str = ''
    config_hash: str = ''
    revision: int = 0
    inflight: dict | None = None


_sessions: "OrderedDict[str, _DashSession]" = OrderedDict()
_sessions_lock = threading.RLock()


def reset_sessions(*, clear_persisted=True) -> None:
    """Drop every session (tests)."""
    with _sessions_lock:
        _sessions.clear()
        if clear_persisted and _checkpoints:
            _checkpoints.clear()


def _config_key():
    return hashlib.sha256(os.path.normcase(str(Path(_config_path()).resolve())).encode()).hexdigest()


def _session_lock(sid):
    return ProcessLock(lock_path(_config_path(), 'agent-' + sid))


def _save(session):
    session.revision += 1
    _checkpoints.save(snapshot(session))


def _restore_session(state):
    principal = principal_context.get()
    if auth_mode() == 'multiuser':
        if principal is None or state['owner_user'] != principal.user_id or state['owner_login'] != principal.login_id:
            raise HTTPException(404, 'session_not_found')
        account_repository().require_project(principal, state['project_id'], edit=True)
    project = _load_project()
    if state['config_key'] != _config_key() or state['config_hash'] != hashlib.sha256(project.config_bytes).hexdigest():
        raise HTTPException(409, 'session_configuration_changed: create a new session')
    allow_source = state['context']['allow_source']
    ctx = ToolContext(project=project, sandbox=ProjectSandbox(project.root, allow_source),
                      allow_source=allow_source, confirm=_deferred_confirm, track_progress=True)
    if principal:
        from .web_tools import AccountToolRegistry
        registry = AccountToolRegistry()
    else:
        registry = ToolRegistry()
    session = _DashSession(state['session_id'], state['mode'], state['model'], state['project_id'], ctx, registry, None)
    for k in ('owner_user', 'owner_login', 'owner_username', 'config_key', 'config_hash'):
        setattr(session, k, state[k])
    session.lock = _session_lock(session.session_id)
    restore(session, state)
    return session


def _refresh(session):
    # Caller holds the OS session lock. Refresh a checkpoint changed by another worker.
    state = _checkpoints.load(session.session_id)
    if state is None:
        raise HTTPException(404, 'session_not_found')
    if state['revision'] != session.revision:
        restored = _restore_session(state)
        for k, v in restored.__dict__.items():
            if k != 'lock':
                setattr(session, k, v)


def _prepare_operation(session):
    _refresh(session)
    if session.inflight:
        raise HTTPException(409, 'session_operation_uncertain: interrupted operation may have completed; inspect its results and create a new session')
    if session.agent and session.agent.client is None:
        session.agent.client = client_factory()
        if session.agent.client is None:
            raise HTTPException(409, 'llm_client_unavailable')


def _begin_operation(session, kind, action_id=None):
    session.inflight = {'kind': kind, 'action_id': action_id}
    _save(session)  # Commit intent before any model/tool/approved side effect.


def _finish_operation(session):
    session.inflight = None
    _save(session)


def _purge_expired(now: float) -> None:
    """Caller holds ``_sessions_lock``. A session with a request in flight is
    never purged."""
    for state in _checkpoints.list(_config_key()):
        sid = state['session_id']
        if now - state['last_used'] <= SESSION_TTL_SECONDS:
            continue
        lock = _sessions[sid].lock if sid in _sessions else _session_lock(sid)
        if lock.locked() or not lock.acquire(blocking=False):
            continue
        try:
            latest = _checkpoints.load(sid)
            if latest and now - latest['last_used'] > SESSION_TTL_SECONDS:
                _sessions.pop(sid, None)
                _checkpoints.remove(sid)
                logger.info("agent session %s expired (idle TTL)", sid)
        finally:
            lock.release()


def _register(session: _DashSession) -> None:
    """Insert a session, evicting the least recently used ones first.

    A session with a request in flight is never evicted: dropping it would make
    the follow-up approve/message 404 and lose the parked action (review #3).
    When every slot is busy the new session is rejected instead of silently
    killing someone's work.
    """
    with _sessions_lock, ProcessLock(lock_path(_config_path(), 'agent-table')):
        now = _clock()
        _purge_expired(now)
        states = _checkpoints.list(session.config_key)
        while len(states) >= MAX_SESSIONS:
            evicted = None
            for state in states:
                if auth_mode() == 'multiuser' and state['owner_user'] != session.owner_user:
                    continue
                sid = state['session_id']
                lock = _sessions[sid].lock if sid in _sessions else _session_lock(sid)
                if lock.locked() or not lock.acquire(blocking=False):
                    continue
                try:
                    _sessions.pop(sid, None)
                    _checkpoints.remove(sid)
                    evicted = sid
                finally:
                    lock.release()
                break
            if evicted is None:
                logger.warning("agent session %s rejected: all %d sessions busy",
                               session.session_id, MAX_SESSIONS)
                raise HTTPException(429, "too_many_sessions: every agent session is busy, retry shortly")
            states = _checkpoints.list(session.config_key)
            logger.info("agent session %s evicted (max %d sessions, %d busy)",
                        evicted, MAX_SESSIONS, len(_sessions))
        session.last_used = now
        _sessions[session.session_id] = session
        _save(session)


def _get_session(session_id: str) -> _DashSession:
    with _sessions_lock:
        _purge_expired(_clock())
        session = _sessions.get(session_id)
        state = _checkpoints.load(session_id)
        if state is None or state['config_key'] != _config_key():
            _sessions.pop(session_id, None)
            raise HTTPException(404, "session_not_found")
        if session is None:
            session = _restore_session(state)
            _sessions[session_id] = session
        if auth_mode() == 'multiuser':
            principal = principal_context.get()
            if principal is None or session.owner_user != principal.user_id or session.owner_login != principal.login_id:
                raise HTTPException(404, 'session_not_found')
            account_repository().require_project(principal, session.project_id, edit=True)
        session.last_used = _clock()
        _sessions.move_to_end(session_id)
        _checkpoints.touch(session_id, session.last_used)
        return session


def _touch(session: _DashSession) -> None:
    with _sessions_lock:
        session.last_used = _clock()
        _checkpoints.touch(session.session_id, session.last_used)
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
                      transcript=transcript, track_progress=True)
    if principal_context.get():
        from .web_tools import AccountToolRegistry
        registry = AccountToolRegistry()
    else:
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
    principal = principal_context.get()
    if principal:
        session.owner_user, session.owner_login = principal.user_id, principal.login_id
        session.owner_username = principal.username
    session.config_key = _config_key()
    session.config_hash = hashlib.sha256(project.config_bytes).hexdigest()
    session.lock = _session_lock(session_id)
    _register(session)
    transcript.emit("dashboard_session", {"session_id": session_id, "mode": session.mode,
                                            "model": session.model, "project": session.project_id,
                                            **({'owner_user': session.owner_user} if principal else {})})
    logger.info("agent session %s created (mode=%s, project=%s)",
                session_id, session.mode, session.project_id)
    return {"session_id": session_id, "mode": session.mode, "model": session.model,
            "project": session.project_id}


@router.post("/sessions/{session_id}/messages")
def post_message(session_id: str, req: MessageRequest):
    session = _get_session(session_id)
    with session.lock:
        _prepare_operation(session)
        return _message_locked(session, req)


def _message_locked(session, req):
    if _current_pending(session) is not None:
        raise HTTPException(
            409, "pending_action_unresolved: approve or decline the pending action first")
    _begin_operation(session, 'message')
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
    _finish_operation(session)
    return response



@router.post("/sessions/{session_id}/approve")
def approve_action(session_id: str, req: ApproveRequest):
    session = _get_session(session_id)
    with session.lock:
        _prepare_operation(session)
        return _approve_locked(session, req)


def _approve_locked(session, req):
    if getattr(session, 'owner_user', None):
        # A stream worker does not inherit ContextVars automatically.
        with baseline_context(source='agent', actor=session.owner_username, identity='account_user'):
            return _approve_with_identity(session, req)
    with baseline_context(**web_identity('agent')):
        return _approve_with_identity(session, req)


def _approve_with_identity(session, req):
    if req.action_id in session.resolved:
        raise HTTPException(409, "action_already_resolved")
    pending = _current_pending(session)
    if pending is None or pending.action_id != req.action_id:
        raise HTTPException(404, "action_not_found")
    tool = pending.tool
    _begin_operation(session, 'approval', req.action_id)
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
        **({"run_id": outcome["run_id"]} if tool in {"run_suite", "verify_fix"} and outcome.get("ok") and outcome.get("run_id") else {}),
        **({"suggestion_id": outcome["id"]} if tool == "write_fix_suggestion" and outcome.get("ok") and outcome.get("id") else {})})
    session.transcript.emit("dashboard_result", response)
    _finish_operation(session)
    return response
