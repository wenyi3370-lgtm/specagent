"""Private durable dashboard checkpoints. No client, credential or pickle data."""
import json
import os
import re
from dataclasses import asdict
from types import SimpleNamespace

from sqlalchemy import JSON, Float, String, delete, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .agent.loop import AgentSession, OfflineWorkflow, Transcript, _Parked, _redact_deep
from .agent.tools import PendingAction, Prepared
from .trace import _redact


class StateBase(DeclarativeBase):
    pass


class State(StateBase):
    __tablename__ = 'agent_session_checkpoints'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    config_key: Mapped[str] = mapped_column(String(64), index=True)
    updated: Mapped[float] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(JSON)


class Checkpoints:
    def __init__(self, store):
        self.store = store
        StateBase.metadata.create_all(store._engine)

    def load(self, sid):
        with self.store._session() as db:
            row = db.get(State, sid)
            return {**row.payload, 'last_used': row.updated} if row else None

    def list(self, key):
        with self.store._session() as db:
            return [{**r.payload, 'last_used': r.updated} for r in db.scalars(select(State).where(State.config_key == key).order_by(State.updated))]

    def save(self, payload):
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded.encode('utf-8')) > 2_000_000:
            raise ValueError('agent session checkpoint exceeds 2 MB')
        with self.store._session.begin() as db:
            db.merge(State(id=payload['session_id'], config_key=payload['config_key'],
                           updated=payload['last_used'], payload=payload))

    def remove(self, sid):
        with self.store._session.begin() as db:
            db.execute(delete(State).where(State.id == sid))

    def clear(self):
        with self.store._session.begin() as db:
            db.execute(delete(State))

    def touch(self, sid, now):
        with self.store._session.begin() as db:
            db.execute(update(State).where(State.id == sid).values(updated=now))


def snapshot(session):
    ctx = session.ctx
    state = {k: getattr(session, k) for k in ('session_id', 'mode', 'model', 'project_id',
        'owner_user', 'owner_login', 'owner_username', 'last_used', 'config_key', 'config_hash', 'revision')}
    state['resolved'] = sorted(session.resolved)
    state['log_id'] = session.transcript.session_id
    state['context'] = {k: getattr(ctx, k) for k in ('allow_source', 'last_run_id', 'read_hashes', 'read_notes', 'quotes')}
    state['actions'] = {key: asdict(action) for key, action in ctx.pending.items()}
    state['inflight'] = session.inflight
    if session.agent:
        agent = session.agent
        state['agent'] = {k: getattr(agent, k) for k in ('messages', 'refused', 'steps', 'max_steps', 'budget_seconds', 'last_resolution')}
        if agent.pending:
            state['agent']['parked'] = {**asdict(agent.pending), 'remaining_calls':
                [{'call_id': getattr(call, 'call_id', '')} for call in agent.pending.remaining_calls]}
        state['agent']['pending_id'] = getattr(agent.pending_action, 'action_id', None)
    if session.offline:
        flow = session.offline
        state['offline'] = {k: getattr(flow, k) for k in ('step', 'refused', 'errors', 'run_payload', 'triage_payload', '_goal', 'last_resolution')}
        state['offline']['pending_id'] = getattr(flow.pending_action, 'action_id', None)
    secrets = [value for name, value in os.environ.items()
               if value and re.search(r'TOKEN|KEY|SECRET|PASSWORD', name, re.I)]
    def scrub(value):
        if isinstance(value, str):
            for secret in sorted(secrets, key=len, reverse=True):
                value = (value.replace(secret, '[REDACTED]') if len(secret) >= 8 else
                         re.sub(r'(?<!\w)' + re.escape(secret) + r'(?!\w)', '[REDACTED]', value))
            return re.sub(r'(https?://)[^\s/@]+@', r'\1[REDACTED]@', value)
        if isinstance(value, dict):
            return {k: scrub(v) for k, v in value.items()}
        if isinstance(value, list):
            return [scrub(v) for v in value]
        return value
    safe = scrub(_redact_deep(_redact(state)))
    # Changed approval arguments must never execute after redaction on disk.
    if safe['actions'] != state['actions']:
        safe['redacted_actions'] = True
    return safe


def restore(session, state):
    session.resolved = set(state['resolved'])
    session.last_used = state['last_used']
    session.inflight = state.get('inflight')
    session.revision = state['revision']
    session.transcript = Transcript.resume(session.ctx.project.root, state['log_id'])
    session.ctx.transcript = session.transcript
    for k, v in state['context'].items():
        setattr(session.ctx, k, v)
    session.ctx.pending = {}
    for key, raw in state['actions'].items():
        raw = dict(raw)
        raw['prepared'] = Prepared(**raw['prepared']) if raw['prepared'] else None
        session.ctx.pending[key] = PendingAction(**raw)
    session.agent, session.offline = None, None
    if 'agent' in state:
        raw = state['agent']
        agent = AgentSession(session.ctx, session.registry, client=None, model=session.model,
                             transcript=session.transcript)
        for k in ('messages', 'refused', 'steps', 'max_steps', 'budget_seconds', 'last_resolution'):
            setattr(agent, k, raw[k])
        if raw.get('parked'):
            parked = dict(raw['parked'])
            parked['remaining_calls'] = [SimpleNamespace(**call) for call in parked['remaining_calls']]
            agent.pending = _Parked(**parked)
        agent.pending_action = session.ctx.pending.get(raw.get('pending_id'))
        session.agent = agent
    if 'offline' in state:
        raw = state['offline']
        flow = OfflineWorkflow(session.ctx, session.registry, session.transcript)
        for k, v in raw.items():
            if k != 'pending_id':
                setattr(flow, k, v)
        flow.pending_action = session.ctx.pending.get(raw.get('pending_id'))
        session.offline = flow
    if state.get('redacted_actions'):
        session.inflight = {'kind': 'redacted_approval', 'action_id': None}
