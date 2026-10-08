"""Opt-in accounts, expiring browser sessions and project memberships.

Provisioning is a local operator command. No password is accepted in argv.
The existing Store and CLI interfaces remain unchanged.
"""
import argparse
import getpass
import hashlib
import hmac
import re
import secrets
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import Boolean, Float, Integer, String, delete, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

SESSION_SECONDS = 8 * 3600
PASSWORD_ITERATIONS = 600_000
COOKIE = 'specagent_session'
principal_context = ContextVar('web_account', default=None)


class AccountBase(DeclarativeBase):
    pass


class User(AccountBase):
    __tablename__ = 'web_users'
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    admin: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class LoginSession(AccountBase):
    __tablename__ = 'web_login_sessions'
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    expires: Mapped[float] = mapped_column(Float)


class Membership(AccountBase):
    __tablename__ = 'web_project_memberships'
    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    role: Mapped[str] = mapped_column(String(16))


class LoginAttempt(AccountBase):
    __tablename__ = 'web_login_attempts'
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    start: Mapped[float] = mapped_column(Float)
    count: Mapped[int] = mapped_column(Integer)


@dataclass(frozen=True)
class Principal:
    user_id: str
    username: str
    admin: bool
    login_id: str  # Digest, never a credential.


def session_digest(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def csrf_token(token):
    return hmac.new(token.encode('utf-8'), b'specagent-csrf-v1', hashlib.sha256).hexdigest()


def password_hash(password):
    if not 12 <= len(password) <= 256:
        raise ValueError('password must contain 12 to 256 characters')
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, PASSWORD_ITERATIONS)
    return f'pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}'


def password_matches(password, encoded):
    try:
        method, iterations, salt, expected = encoded.split('$')
        if method != 'pbkdf2_sha256' or int(iterations) != PASSWORD_ITERATIONS:
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


# Unknown accounts perform the same KDF as existing accounts.
_DUMMY_HASH = f'pbkdf2_sha256${PASSWORD_ITERATIONS}$' + '00' * 16 + '$' + '00' * 32


class Accounts:
    def __init__(self, store):
        self.store = store
        AccountBase.metadata.create_all(store._engine)
        self.session = sessionmaker(bind=store._engine, expire_on_commit=False)

    def create_user(self, username, password, admin=False):
        if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{2,63}', username):
            raise ValueError('username must contain 3 to 64 lowercase ASCII letters, digits, dots, underscores or hyphens')
        value = password_hash(password)
        with self.session.begin() as db:
            if db.scalar(select(User).where(User.username == username)):
                raise ValueError('username already exists')
            user = User(id=uuid.uuid4().hex, username=username, password_hash=value, admin=bool(admin), enabled=True)
            db.add(user)
        return user.id

    def update_user(self, username, password=None, disable=False):
        value = password_hash(password) if password is not None else None
        with self.session.begin() as db:
            user = db.scalar(select(User).where(User.username == username).with_for_update())
            if user is None:
                raise ValueError('user not found')
            if value:
                user.password_hash = value
            if disable:
                user.enabled = False
            db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))

    def users(self):
        with self.session() as db:
            return [{'id': u.id, 'username': u.username, 'admin': u.admin, 'enabled': u.enabled}
                    for u in db.scalars(select(User).order_by(User.username))]

    def login(self, username, password, peer, old_token=None):
        now = time.time()
        # Account and source buckets both persist across worker/process restarts.
        keys = [session_digest('user:' + username), session_digest('peer:' + peer)]
        with self.session.begin() as db:
            for key in keys:
                # Atomic UPDATE is the counter; row locks serialize concurrent failures.
                row = db.get(LoginAttempt, key)
                if row is None:
                    # Concurrent first inserts are handled by the caller as unavailable,
                    # never by authenticating without the limiter.
                    db.add(LoginAttempt(key=key, start=now, count=1))
                    db.flush()
                else:
                    db.execute(update(LoginAttempt).where(LoginAttempt.key == key).values(
                        count=LoginAttempt.count + 1))
                    db.refresh(row)
                    if now - row.start >= 900:
                        row.start, row.count = now, 1
                    if row.count > 20:
                        raise HTTPException(429, 'login_rate_limited')
            user = db.scalar(select(User).where(User.username == username).with_for_update())
            matched = password_matches(password, user.password_hash if user else _DUMMY_HASH)
            if not user or not matched or not user.enabled:
                # Return after committing the counters, with a uniform failure.
                return None
            token = secrets.token_urlsafe(32)
            db.execute(delete(LoginSession).where(LoginSession.expires <= now))
            if old_token:
                db.execute(delete(LoginSession).where(LoginSession.digest == session_digest(old_token)))
            db.add(LoginSession(digest=session_digest(token), user_id=user.id, expires=now + SESSION_SECONDS))
            return token

    def authenticate(self, token):
        if not token or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            return None
        with self.session() as db:
            row = db.get(LoginSession, session_digest(token))
            if row is None or row.expires <= time.time():
                return None
            user = db.get(User, row.user_id)
            if user is None or not user.enabled:
                return None
            return Principal(user.id, user.username, user.admin, row.digest)

    def logout(self, token):
        with self.session.begin() as db:
            db.execute(delete(LoginSession).where(LoginSession.digest == session_digest(token)))

    def role(self, principal, project_id):
        if principal.admin:
            return 'admin'
        with self.session() as db:
            membership = db.get(Membership, (principal.user_id, project_id))
            return membership.role if membership else None

    def require_project(self, principal, project_id, edit=False):
        role = self.role(principal, project_id)
        if role is None or (edit and role not in ('editor', 'admin')):
            raise HTTPException(403, 'project_access_denied')

    def memberships(self, project_id):
        with self.session() as db:
            return [{'user_id': m.user_id, 'project_id': m.project_id, 'role': m.role}
                    for m in db.scalars(select(Membership).where(Membership.project_id == project_id))]

    def grant(self, user_id, project_id, role):
        if role not in ('viewer', 'editor'):
            raise ValueError('invalid project role')
        if not any(p['id'] == project_id for p in self.store.list_projects()):
            raise HTTPException(404, 'project_not_found')
        with self.session.begin() as db:
            if db.get(User, user_id) is None:
                raise HTTPException(404, 'user_not_found')
            item = db.get(Membership, (user_id, project_id))
            if item:
                item.role = role
            else:
                db.add(Membership(user_id=user_id, project_id=project_id, role=role))

    def revoke(self, user_id, project_id):
        with self.session.begin() as db:
            db.execute(delete(Membership).where(Membership.user_id == user_id, Membership.project_id == project_id))


