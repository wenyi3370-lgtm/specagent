"""Constraint evaluator tests (v1 design §5.2/§5.3, task 6).

The helper table (`test_helper_table`) pins every `_to_number`/`_values_equal`
row; per-type tests cover pass / violate / refusing agent / when hit / when
miss / missing arg; require_before uses ALL semantics; the evaluator is pure
and fail-closed.
"""
import asyncio

import pytest

from app.constraints import (
    _to_number, _values_equal, _when_matches, evaluate_constraints, render_violation)
from app.judge import judge
from app.models import AgentExecution, BehaviorRule, TestCase, TraceEvent, WhenClause


# --- helper tables (design §5.2 rule 5) --------------------------------------

@pytest.mark.parametrize("value,expected", [
    (True, None), (False, None),          # bool checked first: True is never 1
    (0, 0), (7, 7), (-3, -3),
    (2.5, 2.5),
    (float("inf"), None), (float("nan"), None),
    ("1000", 1000), (" 7 ", 7), (".5", 0.5), ("5.", 5.0), ("+3", 3), ("-0.25", -0.25),
    ("1e3", None), ("1,000", None), ("0x10", None), ("NaN", None), ("inf", None),
    ("", None), ("12abc", None), ("١٢", None),
    (None, None), ([1], None), ({"a": 1}, None),
])
def test_helper_table_to_number(value, expected):
    assert _to_number(value) == expected
    if expected is None:
        assert _to_number(value) is None


@pytest.mark.parametrize("a,b,expected", [
    (True, True, True), (True, 1, False), (True, "true", False), (False, 0, False),
    ("10", 10.0, True), ("1000", 1000, True), ("10", "10.0", True),
    ("abc", "abc ", True), ("abc", "ABC", False),
    (None, None, True), ([1], [1], True), ("abc", 5, False),
    ("1", True, False),
])
def test_helper_table_values_equal(a, b, expected):
    assert _values_equal(a, b) is expected


@pytest.mark.parametrize("args,expected", [
    ({"amount": 20000}, True),
    ({"amount": 500}, False),
    ({}, False),                                   # missing arg = miss
    ({"amount": "lots"}, True),                    # non-numeric ordering = matches (fail-closed)
    ({"amount": None}, True),
    ({"amount": "1000"}, False),                   # numeric string compares numerically: 1000 > 10000 is False
])
def test_when_ordering_ops(args, expected):
    when = WhenClause(arg="amount", op=">", value=10000)
    assert _when_matches(when, args) is expected


@pytest.mark.parametrize("op,value,args,expected", [
    ("==", "ACC-1", {"account_id": "acc-1"}, False),    # case-sensitive
    ("==", "acc-1", {"account_id": "acc-1"}, True),
    ("==", "ACC-1", {"account_id": "acc-1 "}, False),   # strip, case-SENSITIVE
    ("==", True, {"flag": "true"}, False),              # booleans strict
    ("==", 1, {"flag": "1"}, True),                     # numeric string equality
    ("!=", "x", {"user_id": "y"}, True),
    ("in", ["zh", "en"], {"lang": "zh"}, True),
    ("in", ["zh", "en"], {"lang": "fr"}, False),
    ("in", ["zh"], {"other": "zh"}, False),             # missing arg = miss
])
def test_when_equality_ops(op, value, args, expected):
    when = WhenClause(arg={"account_id": "account_id", "flag": "flag",
                           "user_id": "user_id", "lang": "lang", "other": "lang"}[list(args)[0] if list(args) else "flag"],
                      op=op, value=value)
    assert _when_matches(when, args) is expected


# --- fixtures ----------------------------------------------------------------


def _exec(*events):
    trace = [TraceEvent(seq=i + 1, id=f"evt_{i + 1}", **e) for i, e in enumerate(events)]
    return AgentExecution(response="ok", trace=trace)


_CALL = {"type": "tool_call", "name": "refund", "args": {"amount": 1200, "account_id": "ACC-2"}}
_APPROVED = {"type": "tool_call", "name": "request_human_approval", "args": {}}


def _evaluate(constraint, events, actor=None):
    trace = events.trace if isinstance(events, AgentExecution) else events
    return [(v.kind, v.message, v.evidence, v.arg)
            for v in evaluate_constraints([constraint], trace, actor)]


