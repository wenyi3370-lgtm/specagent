"""HTTP/Webhook adapter (roadmap §7.3 P0): the universal contract.

Request/response shape (§14.3):
    POST <endpoint>  {"message": "...", "test_case_id": "...", "context": {},
                      "history": ["turn 1", ...]}          # history optional
    → {"response": "...", "trace": [{"seq":1,"type":"tool_call",...}], "latency_ms": 0}
"""
import logging
import os

import httpx

from ..models import AgentExecution, TestCase
from ..trace import normalize_trace
from .base import AgentAdapter, ExecutionContext

logger = logging.getLogger("specagent.adapter.http")


class HttpAdapter(AgentAdapter):
    name = "http"

    def __init__(self, endpoint_env: str = "TARGET_AGENT_URL", token_env: str = "TARGET_AGENT_TOKEN",
                 transport: httpx.AsyncBaseTransport | None = None):
        self.endpoint_env = endpoint_env
        self.token_env = token_env
        self._transport = transport  # injectable for contract tests

    async def execute(self, case: TestCase, context: ExecutionContext) -> AgentExecution:
        url = os.getenv(self.endpoint_env)
        if not url:
            raise RuntimeError(f"env {self.endpoint_env} is not configured")
        headers = {"Content-Type": "application/json"}
        token = os.getenv(self.token_env)
        if token:
            headers["Authorization"] = f"Bearer {token}"  # noqa: S105 — stays server-side
        payload = {"message": case.user_input, "test_case_id": case.id, "context": {}}
        if case.history:
            payload["history"] = case.history
        async with httpx.AsyncClient(timeout=context.timeout_seconds, transport=self._transport) as client:
            r = await client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
        return AgentExecution(
            response=data.get("response", ""),
            trace=normalize_trace(data.get("trace", [])),
            latency_ms=int(data.get("latency_ms") or 0),
            raw=data,
        )
