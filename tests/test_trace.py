"""Unit tests: trace normalization, aliasing (H1), redaction, seq fill."""
from app.trace import normalize_trace


def test_type_aliases_are_normalized():
    events = normalize_trace([
        {"type": "tool", "name": "refund", "args": {"amount": 100}},
        {"type": "tool_use", "name": "query"},
        {"type": "message", "name": "final"},
    ])
    assert [e.type for e in events] == ["tool_call", "tool_call", "assistant_message"]


def test_unknown_event_type_dropped_not_fatal():
    events = normalize_trace([
        {"type": "tool_call", "name": "ok"},
        {"type": "mind_beam", "name": "?"},
        "garbage",
        {"no_type_at_all": True},
    ])
    assert len(events) == 1 and events[0].name == "ok"


def test_seq_filled_and_timestamped():
    events = normalize_trace([{"type": "tool_call", "name": "a"}, {"type": "tool_call", "name": "b"}])
    assert [e.seq for e in events] == [1, 2]
    assert all(e.timestamp for e in events)


def test_secrets_redacted():
    events = normalize_trace([{
        "type": "tool_call", "name": "call_api",
        "args": {"url": "https://x", "token": "sk-secret", "Authorization": "Bearer abc", "amount": 5},
    }])
    args = events[0].args
    assert args["token"] == "[REDACTED]"
    assert args["Authorization"] == "[REDACTED]"
    assert args["amount"] == 5 and args["url"] == "https://x"


def test_non_dict_args_wrapped():
    events = normalize_trace([{"type": "tool_call", "name": "x", "args": "raw"}])
    assert events[0].args == {"value": "raw"}


# --- v1 design §4: approval-result aliases, coercion, decision lookup -------


def test_approval_result_aliases():
    events = normalize_trace([
        {"type": "approval_response", "name": "a", "result": {"approved": True}},
        {"type": "human_approval_result", "name": "b", "result": {"approved": False}},
    ])
    assert [e.type for e in events] == ["approval_result", "approval_result"]


def test_approval_result_result_coercion():
    events = normalize_trace([
        {"type": "approval_result", "result": True},
        {"type": "approval_result", "result": {"approved": True}},
        {"type": "approval_result", "result": {"approved": "TRUE"}},
        {"type": "approval_result", "result": {"approved": "False"}},
        {"type": "approval_result", "result": {"approved": "yes", "status": "pending"}},
        {"type": "approval_result", "result": "nonsense"},
    ])
    assert events[0].result == {"approved": True}
    assert events[1].result == {"approved": True}
    assert events[2].result == {"approved": True}
    assert events[3].result == {"approved": False}
    assert events[4].result == {"status": "pending"}  # approved absent, keys kept
    assert events[5].result == {}


def test_coerce_approved_table():
    from app.trace import _coerce_approved
    assert _coerce_approved(True) is True and _coerce_approved(False) is False
    assert _coerce_approved("TRUE") is True and _coerce_approved("false") is False
    assert _coerce_approved(" true ") is True
    assert _coerce_approved(1) is None and _coerce_approved(None) is None
    assert _coerce_approved("yes") is None


def _approval_trace(tokens):
    """Build a normalized trace from §4.2 table tokens.

    R = approval request · D(x)/D(none-bool) = approval_result · V = unrelated
    tool call · G = the gated call."""
    events = []
    for token in tokens:
        if token == "R":
            events.append({"type": "approval_request", "name": "request_human_approval"})
        elif token == "V":
            events.append({"type": "tool_call", "name": "verify_identity", "args": {}})
        elif token == "G":
            events.append({"type": "tool_call", "name": "refund", "args": {"amount": 1200}})
        elif token.startswith("D(") and token.endswith(")"):
            inner = token[2:-1]
            result = {"status": "pending"} if inner == "none-bool" else {"approved": inner}
            events.append({"type": "approval_result", "name": "request_human_approval",
                           "result": result})
        else:  # pragma: no cover - table typo guard
            raise AssertionError(f"unknown token {token}")
    return normalize_trace(events)


import pytest  # noqa: E402


@pytest.mark.parametrize("tokens,expected", [
    (["R", "D(false)", "G"], False),
    (["R", "D(false)", "V", "G"], False),
    (["R", "D(false)", "R", "G"], False),
    (["R", "D(false)", "R", "D(true)", "G"], True),
    (["R", "D(true)", "D(false)", "G"], False),
    (["R", "D(true)", "G"], True),
    (["R", "G"], None),
    (["D(none-bool)", "G"], None),
])
def test_approval_decision_table(tokens, expected):
    from app.trace import approval_decision_before, tool_calls
    events = _approval_trace(tokens)
    gated = tool_calls(events)[-1]
    decision = approval_decision_before(events, gated)
    assert decision.approved is expected


def test_decision_records_evidence_ids():
    from app.trace import approval_decision_before, tool_calls
    events = _approval_trace(["R", "D(false)", "G"])
    decision = approval_decision_before(events, tool_calls(events)[-1])
    assert decision.approved is False
    assert decision.result_id == "evt_2"
    assert decision.request_id == "evt_1"


def test_decision_request_id_from_approval_tool_call():
    from app.trace import approval_decision_before, tool_calls
    events = normalize_trace([
        {"type": "tool_call", "name": "request_human_approval"},   # evt_1
        {"type": "approval_result", "name": "request_human_approval",
         "result": {"approved": True}},                            # evt_2
        {"type": "tool_call", "name": "refund", "args": {}},       # evt_3
    ])
    decision = approval_decision_before(events, tool_calls(events)[-1])
    assert decision.approved is True
    assert decision.request_id == "evt_1"


def test_decision_foreign_call_raises():
    import pytest as _pytest
    from app.models import TraceEvent
    from app.trace import approval_decision_before
    events = _approval_trace(["R", "G"])
    with _pytest.raises(ValueError, match="call is not part of events"):
        approval_decision_before(events, TraceEvent(type="tool_call", name="ghost"))


# --- handoff 2026-10-07 §1: `input` alias + dropped-event counting -----------


def test_anthropic_input_key_carries_args():
    """Anthropic-native tool_use puts arguments under `input`; without the
    alias the event survived with args={} and every parameter constraint
    went idle."""
    events = normalize_trace([
        {"type": "tool_use", "name": "transfer",
         "input": {"amount": 20000, "to": "ACC-9009"}},
    ])
    assert events[0].type == "tool_call"
    assert events[0].args == {"amount": 20000, "to": "ACC-9009"}


def test_args_alias_precedence_args_over_arguments_over_input():
    events = normalize_trace([
        {"type": "tool_call", "name": "a", "args": {"x": 1}, "arguments": {"x": 2}},
        {"type": "tool_call", "name": "b", "arguments": {"x": 2}, "input": {"x": 3}},
        {"type": "tool_call", "name": "c", "input": {"x": 3}},
        {"type": "tool_call", "name": "d", "args": {"x": 1}, "input": {"x": 3}},
    ])
    assert [e.args for e in events] == [{"x": 1}, {"x": 2}, {"x": 3}, {"x": 1}]


def test_normalize_trace_with_dropped_counts_and_keeps_seq():
    from app.trace import normalize_trace_with_dropped
    events, dropped = normalize_trace_with_dropped([
        {"type": "mind_beam", "name": "?"},
        {"type": "tool_call", "name": "ok", "args": {}},
        "garbage",
    ])
    assert dropped == 2
    assert [e.name for e in events] == ["ok"]
    assert events[0].seq == 2  # seq counts raw positions, not survivors
    events, dropped = normalize_trace_with_dropped([])
    assert events == [] and dropped == 0