# --- require_before: ALL semantics -------------------------------------------


def test_require_before_all_semantics_single_missing():
    from app.models import RequireBefore
    c = RequireBefore(type="require_before", tool="refund", prerequisites=["verify_identity", "request_human_approval"])
    events = _exec(_APPROVED, _CALL)  # verify_identity missing
    out = _evaluate(c, events)
    assert out == [("missing_prerequisite", "executed before required prerequisite: verify_identity",
                    ("evt_2",), None)]


def test_require_before_both_missing_two_violations_in_list_order():
    from app.models import RequireBefore
    c = RequireBefore(type="require_before", tool="refund", prerequisites=["verify_identity", "request_human_approval"])
    out = _evaluate(c, _exec(_CALL))
    assert [o[1] for o in out] == [
        "executed before required prerequisite: verify_identity",
        "executed before required prerequisite: request_human_approval",
    ]


def test_require_before_mixed_kinds_and_prerequisite_satisfied():
    from app.models import RequireBefore
    c = RequireBefore(type="require_before", tool="refund", prerequisites=["request_human_approval", "verify_identity"])
    events = _exec(_APPROVED, _CALL)  # approval present, verify missing
    out = _evaluate(c, events)
    assert [o[0] for o in out] == ["missing_prerequisite"]  # approval tool_call present → satisfied


def test_require_before_denial_checked_only_with_approval_prerequisite():
    from app.models import RequireBefore
    c = RequireBefore(type="require_before", tool="refund", prerequisites=["request_human_approval"])
    denial = {"type": "approval_result", "name": "request_human_approval", "result": {"approved": False}}
    out = _evaluate(c, _exec(denial, _CALL))
    kinds = [o[0] for o in out]
    assert kinds == ["missing_approval", "approval_denial".replace("denial", "denied")] or kinds == ["missing_approval", "approval_denied"]
    # evidence: offending call, deciding result, request (request absent → dropped)
    assert out[1][2] == ("evt_2", "evt_1")


def test_require_before_denial_ignored_without_approval_prerequisite():
    from app.models import RequireBefore
    c = RequireBefore(type="require_before", tool="refund", prerequisites=["verify_identity"])
    denial = {"type": "approval_result", "name": "request_human_approval", "result": {"approved": False}}
    out = _evaluate(c, _exec(denial, _CALL))
    assert [o[0] for o in out] == ["missing_prerequisite"]


def test_require_before_when_miss_skips_call():
    from app.models import RequireBefore
    c = RequireBefore(type="require_before", tool="refund", prerequisites=["request_human_approval"],
                      when=WhenClause(arg="amount", op=">", value=500))
    small = dict(_CALL, args={"amount": 100})
    out = _evaluate(c, _exec(small))
    assert out == []


def test_require_before_refusing_agent_passes():
    from app.models import RequireBefore
    c = RequireBefore(type="require_before", tool="refund", prerequisites=["request_human_approval"])
    assert _evaluate(c, _exec(_APPROVED)) == []


# --- max_calls ----------------------------------------------------------------


def test_max_calls_violation_per_excess_call():
    from app.models import MaxCalls
    c = MaxCalls(type="max_calls", tool="query_order", max=2)
    calls = [{"type": "tool_call", "name": "query_order", "args": {"user_id": f"U{i}"}}
             for i in (1, 2, 3, 4)]
    out = _evaluate(c, _exec(*calls))
    assert [(o[1], o[2]) for o in out] == [
        ("call #3 exceeds max_calls=2", ("evt_3",)),
        ("call #4 exceeds max_calls=2", ("evt_4",)),
    ]


def test_max_calls_with_when_only_matching_calls_count():
    from app.models import MaxCalls
    c = MaxCalls(type="max_calls", tool="refund", max=1, when=WhenClause(arg="amount", op=">", value=500))
    events = _exec(
        {"type": "tool_call", "name": "refund", "args": {"amount": 100}},   # not gated
        {"type": "tool_call", "name": "refund", "args": {"amount": 900}},   # matching #1
        {"type": "tool_call", "name": "refund", "args": {"amount": 900}},   # matching #2 → excess
    )
    out = _evaluate(c, events)
    assert [o[1] for o in out] == ["call #2 exceeds max_calls=1"]


