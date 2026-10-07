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

from fastapi import HTTPException, Request

TOKEN_ENV = "SPECAGENT_API_TOKEN"
AGENT_INSECURE_ENV = "SPECAGENT_AGENT_API_INSECURE"

logger = logging.getLogger("specagent.auth")


def configured_token() -> str | None:
    """Read per request, so tests can monkeypatch.setenv (design §3.4)."""
    value = os.getenv(TOKEN_ENV, "").strip()
    return value or None


def agent_api_enabled() -> bool:
    """Dashboard agent panel gate (v1 design §8.9): a configured token, or the
    explicit loopback-only opt-in."""
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


def require_api_token(request: Request) -> None:
    expected = configured_token()
    if expected is None:
        return  # local/no-token mode: unchanged behavior
    for supplied in _supplied_tokens(request):
        if hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
            return
    raise HTTPException(401, "invalid or missing API token",
                        headers={"WWW-Authenticate": "Bearer"})


_LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")


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
    if configured_token() is not None:
        return  # require_api_token already authenticated the caller
    if os.getenv(AGENT_INSECURE_ENV, "").strip() == "1":
        host = request.client.host if request.client else ""
        if host in _LOOPBACK_HOSTS and _host_header_allowed(request):
            logger.warning("agent API used WITHOUT authentication (loopback opt-in)")
            return
    raise HTTPException(403, "agent_api_requires_token: set SPECAGENT_API_TOKEN to use the dashboard agent")


def log_startup_warning(logger: logging.Logger) -> None:
    if configured_token() is None:
        logger.warning(
            "SPECAGENT_API_TOKEN is not set: /api/* is open to anyone who can reach this "
            "server. Fine for local use; set a token before exposing it.")
        if os.getenv(AGENT_INSECURE_ENV, "").strip() == "1":
            logger.warning(
                "SPECAGENT_AGENT_API_INSECURE=1: the dashboard agent API is enabled "
                "WITHOUT authentication for loopback clients")
