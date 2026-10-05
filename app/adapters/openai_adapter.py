"""OpenAI Responses API adapter (roadmap §7.3 P1): native support for agents
built directly on the OpenAI tool-calling loop.

The "agent" is an :class:`OpenAIAgentDefinition` — model + instructions + a
registry of tool executors. The adapter runs the function-calling loop and
emits normalized events (assistant_message / tool_call / tool_result). It
never judges; the deterministic judge sees the same trace as every other
adapter.

The OpenAI client is injectable so the loop is unit-testable offline.
"""
import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from ..models import AgentExecution, TestCase
from ..trace import normalize_trace
from .base import AgentAdapter, ExecutionContext

logger = logging.getLogger("specagent.adapter.openai")


@dataclass
class OpenAITool:
    name: str
    executor: Callable[[dict], Any] | None = None
    description: str = ""
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})


@dataclass
class OpenAIAgentDefinition:
    model: str = "gpt-4.1-mini"
    instructions: str = ""
    tools: list[OpenAITool] = field(default_factory=list)
    max_tool_rounds: int = 6


def _text_of(message_item: Any) -> str:
    content = getattr(message_item, "content", None) or []
    if isinstance(content, str):
        return content
    return "".join(getattr(part, "text", "") or "" for part in content)


def _raw_dict(response: Any) -> Any:
    dump = getattr(response, "model_dump", None)
    try:
        return dump() if callable(dump) else str(response)
    except Exception:  # noqa: BLE001 — raw is best-effort debug info only
        return None


class OpenAIResponsesAdapter(AgentAdapter):
    name = "openai"

    def __init__(self, definition: OpenAIAgentDefinition, client: Any = None):
        if not isinstance(definition, OpenAIAgentDefinition):
            raise TypeError(f"expected OpenAIAgentDefinition, got {type(definition).__name__}")
        self.definition = definition
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI()
        return self._client

    def _tool(self, name: str) -> OpenAITool | None:
        return next((t for t in self.definition.tools if t.name == name), None)

    async def execute(self, case: TestCase, context: ExecutionContext) -> AgentExecution:
        client = self._get_client()
        d = self.definition
        messages: list[dict] = [{"role": "user", "content": m} for m in case.history]
        messages.append({"role": "user", "content": case.user_input})
        trace: list[dict] = [{"type": "user_message", "name": "user_input",
                              "args": {"text": case.user_input}}]
        final = ""
        raw: Any = None

        for _ in range(max(1, d.max_tool_rounds)):
            kwargs: dict = {
                "model": d.model,
                "input": messages,
                "instructions": d.instructions or None,
            }
            if d.tools:
                kwargs["tools"] = [
                    {"type": "function", "name": t.name,
                     "description": t.description or None, "parameters": t.parameters}
                    for t in d.tools
                ]
            response = await asyncio.wait_for(
                asyncio.to_thread(lambda: client.responses.create(**kwargs)),
                timeout=context.timeout_seconds,
            )
            raw = response
            calls = [i for i in (response.output or []) if getattr(i, "type", "") == "function_call"]
            for item in response.output or []:
                if getattr(item, "type", "") == "message":
                    text = _text_of(item)
                    if text:
                        final += text
                        trace.append({"type": "assistant_message", "name": "reply",
                                      "args": {"text": text}})
            if not calls:
                break
            messages = messages + list(response.output)
            for call in calls:
                try:
                    args = json.loads(call.arguments or "{}")
                except json.JSONDecodeError:
                    args = {"_raw": call.arguments}
                trace.append({"type": "tool_call", "name": call.name, "args": args})
                output = self._run_tool(call.name, args)
                trace.append({"type": "tool_result", "name": call.name, "result": output})
                messages.append({
                    "type": "function_call_output",
                    "call_id": getattr(call, "call_id", ""),
                    "output": json.dumps(output, ensure_ascii=False, default=str),
                })

        return AgentExecution(
            response=final,
            trace=normalize_trace(trace),
            latency_ms=0,
            raw=_raw_dict(raw),
        )

    def _run_tool(self, name: str, args: dict) -> Any:
        tool = self._tool(name)
        if tool is None or tool.executor is None:
            return {"error": f"no executor registered for tool {name!r}"}
        try:
            return tool.executor(args)
        except Exception as exc:  # noqa: BLE001 — tool errors belong in the trace
            logger.warning("tool %s executor failed: %s", name, exc)
            return {"error": f"{type(exc).__name__}: {exc}"}