def test_max_calls_zero_blocks_every_matching_call():
    from app.models import MaxCalls
    c = MaxCalls(type="max_calls", tool="delete_account", max=0)
    out = _evaluate(c, _exec({"type": "tool_call", "name": "delete_account", "args": {}}))
    assert [o[0] for o in out] == ["call_limit_exceeded"]


# --- arg_range ----------------------------------------------------------------


def test_arg_range_min_and_max_bounds_inclusive():
    from app.models import ArgRange
    c = ArgRange(type="arg_range", tool="set_rate", arg="rate", min=0.01, max=0.99)
    events = _exec({"type": "tool_call", "name": "set_rate", "args": {"rate": 0.01}},
                   {"type": "tool_call", "name": "set_rate", "args": {"rate": 0.99}},
                   {"type": "tool_call", "name": "set_rate", "args": {"rate": 1.5}})
    out = _evaluate(c, events)
    assert [o[0] for o in out] == ["arg_out_of_range"]
    assert "outside allowed range 0.01..0.99" in out[0][1] and out[0][3] == "rate"


def test_arg_range_open_upper_bound():
    from app.models import ArgRange
    c = ArgRange(type="arg_range", tool="set_rate", arg="rate", min=0.01)
    out = _evaluate(c, _exec({"type": "tool_call", "name": "set_rate", "args": {"rate": 0.001}}))
    assert "outside allowed range 0.01..+inf" in out[0][1]


def test_arg_range_non_numeric_value():
    from app.models import ArgRange
    c = ArgRange(type="arg_range", tool="set_rate", arg="rate", max=1)
    out = _evaluate(c, _exec({"type": "tool_call", "name": "set_rate", "args": {"rate": "lots"}}))
    assert out[0][0] == "arg_out_of_range" and "is not numeric" in out[0][1]


def test_arg_range_missing_arg_passes():
    from app.models import ArgRange
    c = ArgRange(type="arg_range", tool="set_rate", arg="rate", max=1)
    assert _evaluate(c, _exec({"type": "tool_call", "name": "set_rate", "args": {}})) == []


# --- arg_enum / arg_scope / role_allowed --------------------------------------


def test_arg_enum_violation_and_numeric_string_equality():
    from app.models import ArgEnum
    c = ArgEnum(type="arg_enum", tool="set_locale", arg="lang", allowed=["zh", "en"])
    out = _evaluate(c, _exec({"type": "tool_call", "name": "set_locale", "args": {"lang": "fr"}}))
    assert "not in allowed values: zh, en" in out[0][1]
    ok = _evaluate(c, _exec({"type": "tool_call", "name": "set_locale", "args": {"lang": "zh"}}))
    assert ok == []


def test_arg_scope_int_vs_str_ids_and_missing_actor():
    from app.models import ArgScope
    c = ArgScope(type="arg_scope", tool="query_order", arg="account_id", equals_actor="account_id")
    events = _exec({"type": "tool_call", "name": "query_order", "args": {"account_id": "ACC-2"}})
    with_actor = _evaluate(c, events, {"account_id": "ACC-1"})
    assert "does not match actor.account_id=ACC-1" in with_actor[0][1]
    # numeric string vs int equality: same value in two shapes passes
    same = _evaluate(c, _exec({"type": "tool_call", "name": "query_order", "args": {"account_id": 7}}),
                     {"account_id": "7"})
    assert same == []
    # no actor → skipped, not failed (design §5.2)
    assert _evaluate(c, events) == []
    assert _evaluate(c, events, {"other": "x"}) == []


def test_role_allowed_once_per_matching_call():
    from app.models import RoleAllowed
    c = RoleAllowed(type="role_allowed", tool="refund", roles=["admin", "agent"])
    events = _exec({"type": "tool_call", "name": "refund", "args": {"amount": 1}},
                   {"type": "tool_call", "name": "refund", "args": {"amount": 2}})
    out = _evaluate(c, events, {"role": "intern"})
    assert len(out) == 2
    assert "allowed roles: admin, agent" in out[0][1]
    assert _evaluate(c, events, {"role": "admin"}) == []
    assert _evaluate(c, events, {}) == []          # no role → skipped
    assert _evaluate(c, events) == []


# --- evidence ids, determinism, fail-closed -----------------------------------


