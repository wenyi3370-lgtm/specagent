"""Operator-owned notification destinations and explicitly confirmed deliveries."""
import hashlib
import hmac
import json
import os
import re
import smtplib
import time
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import Boolean, Float, Integer, JSON, String, func, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .agent.sandbox import redact_text
from .web_presenters import web_payload

CONFIG_ENV = 'SPECAGENT_NOTIFICATION_CONFIG'
PREVIEW_SECONDS = 300


class Destination(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-z][a-z0-9_-]{0,39}$')
    name: str = Field(min_length=1, max_length=80)
    kind: Literal['webhook', 'email', 'pr_comment']
    url: str | None = None
    secret_env: str | None = None
    host: str | None = None
    port: int = Field(default=465, ge=1, le=65535)
    tls: Literal['ssl', 'starttls'] = 'ssl'
    sender: str | None = None
    recipients: list[str] = Field(default_factory=list, max_length=20)
    username: str | None = None
    password_env: str | None = None
    repository: str | None = None
    pull_number: int | None = Field(default=None, ge=1)
    token_env: str | None = None

    @model_validator(mode='after')
    def validate_destination(self):
        for value in (self.secret_env, self.password_env, self.token_env):
            if value and not re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', value):
                raise ValueError('invalid credential reference')
        if any('\r' in s or '\n' in s for s in [self.name, self.host or '', self.username or '', self.sender or '', *self.recipients]):
            raise ValueError('invalid header')
        if self.kind == 'webhook':
            parsed = urlsplit(self.url or '')
            if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or (parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1')):
                raise ValueError('invalid webhook destination')
        elif self.kind == 'email':
            if not self.host or not self.sender or not self.recipients:
                raise ValueError('incomplete email destination')
            for address in [self.sender, *self.recipients]:
                if not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+', address):
                    raise ValueError('invalid email address')
            if bool(self.username) != bool(self.password_env):
                raise ValueError('incomplete mail authentication')
        else:
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,39}/[A-Za-z0-9_.-]{1,100}', self.repository or '') or self.repository.split('/')[1] in ('.', '..') or not self.pull_number or not self.token_env:
                raise ValueError('incomplete PR destination')
        return self

    def credentials(self):
        return {key: os.getenv(value, '') for key, value in
                [('secret', self.secret_env), ('password', self.password_env), ('token', self.token_env)] if value}

    def ready(self):
        return all(value and '\r' not in value and '\n' not in value for value in self.credentials().values())

    def binding(self):
        data = {'profile': self.model_dump(), 'credentials': self.credentials()}
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def safe(self, value):
        private = [self.url, self.host, self.sender, self.username, *self.recipients, *self.credentials().values()]
        def clean(item):
            if isinstance(item, str):
                for secret in sorted((s for s in private if s), key=len, reverse=True):
                    item = item.replace(secret, '[redacted]')
                return redact_text(item)
            if isinstance(item, dict):
                return {key: clean(value) for key, value in item.items()}
            if isinstance(item, list):
                return [clean(value) for value in item]
            return item
        return clean(web_payload(value))

    def public(self):
        return {'id': self.id, 'name': redact_text(self.safe(self.name)), 'kind': self.kind, 'ready': self.ready()}


class NotificationConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    channels: list[Destination] = Field(default_factory=list, max_length=20)


def load_destinations():
    value = os.getenv(CONFIG_ENV, '').strip()
    if not value:
        return 'missing', {}
    try:
        path = Path(value).resolve()
        if path.name.lower() == '.env' or path.stat().st_size > 65536:
            return 'invalid', {}
        config = NotificationConfig.model_validate_json(path.read_text(encoding='utf-8'))
        if len({p.id for p in config.channels}) != len(config.channels):
            return 'invalid', {}
        return 'loaded', {p.id: p for p in config.channels}
    except (OSError, ValueError, ValidationError):
        return 'invalid', {}


class NotificationBase(DeclarativeBase):
    pass