class PrincipalMiddleware:
    """Keep account context inside a request, including threadpool handlers."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        token = principal_context.set(None)
        original_receive, original_send = receive, send
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await original_receive()
            received += len(message.get('body', b''))
            if received > 4096:
                raise HTTPException(413, 'login_body_too_large')
            return message

        async def no_cache_send(message):
            if message['type'] == 'http.response.start':
                headers = [(k, v) for k, v in message.get('headers', []) if k.lower() != b'cache-control']
                message = {**message, 'headers': headers + [(b'cache-control', b'no-store')]}
            await original_send(message)

        try:
            from .auth import auth_mode
            if scope['type'] == 'http' and scope.get('path', '').startswith('/api/') and (auth_mode() != 'shared' or scope.get('path', '').startswith('/api/notifications/')):
                send = no_cache_send
                if scope.get('path') == '/api/auth/login':
                    receive = bounded_receive
            await self.app(scope, receive, send)
        finally:
            principal_context.reset(token)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Local SpecAgent account provisioning')
    parser.add_argument('action', choices=['create-user', 'reset-password', 'disable-user', 'grant'])
    parser.add_argument('username')
    parser.add_argument('--admin', action='store_true')
    parser.add_argument('--project')
    parser.add_argument('--role', choices=['viewer', 'editor'], default='viewer')
    args = parser.parse_args(argv)
    from .storage import Store
    accounts = Accounts(Store())
    try:
        if args.action == 'grant':
            user = next((u for u in accounts.users() if u['username'] == args.username), None)
            if not user or not args.project:
                raise ValueError('existing user and --project are required')
            accounts.grant(user['id'], args.project, args.role)
        elif args.action == 'disable-user':
            accounts.update_user(args.username, disable=True)
        else:
            password = getpass.getpass('Password: ')
            if password != getpass.getpass('Confirm password: '):
                raise ValueError('passwords do not match')
            if args.action == 'create-user':
                accounts.create_user(args.username, password, args.admin)
            else:
                accounts.update_user(args.username, password=password)
        print('Account operation completed.')
        return 0
    except (ValueError, HTTPException) as exc:
        print(exc.detail if isinstance(exc, HTTPException) else str(exc))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
