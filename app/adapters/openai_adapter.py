"""OpenAI Responses API adapter (roadmap §7.3 P1): native support for agents
built directly on the OpenAI tool-calling loop.

The "agent" is an :class:`OpenAIAgentDefinition` — model + instructions + a
registry of tool executors. The adapter runs the function-calling loop and
emits normalized events (assistant_message / tool_call / tool_result). It
never judges; the deterministic judge sees the same trace as every other
adapter.

Actor identity (v1 design §5.4): probe cases carry a flat `actor` dict — the
session identity `arg_scope`/`role_allowed` constraints evaluate against.
Unlike the HTTP contract (`context` field) and the python adapter (`actor`
kwarg), an LLM agent has no call channel for it, so two opt-in paths exist:

  - ``include_actor_context`` prepends one system message ("session actor:
    {...}") to the model input, so the model can scope its own calls. Policy
    stays in the agent's instructions; this message carries only identity
    facts.
  - Tool executors that declare an ``actor`` parameter (or ``**kwargs``)
    receive ``case.actor`` — the backend-enforcement point (§6.1 convention,
    same signature-filtering rule as PythonAdapter). The judge still sees
    what the model *sent*: executor validation is a data-safety backstop,
    never a verdict fix.

The OpenAI client is injectable so the loop is unit-testable offline.
"""
import asyncio
import inspect
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from ..models import APPROVAL_TOOLS, AgentExecution, TestCase
from ..trace import _coerce_approved, normalize_trace_with_dropped
from .base import AgentAdapter, ExecutionContext

logger = logging.getLogger("specagent.adapter.openai")


def _accepts_actor(executor: Callable) -> bool:
    """§6.1 signature-filtering convention: an executor opts into the session
    identity by declaring an ``actor`` parameter (or ``**kwargs``)."""
    try:
        params = inspect.signature(executor).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.kind == inspect.Parameter.VAR_KEYWORD or p.name == "actor"
               for p in params)


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
    # Opt-in (v1 design §5.4): prepend a "session actor: {...}" system message
    # when the case carries an actor, so the model knows the session identity.
    include_actor_context: bool = False


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
        if d.include_actor_context and case.actor:
            messages.insert(0, {
                "role": "system",
                "content": "session actor: " + json.dumps(dict(case.actor), ensure_ascii=False),
            })
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
                output = self._run_tool(call.name, args, actor=case.actor)
                trace.append({"type": "tool_result", "name": call.name, "result": output})
                # Approval-result emission (v1 design §4.4): an approval tool
                # whose sandboxed executor returns a coercible `approved` value
                # produces the deciding approval_result event, in the order
                # tool_call → tool_result → approval_result.
                if (call.name in APPROVAL_TOOLS and isinstance(output, dict)
                        and "approved" in output):
                    approved = _coerce_approved(output["approved"])
                    if approved is not None:
                        trace.append({"type": "approval_result", "name": call.name,
                                      "result": {"approved": approved}})
                messages.append({
                    "type": "function_call_output",
                    "call_id": getattr(call, "call_id", ""),
                    "output": json.dumps(output, ensure_ascii=False, default=str),
                })

        events, dropped = normalize_trace_with_dropped(trace)
        return AgentExecution(
            response=final,
            trace=events,
            dropped_events=dropped,
            latency_ms=0,
            raw=_raw_dict(raw),
        )

    def _run_tool(self, name: str, args: dict, actor: dict | None = None) -> Any:
        tool = self._tool(name)
        if tool is None or tool.executor is None:
            return {"error": f"no executor registered for tool {name!r}"}
        try:
            if actor and _accepts_actor(tool.executor):
                return tool.executor(args, actor=dict(actor))
            return tool.executor(args)
        except Exception as exc:  # noqa: BLE001 — tool errors belong in the trace
            logger.warning("tool %s executor failed: %s", name, exc)
            return {"error": f"{type(exc).__name__}: {exc}"}
