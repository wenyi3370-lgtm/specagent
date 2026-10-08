"""API token authentication (v1 design §3.4).

Local/no-token mode is unchanged: when `SPECAGENT_API_TOKEN` is unset (or
blank), `require_api_token` is a no-op and every endpoint behaves exactly as
before. With a token configured, every `/api/*` route except `/api/health`
requires either `Authorization: Bearer <token>` or `X-API-Key: <token>`.

Decisions pinned by the design:
- bytes-level `hmac.compare_digest`, so non-ASCII headers cannot raise;
- one uniform 401 message for missing vs wrong (no oracle);
- the token is never logged and never echoed;
- an empty/whitespace env value means "unset";
- `/docs`, `/openapi.json`, `/`, `/static/*` stay open (they expose no data).

Dashboard agent API (v1 design §8.9): `/api/agent/*` additionally depends on
`require_agent_enabled` — without a token it is 403, unless the explicit
loopback-only opt-in `SPECAGENT_AGENT_API_INSECURE=1` is set (logged at
WARNING at startup and per request).
"""
import hmac
import logging
import os
import threading

from fastapi import HTTPException, Request

TOKEN_ENV = "SPECAGENT_API_TOKEN"
AGENT_INSECURE_ENV = "SPECAGENT_AGENT_API_INSECURE"

logger = logging.getLogger("specagent.auth")
_store_provider = None
_repository_lock = threading.Lock()


def auth_mode():
    value = os.getenv('SPECAGENT_AUTH_MODE', 'shared').strip()
    return value if value in ('shared', 'multiuser') else 'invalid'


def bind_account_store(provider):
    global _store_provider
    _store_provider = provider


def account_repository():
    from .accounts import Accounts
    if _store_provider is None:
        raise HTTPException(503, 'account_store_unavailable')
    store = _store_provider()
    with _repository_lock:
        repository = getattr(store, '_web_accounts', None)
        if repository is None:
            repository = Accounts(store)
            store._web_accounts = repository
        return repository


def configured_token() -> str | None:
    """Read per request, so tests can monkeypatch.setenv (design §3.4)."""
    value = os.getenv(TOKEN_ENV, "").strip()
    return value or None


def agent_api_enabled() -> bool:
    """Dashboard agent panel gate (v1 design §8.9): a configured token, or the
    explicit loopback-only opt-in."""
    if auth_mode() != 'shared':
        return auth_mode() == 'multiuser'
    return configured_token() is not None or os.getenv(AGENT_INSECURE_ENV, "").strip() == "1"


def _supplied_tokens(request: Request) -> list[str]:
    tokens = []
    auth_header = request.headers.get("authorization", "")
    scheme, _, credential = auth_header.partition(" ")
    if scheme.lower() == "bearer" and credential.strip():
        tokens.append(credential.strip())
    api_key = request.headers.get("x-api-key", "").strip()
    if api_key:
        tokens.append(api_key)
    return tokens


async def require_api_token(request: Request) -> None:
    mode = auth_mode()
    if mode == 'invalid':
        raise HTTPException(503, 'invalid_auth_mode')
    if mode == 'multiuser':
        if request.url.path == '/api/auth/login' and request.method == 'POST':
            return
        from .accounts import COOKIE, csrf_token, principal_context
        from .web_access import authorize
        from starlette.concurrency import run_in_threadpool
        try:
            repository = await run_in_threadpool(account_repository)
            token = request.cookies.get(COOKIE, '')
            principal = await run_in_threadpool(repository.authenticate, token)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, 'account_store_unavailable') from None
        if principal is None:
            raise HTTPException(401, 'login_required')
        principal_context.set(principal)
        request.state.principal = principal
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            require_same_origin(request)
            if not hmac.compare_digest(request.headers.get('x-specagent-csrf', '').encode(), csrf_token(token).encode()):
                raise HTTPException(403, 'csrf_required')
        await authorize(request, repository, principal)
        return
    expected = configured_token()
    if expected is None:
        return  # local/no-token mode: unchanged behavior
    for supplied in _supplied_tokens(request):
        if hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
            return
    raise HTTPException(401, "invalid or missing API token",
                        headers={"WWW-Authenticate": "Bearer"})


_LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")


def require_same_origin(request):
    origin = request.headers.get('origin')
    expected = str(request.base_url).rstrip('/')
    if (origin is not None and origin != expected) or request.headers.get('sec-fetch-site') == 'cross-site':
        raise HTTPException(403, 'same_origin_required')


def _host_header_allowed(request: Request) -> bool:
    """The opt-in mode has no token, so the ``Host`` header is the only thing
    left that a DNS-rebinding page cannot forge: the browser keeps sending the
    attacker's own hostname even after the name resolves to 127.0.0.1. Accept
    loopback names with or without a numeric port, plus the IPv6 forms
    (``[::1]`` / ``[::1]:8765`` and the bare ``::1``)."""
    raw = request.headers.get("host", "").strip()
    if not raw:
        return False
    if raw.startswith("["):                       # [::1] or [::1]:8765
        host, sep, port = raw[1:].partition("]")
        if not sep:
            return False
        host, port = host.strip(), port.strip()
        if port and not port.startswith(":"):
            return False
        port = port.lstrip(":")
    elif raw.count(":") == 1:                     # host or host:port
        host, _, port = raw.partition(":")
        host, port = host.strip(), port.strip()
        if port and not port.isdigit():
            return False
    else:                                         # bare IPv6, e.g. ::1
        host, port = raw, ""
    if port and not port.isdigit():
        return False
    return host.lower() in _LOOPBACK_HOSTS


def require_agent_enabled(request: Request) -> None:
    """Second gate of `/api/agent/*` (v1 design §8.9); runs after
    `require_api_token`, so 401 comes before 403."""
    if auth_mode() == 'multiuser':
        if getattr(request.state, 'principal', None) is None:
            raise HTTPException(401, 'login_required')
        return
    if auth_mode() != 'shared':
        raise HTTPException(503, 'invalid_auth_mode')
    if configured_token() is not None:
        return  # require_api_token already authenticated the caller
    if os.getenv(AGENT_INSECURE_ENV, "").strip() == "1":
        host = request.client.host if request.client else ""
        if host in _LOOPBACK_HOSTS and _host_header_allowed(request):
            logger.warning("agent API used WITHOUT authentication (loopback opt-in)")
            return
    raise HTTPException(403, "agent_api_requires_token: set SPECAGENT_API_TOKEN to use the dashboard agent")


def log_startup_warning(logger: logging.Logger) -> None:
    if auth_mode() == 'multiuser':
        logger.info('Account authentication enabled; shared API tokens do not authenticate this mode')
        return
    if auth_mode() == 'invalid':
        logger.error('Invalid SPECAGENT_AUTH_MODE: API access is disabled')
        return
    if configured_token() is None:
        logger.warning(
            "SPECAGENT_API_TOKEN is not set: /api/* is open to anyone who can reach this "
            "server. Fine for local use; set a token before exposing it.")
        if os.getenv(AGENT_INSECURE_ENV, "").strip() == "1":
            logger.warning(
                "SPECAGENT_AGENT_API_INSECURE=1: the dashboard agent API is enabled "
                "WITHOUT authentication for loopback clients")
