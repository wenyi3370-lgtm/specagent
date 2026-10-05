"""LangGraph adapter (roadmap §7.3 P1): wraps a compiled LangGraph graph and
converts ``astream_events`` output into the unified trace schema.

The graph object is duck-typed (anything with ``astream_events``), so this
adapter itself has zero hard dependency on langgraph — the user's project
supplies the compiled graph via ``adapter.agent: module:attribute``.
"""
import logging
from typing import Any

from ..models import AgentExecution, TestCase
from ..trace import normalize_trace
from .base import AgentAdapter, ExecutionContext

logger = logging.getLogger("specagent.adapter.langgraph")


def _as_args(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    return {"value": value}


def _message_text(output: Any) -> str:
    content = getattr(output, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part if isinstance(part, str) else str(part) for part in content)
    if isinstance(output, dict):
        return str(output.get("content", "") or "")
    return ""


class LangGraphAdapter(AgentAdapter):
    name = "langgraph"

    def __init__(self, graph: Any):
        if not hasattr(graph, "astream_events"):
            raise TypeError("LangGraphAdapter expects a compiled graph with astream_events()")
        self.graph = graph

    async def execute(self, case: TestCase, context: ExecutionContext) -> AgentExecution:
        messages = [("user", m) for m in case.history] + [("user", case.user_input)]
        trace: list[dict] = [{"type": "user_message", "name": "user_input",
                              "args": {"text": case.user_input}}]
        final = ""
        event_count = 0
        async for event in self.graph.astream_events({"messages": messages}, version="v2"):
            event_count += 1
            kind = event.get("event")
            if kind == "on_tool_start":
                trace.append({"type": "tool_call", "name": event.get("name") or "tool",
                              "args": _as_args((event.get("data") or {}).get("input"))})
            elif kind == "on_tool_end":
                trace.append({"type": "tool_result", "name": event.get("name") or "tool",
                              "result": (event.get("data") or {}).get("output")})
            elif kind == "on_chat_model_end":
                text = _message_text((event.get("data") or {}).get("output"))
                if text:
                    final = text
                    trace.append({"type": "assistant_message", "name": "reply",
                                  "args": {"text": text}})
        return AgentExecution(
            response=final,
            trace=normalize_trace(trace),
            latency_ms=0,
            raw={"astream_events": event_count},
        )