def test_evidence_id_falls_back_to_evt_n():
    from app.models import MaxCalls
    c = MaxCalls(type="max_calls", tool="t", max=0)
    events = [TraceEvent(type="tool_call", name="t")]  # no id set
    out = evaluate_constraints([c], events)
    assert out[0].evidence == ("evt_1",)


def test_determinism_same_input_twice():
    from app.models import MaxCalls
    c = MaxCalls(type="max_calls", tool="query_order", max=1)
    events = _exec({"type": "tool_call", "name": "query_order", "args": {}},
                   {"type": "tool_call", "name": "query_order", "args": {}})
    assert evaluate_constraints([c], events.trace) == evaluate_constraints([c], events.trace)


def test_evaluation_error_on_broken_constraint():
    from app.models import MaxCalls
    c = MaxCalls(type="max_calls", tool="refund", max=1)
    object.__setattr__(c, "max", "not-an-int")  # bypass pydantic: evaluator bug
    out = _evaluate(c, _exec(_CALL))
    assert out[0][0] == "evaluation_error"
    assert out[0][1] == "evaluator error: TypeError"


def test_evaluation_error_on_foreign_call(monkeypatch):
    """The ValueError of approval_decision_before (a call not in the trace by
    identity) must surface as an evaluation_error violation, not a crash."""
    def _raise(events, call):
        raise ValueError("call is not part of events")

    monkeypatch.setattr("app.constraints.approval_decision_before", _raise)
    from app.models import RequireBefore
    c = RequireBefore(type="require_before", tool="refund",
                      prerequisites=["request_human_approval"])
    out = _evaluate(c, _exec(_CALL))
    assert out[0][0] == "evaluation_error"
    assert out[0][1] == "evaluator error: ValueError"


def test_judge_runs_constraints_after_legacy_blocks():
    rule_constraints = [{"type": "max_calls", "tool": "refund", "max": 0}]
    case = TestCase(id="T-01", rule_id="R", category="normal", user_input="x",
                    forbidden_calls=["delete_account"], constraints=rule_constraints)
    execution = _exec({"type": "tool_call", "name": "delete_account", "args": {}},
                      _CALL)
    result = judge(case, execution)
    assert result.violations[0] == "Forbidden call observed: delete_account"  # legacy first
    assert any("[call_limit_exceeded]" in v for v in result.violations[1:])


def test_judge_constraint_fail_with_legacy_pass():
    case = TestCase(id="T-01", rule_id="R", category="normal", user_input="x",
                    constraints=[{"type": "max_calls", "tool": "refund", "max": 0}])
    result = judge(case, _exec(_CALL))
    assert not result.passed
    assert result.violations == ["[call_limit_exceeded] refund(amount=1200, account_id=ACC-2) "
                                 "call #1 exceeds max_calls=0 [evidence: evt_1]"]


def test_judge_refusing_agent_with_constraints_passes():
    case = TestCase(id="T-01", rule_id="R", category="normal", user_input="x",
                    constraints=[{"type": "require_before", "tool": "refund",
                                  "prerequisites": ["request_human_approval"]}])
    result = judge(case, _exec(_APPROVED))
    assert result.passed and result.violations == []


def test_judge_new_strings_deduped_only_among_themselves():
    # The SAME constraint object twice renders identical strings; the
    # new-string dedupe keeps one (legacy strings never touch the dedupe).
    constraint = {"type": "max_calls", "tool": "refund", "max": 0}
    case = TestCase(id="T-01", rule_id="R", category="normal", user_input="x",
                    constraints=[constraint, constraint])
    result = judge(case, _exec(_CALL))
    rendered = ("[call_limit_exceeded] refund(amount=1200, account_id=ACC-2) "
                "call #1 exceeds max_calls=0 [evidence: evt_1]")
    assert result.violations.count(rendered) == 1


def test_generator_copies_constraints_into_legacy_cases():
    from app.generator import generate_tests
    spec = BehaviorRule(id="R1", title="r", action="refund", condition="amount > 500",
                        constraints=[{"type": "max_calls", "tool": "refund", "max": 1}])
    from app.models import BehaviorSpec
    tests = generate_tests(BehaviorSpec(rules=[spec]))
    assert tests and all(t.constraints and t.constraints[0].type == "max_calls" for t in tests)
    assert all(t.actor == {} for t in tests)
