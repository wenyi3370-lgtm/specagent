"""Test environment isolation: deterministic, no LLM compiler, no external agent.

Set env BEFORE any test module imports app.main (which builds the Store).

The appended blocks below (v1 design §1.4) harden this further:
- `dotenv.load_dotenv` is replaced by a no-op so a developer's `.env` can never
  re-create auth/LLM variables after the pops above;
- the variables every reader treats as "unset when empty" are blanked to "";
- an autouse fixture makes any non-loopback name resolution or connection
  attempt raise, so an accidental network call fails the test loudly.
"""
import asyncio
import ipaddress
import os
import socket
import tempfile

_DB = os.path.join(tempfile.mkdtemp(prefix="specagent-tests-"), "test.db")
for suffix in ("", "-wal", "-shm"):
    try:
        os.remove(_DB + suffix)
    except FileNotFoundError:
        pass
os.environ["SPECAGENT_DB"] = _DB
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("TARGET_AGENT_URL", None)
os.environ.pop("TARGET_AGENT_TOKEN", None)

# --- v1 design §1.4 (append-only): .env isolation ---------------------------
# `app.main` calls `load_dotenv()` at import; it does not override existing
# variables but DOES re-create popped ones. Bind a no-op before any `app`
# import so the suite never reads any `.env` file.
import dotenv  # noqa: E402


def _load_dotenv_noop(*_args, **_kwargs) -> bool:
    return False


dotenv.load_dotenv = _load_dotenv_noop

# Readers use truthiness / `or` chains, so "" means "unset" (design §1.4).
for _var in (
    "SPECAGENT_API_TOKEN",
    "OPENAI_API_KEY",
    "TARGET_AGENT_URL",
    "TARGET_AGENT_TOKEN",
    "SPECAGENT_AGENT_MODEL",
    "SPECAGENT_PROJECT_CONFIG",
    "SPECAGENT_AGENT_API_INSECURE",
    "SPECAGENT_AUTH_MODE",
):
    os.environ[_var] = ""
# These must keep their *default* fallback values, so they are popped, not
# blanked: os.getenv("OPENAI_MODEL", "gpt-5.5") would return "" otherwise.
os.environ.pop("SPECAGENT_AUTH_MODE", None)
os.environ.pop("OPENAI_MODEL", None)
os.environ.pop("OPENAI_BASE_URL", None)

# --- v1 design §1.4 (append-only): no-outbound-network fixture --------------


def _is_loopback(address) -> bool:
    """Loopback allowance shared by all four guarded entry points: 127.0.0.0/8,
    ::1, "localhost", empty/None host, and AF_UNIX string paths (asyncio on
    Windows builds a loopback socketpair, which must keep working)."""
    host = address
    if isinstance(address, (tuple, list)):
        host = address[0] if address else ""
    if host is None or host == "":
        return True
    if isinstance(host, str) and host.startswith("/"):
        return True  # AF_UNIX path
    if not isinstance(host, str):
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


_real_getaddrinfo = socket.getaddrinfo
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_create_connection = asyncio.base_events.BaseEventLoop.create_connection
_NETWORK_MSG = "network access is disabled in tests"


def _guarded_getaddrinfo(host, *args, **kwargs):
    if not _is_loopback(host):
        raise RuntimeError(_NETWORK_MSG)
    return _real_getaddrinfo(host, *args, **kwargs)


def _guarded_connect(self, address):
    if not _is_loopback(address):
        raise RuntimeError(_NETWORK_MSG)
    return _real_connect(self, address)


def _guarded_connect_ex(self, address):
    if not _is_loopback(address):
        raise RuntimeError(_NETWORK_MSG)
    return _real_connect_ex(self, address)


def _guarded_create_connection(self, protocol_factory, host=None, port=None, *args, **kwargs):
    if not _is_loopback(host):
        raise RuntimeError(_NETWORK_MSG)
    return _real_create_connection(self, protocol_factory, host, port, *args, **kwargs)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_outbound_network(monkeypatch):
    """Make every non-loopback connection entry point raise before any I/O."""
    monkeypatch.setattr(socket, "getaddrinfo", _guarded_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _guarded_connect_ex)
    monkeypatch.setattr(asyncio.base_events.BaseEventLoop, "create_connection",
                        _guarded_create_connection)
    yield
