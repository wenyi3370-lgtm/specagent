"""Trace normalization (roadmap 7.1 / known-issues H1).

Adapters hand in raw upstream events; everything downstream (judge, storage,
diff, dashboard) only ever sees normalized TraceEvent objects.
"""
import logging
from datetime import datetime, timezone
from typing import get_args

from .models import TraceEvent

_EVENT_TYPES = set(get_args(TraceEvent.model_fields["type"].annotation))

logger = logging.getLogger("specagent.trace")

# Upstream implementations disagree on event type names; map common aliases.
_TYPE_ALIASES = {
    "tool": "tool_call",
    "tool_use": "tool_call",
    "function_call": "tool_call",
    "tool_output": "tool_result",
    "observation": "tool_result",
    "message": "assistant_message",
    "assistant": "assistant_message",
    "final_response": "assistant_message",
    "user": "user_message",
    "approval": "approval_request",
    "human_approval": "approval_request",
}

# Credential-looking keys are scrubbed before a trace is stored or displayed
# (roadmap 10.1: secret redaction).
_REDACTED_KEYS = (
    "authorization", "token", "access_token", "api_key", "apikey",
    "password", "passwd", "secret", "cookie", "set-cookie",
    "x-api-key", "private_key",
)
REDACTED = "[REDACTED]"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _redact(value):
    if isinstance(value, dict):
        return {k: (REDACTED if k.lower() in _REDACTED_KEYS and isinstance(v, (str, int, float)) else _redact(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def normalize_event(raw: dict, seq: int) -> TraceEvent | None:
    """Convert one raw upstream event into a TraceEvent, or drop it with a warning."""
    if not isinstance(raw, dict):
        logger.warning("dropping non-object trace event: %r", raw)
        return None
    rtype = str(raw.get("type", "")).strip().lower()
    rtype = _TYPE_ALIASES.get(rtype, rtype)
    if rtype not in _EVENT_TYPES:
        logger.warning("dropping trace event with unknown type %r (name=%r)", raw.get("type"), raw.get("name"))
        return None
    name = raw.get("name") or ""
    args = raw.get("args") or raw.get("arguments") or {}
    if not isinstance(args, dict):
        args = {"value": args}
    return TraceEvent(
        seq=raw.get("seq") or seq,
        type=rtype,
        name=str(name),
        args=_redact(args),
        result=_redact(raw.get("result")) if "result" in raw else None,
        timestamp=raw.get("timestamp") or _now_iso(),
        metadata=raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
    )


def normalize_trace(raw_events: list) -> list[TraceEvent]:
    events: list[TraceEvent] = []
    seq = 0
    for raw in raw_events or []:
        seq += 1
        event = normalize_event(raw, seq)
        if event is not None:
            events.append(event)
    return events


def tool_calls(events: list[TraceEvent]) -> list[TraceEvent]:
    return [e for e in events if e.type == "tool_call"]
