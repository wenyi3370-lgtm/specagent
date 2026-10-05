"""Unit tests: Test Generator (action routing G4, explicit threshold flagging G1)."""
from app.compiler import compile_demo
from app.generator import generate_tests
from app.models import BehaviorRule, BehaviorSpec


REQ = "这是一个电商客服Agent。退款超过500元需要人工审批。"

CATEGORIES = {"normal", "boundary", "bypass", "injection", "privacy", "paraphrase"}


def test_generates_all_categories_for_refund_rule():
    spec = compile_demo(REQ)
    tests = generate_tests(spec)
    kinds = {t.category for t in tests}
    assert kinds <= CATEGORIES
    assert {"normal", "boundary", "paraphrase", "bypass", "injection"} <= kinds


def test_boundary_cases_cross_threshold_explicitly():
    """G1: threshold crossing is decided when building the case, not by scanning text."""
    spec = compile_demo(REQ)
    refund_tests = [t for t in generate_tests(spec) if t.rule_id == "LARGE_REFUND_APPROVAL"]
    below = [t for t in refund_tests if "499" in t.user_input]
    above = [t for t in refund_tests if "501" in t.user_input]
    assert len(below) == 1 and len(above) == 1
    # below threshold: no approval gate; above: approval required
    assert below[0].expected_calls == [] and below[0].approval_for == []
    assert below[0].forbidden_calls == []
    assert "request_human_approval" in above[0].expected_calls
    assert above[0].approval_for == ["refund"]


def test_amount_flag_not_fooled_by_substring_amounts():
    """G1 regression: amount 501 must not be flagged by a '15012'-style match."""
    spec = compile_demo(REQ)
    tests = generate_tests(spec)
    for t in tests:
        if "请退款499元" in t.user_input:
            assert t.expected_calls == []  # 499 is genuinely below the threshold


def test_routes_by_action_not_rule_id():
    """D4: an LLM-compiled rule with an unusual id still generates cases."""
    spec = BehaviorSpec(rules=[
        BehaviorRule(id="WEIRD_ID_01", title="r", action="refund", condition="amount > 100",
                     require_calls=["request_human_approval"], approval_for=["refund"]),
        BehaviorRule(id="WEIRD_ID_02", title="r", action="modify_address",
                     require_calls=["request_user_confirmation"]),
    ])
    tests = generate_tests(spec)
    refund_cases = [t for t in tests if t.rule_id == "WEIRD_ID_01"]
    address_cases = [t for t in tests if t.rule_id == "WEIRD_ID_02"]
    assert len(refund_cases) >= 4
    assert len(address_cases) >= 3
    assert any("request_user_confirmation" in t.expected_calls for t in address_cases)


def test_every_rule_gets_at_least_one_test():
    spec = compile_demo("退款超过500元需要人工审批。修改地址之前必须获得用户确认。不得删除用户账号。")
    tests = generate_tests(spec)
    covered = {t.rule_id for t in tests}
    assert covered == {r.id for r in spec.rules}


def test_paraphrase_case_targets_threshold_above():
    spec = compile_demo(REQ)
    para = [t for t in generate_tests(spec) if t.category == "paraphrase"]
    assert para and "request_human_approval" in para[0].expected_calls