class ChannelSetting(NotificationBase):
    __tablename__ = 'notification_channels'
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=0)


class Delivery(NotificationBase):
    __tablename__ = 'notification_deliveries'
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(String(64))
    channel_id: Mapped[str] = mapped_column(String(40))
    owner: Mapped[str] = mapped_column(String(64))  # Login digest or shared-token digest.
    actor: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16))
    created: Mapped[float] = mapped_column(Float)
    updated: Mapped[float] = mapped_column(Float)
    expires: Mapped[float] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(JSON)
    binding: Mapped[str] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(String(64), nullable=True)


def utc(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def summary(run):
    keys = ('id', 'project_id', 'status', 'passed', 'failed', 'errors', 'canceled', 'score', 'total')
    return {key: run.get(key, 0 if key not in ('id', 'project_id', 'status') else '') for key in keys}


def fingerprint(profile, revision, payload):
    text = json.dumps([profile.binding(), revision, payload], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


class Notifications:
    def __init__(self, store):
        self.store = store
        NotificationBase.metadata.create_all(store._engine)
        self.session = sessionmaker(bind=store._engine, expire_on_commit=False)

    def channels(self):
        state, profiles = load_destinations()
        with self.session() as db:
            settings = {s.id: s for s in db.scalars(select(ChannelSetting))}
            return {'state': state, 'channels': [{**p.public(), 'enabled': bool(settings.get(p.id) and settings[p.id].enabled)} for p in profiles.values()]}

    def configure(self, channel_id, enabled):
        _, profiles = load_destinations()
        profile = profiles.get(channel_id)
        if not profile:
            raise HTTPException(404, 'channel_not_found')
        if enabled and not profile.ready():
            raise HTTPException(409, 'channel_not_ready')
        with self.session.begin() as db:
            row = db.scalar(select(ChannelSetting).where(ChannelSetting.id == channel_id).with_for_update())
            if row is None:
                row = ChannelSetting(id=channel_id, enabled=enabled, revision=1)
                db.add(row)
            elif row.enabled != enabled:
                row.enabled, row.revision = enabled, row.revision + 1
        return self.channels()

    def get(self, delivery_id):
        with self.session() as db:
            return db.get(Delivery, delivery_id)

    def public(self, row):
        state = 'expired' if row.state == 'prepared' and row.expires <= time.time() else row.state
        return {'id': row.id, 'project_id': row.project_id, 'run_id': row.run_id, 'channel_id': row.channel_id,
                'actor': row.actor, 'state': state, 'created_at': utc(row.created), 'updated_at': utc(row.updated),
                'expires_at': utc(row.expires), 'summary': row.payload, 'error': row.error}

    def history(self, project_id, offset, limit):
        with self.session() as db:
            query = select(Delivery).where(Delivery.project_id == project_id)
            total = db.scalar(select(func.count()).select_from(Delivery).where(Delivery.project_id == project_id))
            rows = db.scalars(query.order_by(Delivery.created.desc(), Delivery.id.desc()).offset(offset).limit(limit))
            return {'items': [self.public(r) for r in rows], 'total': total, 'offset': offset, 'limit': limit, 'has_more': offset + limit < total}

    def preview(self, run_id, channel_id, owner, actor):
        run = self.store.get_run(run_id)
        if run is None:
            raise HTTPException(404, 'run_not_found')
        if run['status'] not in ('completed', 'canceled'):
            raise HTTPException(409, 'run_not_finished')
        _, profiles = load_destinations()
        profile = profiles.get(channel_id)
        with self.session.begin() as db:
            setting = db.get(ChannelSetting, channel_id)
            if not profile or not profile.ready() or not setting or not setting.enabled:
                raise HTTPException(409, 'channel_not_enabled')
            payload = profile.safe(summary(run))
            now = time.time()
            row = Delivery(id=uuid.uuid4().hex, project_id=run['project_id'], run_id=run_id, channel_id=channel_id,
                           owner=owner, actor=actor, state='prepared', created=now, updated=now,
                           expires=now + PREVIEW_SECONDS, payload=payload,
                           binding=fingerprint(profile, setting.revision, payload))
            db.add(row)
        return self.public(row)

    def send(self, delivery_id, run_id, owner, confirmed):
        if not confirmed:
            raise HTTPException(422, 'notification_confirmation_required')
        _, profiles = load_destinations()
        with self.session.begin() as db:
            row = db.scalar(select(Delivery).where(Delivery.id == delivery_id).with_for_update())
            if row is None or row.owner != owner:
                raise HTTPException(404, 'notification_not_found')
            if row.run_id != run_id:
                raise HTTPException(422, 'notification_confirmation_mismatch')
            if row.state != 'prepared':
                raise HTTPException(409, 'notification_already_attempted')
            run = self.store.get_run(run_id)
            profile, setting = profiles.get(row.channel_id), db.get(ChannelSetting, row.channel_id)
            if row.expires <= time.time() or not run or run['status'] not in ('completed', 'canceled') or not profile or not profile.ready() or not setting or not setting.enabled or fingerprint(profile, setting.revision, profile.safe(summary(run))) != row.binding:
                raise HTTPException(409, 'notification_preview_changed')
            # Conditional claim also serializes SQLite workers without row locks.
            claimed = db.execute(update(Delivery).where(Delivery.id == delivery_id, Delivery.state == 'prepared').values(state='sending', updated=time.time()))
            if claimed.rowcount != 1:
                raise HTTPException(409, 'notification_already_attempted')
            payload = dict(row.payload)
        # Claim commits before network I/O. Never retry automatically after ambiguity.
        try:
            deliver(profile, payload)
            outcome, error = 'sent', None
        except Exception:
            outcome, error = 'failed', 'delivery_failed_outcome_unknown'
        with self.session.begin() as db:
            db.execute(update(Delivery).where(Delivery.id == delivery_id).values(state=outcome, error=error, updated=time.time()))
        return self.public(self.get(delivery_id))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def post_json(url, body, headers):
    request = Request(url, data=body, headers={'Content-Type': 'application/json', **headers}, method='POST')
    with build_opener(NoRedirect()).open(request, timeout=10) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError('delivery_rejected')


def deliver(profile, payload):
    body = json.dumps({'event': 'specagent.run', 'run': payload}, sort_keys=True, ensure_ascii=False).encode()
    credentials = profile.credentials()
    if profile.kind == 'webhook':
        headers = {'X-SpecAgent-Event': 'run'}
        if profile.secret_env:
            headers['X-SpecAgent-Signature'] = 'sha256=' + hmac.new(credentials['secret'].encode(), body, hashlib.sha256).hexdigest()
        post_json(profile.url, body, headers)
    elif profile.kind == 'pr_comment':
        from .ci import _md
        text = '## SpecAgent run notification\n\n' + '\n'.join(f'- {_md(key)}: `{_md(value)}`' for key, value in payload.items()) + '\n'
        post_json(f'https://api.github.com/repos/{profile.repository}/issues/{profile.pull_number}/comments',
                  json.dumps({'body': text}).encode(), {'Authorization': 'Bearer ' + credentials['token'], 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})
    else:
        message = EmailMessage()
        message['Subject'] = 'SpecAgent run notification'
        message['From'], message['To'] = profile.sender, ', '.join(profile.recipients)
        message.set_content(json.dumps(payload, ensure_ascii=False, indent=2))
        import ssl
        factory = smtplib.SMTP_SSL if profile.tls == 'ssl' else smtplib.SMTP
        options = {'timeout': 10, **({'context': ssl.create_default_context()} if profile.tls == 'ssl' else {})}
        with factory(profile.host, profile.port, **options) as smtp:
            if profile.tls == 'starttls':
                smtp.starttls(context=ssl.create_default_context())
            if profile.username:
                smtp.login(profile.username, credentials['password'])
            if smtp.send_message(message):
                raise RuntimeError('mail_recipients_rejected')
