"""HTTP/Webhook adapter (roadmap §7.3 P0): the universal contract.

Request/response shape (§14.3):
    POST <endpoint>  {"message": "...", "test_case_id": "...", "context": {},
                      "history": ["turn 1", ...]}          # history optional
    → {"response": "...", "trace": [{"seq":1,"type":"tool_call",...}], "latency_ms": 0}

Security (roadmap §10.1):
  - the endpoint comes from backend environment variables only — the browser
    can never submit a URL (SSRF-safe by construction);
  - an optional hostname allowlist (project config or SPECAGENT_ALLOWED_HOSTS)
    further constrains which hosts may be contacted.
"""
import logging
import os
from urllib.parse import urlparse

import httpx

from ..models import AgentExecution, TestCase
from ..trace import normalize_trace
from .base import AgentAdapter, ExecutionContext, TransientAgentError

logger = logging.getLogger("specagent.adapter.http")


def host_allowed(hostname: str | None, allowlist: list[str]) -> bool:
    """Exact-host match, case-insensitive; entries may carry a port or use a
    ``*.suffix`` wildcard."""
    if not hostname:
        return False
    hostname = hostname.lower()
    for entry in allowlist:
        entry = entry.strip().lower().removeprefix("http://").removeprefix("https://").rstrip("/")
        if not entry:
            continue
        if entry.startswith("*."):
            suffix = entry[1:]  # ".example.com"
            if hostname.endswith(suffix) and hostname != suffix[1:]:
                return True
        elif hostname == entry.split(":")[0] or hostname == entry:
            return True
    return False


class HttpAdapter(AgentAdapter):
    name = "http"

    def __init__(self, endpoint_env: str = "TARGET_AGENT_URL", token_env: str = "TARGET_AGENT_TOKEN",
                 allowed_hosts: list[str] | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.endpoint_env = endpoint_env
        self.token_env = token_env
        merged = list(allowed_hosts or [])
        env_allowlist = os.getenv("SPECAGENT_ALLOWED_HOSTS", "")
        merged += [h.strip() for h in env_allowlist.split(",") if h.strip()]
        self.allowed_hosts = merged
        self._transport = transport  # injectable for contract tests

    async def execute(self, case: TestCase, context: ExecutionContext) -> AgentExecution:
        url = os.getenv(self.endpoint_env)
        if not url:
            raise RuntimeError(f"env {self.endpoint_env} is not configured")
        if self.allowed_hosts:
            hostname = urlparse(url).hostname
            if not host_allowed(hostname, self.allowed_hosts):
                raise RuntimeError(
                    f"endpoint host {hostname!r} is not in the allowlist "
                    f"({', '.join(self.allowed_hosts)}) — refusing to call it"
                )
        headers = {"Content-Type": "application/json"}
        token = os.getenv(self.token_env)
        if token:
            headers["Authorization"] = f"Bearer {token}"  # noqa: S105 — stays server-side
        payload = {"message": case.user_input, "test_case_id": case.id, "context": {}}
        if case.history:
            payload["history"] = case.history
        async with httpx.AsyncClient(timeout=context.timeout_seconds, transport=self._transport) as client:
            try:
                r = await client.post(url, json=payload, headers=headers)
                r.raise_for_status()
            except httpx.TransportError as exc:
                raise TransientAgentError(f"transport error: {exc}") from exc
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code >= 500:
                    raise TransientAgentError(
                        f"upstream {exc.response.status_code}") from exc
                raise
            data = r.json()
        return AgentExecution(
            response=data.get("response", ""),
            trace=normalize_trace(data.get("trace", [])),
            latency_ms=int(data.get("latency_ms") or 0),
            raw=data,
        )
