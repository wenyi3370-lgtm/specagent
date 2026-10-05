import logging
import os

import httpx

from ..models import AgentExecution
from ..trace import normalize_trace

logger = logging.getLogger("specagent.adapter.http")


async def run_http_agent(user_input: str, timeout_seconds: int | None = None) -> AgentExecution:
    """Adapter for a real agent endpoint configured by environment variable.

    Expected response shape:
    {
      "response": "text",
      "trace": [{"type":"tool_call", "name":"refund", "args":{...}}]
    }

    The endpoint is backend-configured (never accepted from the browser) to
    avoid SSRF; it must be reachable at TARGET_AGENT_URL.
    """
    url = os.getenv("TARGET_AGENT_URL")
    if not url:
        raise RuntimeError("TARGET_AGENT_URL is not configured")
    headers = {"Content-Type": "application/json"}
    token = os.getenv("TARGET_AGENT_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"  # noqa: S105 — token stays server-side
    timeout = timeout_seconds or int(os.getenv("TARGET_AGENT_TIMEOUT", "30"))
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, json={"message": user_input}, headers=headers)
        r.raise_for_status()
        data = r.json()
    return AgentExecution(
        response=data.get("response", ""),
        trace=normalize_trace(data.get("trace", [])),
        latency_ms=int(data.get("latency_ms") or 0),
    )
