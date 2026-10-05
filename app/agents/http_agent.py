import os
import httpx
from ..models import AgentExecution, TraceEvent


async def run_http_agent(user_input: str) -> AgentExecution:
    """Optional adapter for a real agent endpoint configured by environment variable.

    Expected response shape:
    {
      "response": "text",
      "trace": [{"type":"tool_call", "name":"refund", "args":{...}}]
    }
    """
    url = os.getenv("TARGET_AGENT_URL")
    if not url:
        raise RuntimeError("TARGET_AGENT_URL is not configured")
    headers = {"Content-Type": "application/json"}
    token = os.getenv("TARGET_AGENT_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, json={"message": user_input}, headers=headers)
        r.raise_for_status()
        data = r.json()
    return AgentExecution(
        response=data.get("response", ""),
        trace=[TraceEvent.model_validate(x) for x in data.get("trace", [])],
    )
