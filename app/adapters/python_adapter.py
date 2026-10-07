"""Python adapter (v1 design §6.1, task 9): run a plain ``module:function``
callable in-process.

Contract: the callable receives keyword arguments filtered to what its
signature declares — ``message`` (required, or ``**kwargs``), plus optional
``history`` and ``actor``. It returns either an :class:`AgentExecution` or a
``dict`` with optional ``response``/``trace``/``latency_ms``; the trace goes
through the same normalize/redact/id pipeline as the HTTP adapter. Exceptions
propagate — the orchestrator converts them (and timeouts) into per-case ERROR
results; ``TransientAgentError`` is never raised, so in-process code is never
retried.

Timeout note: ``asyncio.wait_for`` around ``asyncio.to_thread`` cannot kill a
worker thread. After a timeout the run records ERROR and the thread may linger
until the function returns on its own.
"""
import asyncio
import inspect

from ..errors import SpecValidationError
from ..models import AgentExecution, TestCase, TraceEvent
from ..trace import normalize_trace_with_dropped
from .base import AgentAdapter, ExecutionContext


class PythonAdapter(AgentAdapter):
    name = "python"

    def __init__(self, fn, name: str):
        self.fn = fn
        self.name = f"python:{name}"  # label stored in runs.agent (§6.1)
        sig = inspect.signature(fn)
        self._var_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD
                               for p in sig.parameters.values())
        self._accepted = {p.name for p in sig.parameters.values()
                          if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)}

    @staticmethod
    def validate_signature(fn, path: str) -> None:
        """Factory-side check (§6.1): the callable must accept ``message``
        (a parameter with that name, or ``**kwargs``); ``history``/``actor``
        are optional."""
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError) as exc:
            raise SpecValidationError(
                [f"adapter.agent: cannot inspect signature of {path!r}: {exc}"]) from None
        params = sig.parameters.values()
        has_var_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)
        names = {p.name for p in params
                 if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)}
        if "message" not in names and not has_var_kwargs:
            raise SpecValidationError([
                f"adapter.agent: {path} must accept a 'message' parameter (or **kwargs); "
                f"got signature {sig}"])

    def _kwargs(self, case: TestCase) -> dict:
        candidates = {"message": case.user_input,
                      "history": list(case.history),
                      "actor": dict(case.actor)}
        if self._var_kwargs:
            return candidates
        return {k: v for k, v in candidates.items() if k in self._accepted}

    async def execute(self, case: TestCase, context: ExecutionContext) -> AgentExecution:
        kwargs = self._kwargs(case)
        if inspect.iscoroutinefunction(self.fn):
            raw = await asyncio.wait_for(self.fn(**kwargs), context.timeout_seconds)
        else:
            raw = await asyncio.wait_for(
                asyncio.to_thread(self.fn, **kwargs), context.timeout_seconds)
        if isinstance(raw, AgentExecution):
            raw = raw.model_dump()
        if not isinstance(raw, dict):
            raise TypeError(f"python agent must return AgentExecution or dict, "
                            f"got {type(raw).__name__}")
        trace = [e.model_dump() if isinstance(e, TraceEvent) else e
                 for e in (raw.get("trace") or [])]
        events, dropped = normalize_trace_with_dropped(trace)
        return AgentExecution(
            response=raw.get("response", ""),
            trace=events,
            dropped_events=dropped,
            latency_ms=int(raw.get("latency_ms") or 0),
            raw=raw,
        )
