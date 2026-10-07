"""Trace normalization (roadmap 7.1 / known-issues H1; approval semantics in
v1 design §4).

Adapters hand in raw upstream events; everything downstream (judge, storage,
diff, dashboard) only ever sees normalized TraceEvent objects.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import get_args

from .models import APPROVAL_TOOLS, TraceEvent

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
    # v1 design §4.1: result-side approval aliases
    "approval_response": "approval_result",
    "human_approval_result": "approval_result",
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


def _coerce_approved(value) -> bool | None:
    """The single approval-decision coercion (v1 design §4.1, exported for the
    OpenAI adapter): a bool passes through; the strings "true"/"false"
    (case-insensitive) coerce to the bool; anything else is no decision."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def _normalize_approval_result(result):
    """`result` of an approval_result event becomes a dict holding a coerced
    boolean (§4.1): bare bool -> {"approved": bool}; a dict keeps its other
    keys (redacted as usual) with `approved` coerced or removed."""
    if isinstance(result, bool):
        return {"approved": result}
    if isinstance(result, dict):
        coerced = _coerce_approved(result.get("approved"))
        out = dict(result)
        if coerced is None:
            out.pop("approved", None)
        else:
            out["approved"] = coerced
        return out
    return {}


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
    # Anthropic-native tool_use carries arguments under `input`; without this
    # alias every parameter constraint would silently see args={} (handoff §1).
    args = raw.get("args") or raw.get("arguments") or raw.get("input") or {}
    if not isinstance(args, dict):
        args = {"value": args}
    result = _redact(raw.get("result")) if "result" in raw else None
    if rtype == "approval_result":
        result = _normalize_approval_result(result)
    return TraceEvent(
        id=str(raw.get("id") or f"evt_{seq}"),
        seq=raw.get("seq") or seq,
        type=rtype,
        name=str(name),
        args=_redact(args),
        result=result,
        timestamp=raw.get("timestamp") or _now_iso(),
        metadata=raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
    )


def normalize_trace_with_dropped(raw_events: list) -> tuple[list[TraceEvent], int]:
    """normalize_trace plus the number of dropped raw events. Adapters report
    the count on the execution so the judge can tell "upstream sent only
    unparseable events" (an ERROR — nothing was verified) apart from an
    intact empty trace (a legitimate refusal, §5.2 rule 3)."""
    events: list[TraceEvent] = []
    dropped = 0
    for seq, raw in enumerate(raw_events or [], 1):
        event = normalize_event(raw, seq)
        if event is None:
            dropped += 1
        else:
            events.append(event)
    return events, dropped


def normalize_trace(raw_events: list) -> list[TraceEvent]:
    return normalize_trace_with_dropped(raw_events)[0]


def ensure_ids(events: list[TraceEvent]) -> list[TraceEvent]:
    """Backfill seq/id on events constructed in-process (e.g. the demo agent),
    so every execution carries citable evidence ids regardless of adapter."""
    for index, event in enumerate(events, 1):
        if not event.seq:
            event.seq = index
        if not event.id:
            event.id = f"evt_{event.seq}"
    return events


def apply_limits(execution, max_trace_events: int, max_response_chars: int):
    """Roadmap §10.2 Trace Limit: clip oversized traces/responses and mark the
    execution ``truncated`` so nothing is silently dropped."""
    dropped = 0
    if max_trace_events and len(execution.trace) > max_trace_events:
        dropped = len(execution.trace) - max_trace_events
        execution.trace = execution.trace[:max_trace_events]
        logger.info("trace truncated for storage: dropped %s events", dropped)
    if max_response_chars and len(execution.response) > max_response_chars:
        execution.response = execution.response[:max_response_chars]
        logger.info("response truncated to %s chars", max_response_chars)
    if dropped or len(execution.response) >= (max_response_chars or 10**18):
        execution.truncated = True
    return execution


def tool_calls(events: list[TraceEvent]) -> list[TraceEvent]:
    return [e for e in events if e.type == "tool_call"]


@dataclass(frozen=True)
class ApprovalDecision:
    """Explicit approval decision for one gated call (v1 design §4.2).

    `approved` True/False is an explicit decision from an `approval_result`
    event; None means no explicit decision (the legacy judge's world)."""
    approved: bool | None
    result_id: str | None      # id of the deciding approval_result event
    request_id: str | None     # nearest approval request before that result (evidence only)


def _approved_of(event: TraceEvent) -> bool | None:
    result = event.result
    if isinstance(result, bool):
        return result
    if isinstance(result, dict):
        return _coerce_approved(result.get("approved"))
    return None


def approval_decision_before(events, call: TraceEvent) -> ApprovalDecision:
    """The one shared approval-decision lookup (v1 design §4.2, normative).

    Positions are list indices in the normalized trace (= emission order);
    `seq` is ignored because HTTP agents may send their own. Only
    `approval_result` events whose coerced `approved` is a bool decide; the
    latest such result wins. A later `approval_request`, a later approval-tool
    call, any other tool call, or a result without a bool never resets or
    masks an earlier decision. No bool-carrying result before the call ->
    `ApprovalDecision(None, None, None)` = exactly the legacy behavior."""
    idx = next((i for i, e in enumerate(events) if e is call), None)
    if idx is None:
        raise ValueError("call is not part of events")
    decision = ApprovalDecision(None, None, None)
    for i in range(idx):
        event = events[i]
        if event.type != "approval_result":
            continue
        approved = _approved_of(event)
        if approved is None:
            continue
        request_id = None
        for j in range(i - 1, -1, -1):
            prior = events[j]
            if prior.type == "approval_request" or (
                    prior.type == "tool_call" and prior.name in APPROVAL_TOOLS):
                request_id = prior.id or f"evt_{j + 1}"
                break
        decision = ApprovalDecision(approved, event.id or f"evt_{i + 1}", request_id)
    return decision
