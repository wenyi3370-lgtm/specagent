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
