"""Unit tests: Spec Compiler (demo channel + YAML loader)."""
import pytest

from app.compiler import compile_demo, compile_spec
from app.errors import SpecValidationError
from app.spec_yaml import load_spec_file, parse_spec

REQ = """这是一个电商客服Agent。修改地址之前必须获得用户确认。退款超过800元需要人工审批。不得透露其他用户的订单信息。不得删除用户账号。"""


def test_demo_compiles_all_four_rules():
    spec = compile_demo(REQ)
    assert [r.id for r in spec.rules] == [
        "ADDRESS_CONFIRM", "LARGE_REFUND_APPROVAL", "ORDER_PRIVACY", "NO_ACCOUNT_DELETE",
    ]


def test_refund_threshold_is_extracted_from_text():
    spec = compile_demo(REQ)
    rule = next(r for r in spec.rules if r.id == "LARGE_REFUND_APPROVAL")
    assert rule.condition == "refund amount > 800"


def test_refund_rule_uses_approval_gate_not_unconditional_forbid():
    rule = next(r for r in compile_demo(REQ).rules if r.id == "LARGE_REFUND_APPROVAL")
    assert rule.approval_for == ["refund"]
    assert rule.forbid_calls == []
    assert rule.severity == "critical"


def test_fallback_rule_when_nothing_matches():
    spec = compile_demo("随便写一段没有匹配的描述。")
    assert spec.rules[0].id == "SAFE_DEFAULT"


def test_compile_spec_without_api_key_uses_demo_compiler():
    spec = compile_spec(REQ)
    assert spec.compiler == "deterministic-demo"


def test_yaml_spec_roundtrip(tmp_path):
    path = tmp_path / "behavior.yaml"
    path.write_text(
        """
agent: Demo Agent
rules:
  - id: LARGE_REFUND_APPROVAL
    title: 退款超过500元必须人工审批
    severity: critical
    action: refund
    condition: refund amount > 500
    require_calls: [request_human_approval]
    approval_for: [refund]
""",
        encoding="utf-8",
    )
    spec = load_spec_file(str(path))
    assert spec.agent_name == "Demo Agent"
    assert spec.rules[0].approval_for == ["refund"]
    assert spec.compiler == "yaml"


def test_yaml_spec_field_level_errors(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        """
rules:
  - id: BAD_RULE
    severity: ultra
""",
        encoding="utf-8",
    )
    with pytest.raises(SpecValidationError) as exc:
        load_spec_file(str(path))
    messages = "\n".join(exc.value.errors)
    assert "rules[0]" in messages
    assert "severity" in messages


def test_yaml_spec_duplicate_rule_ids_rejected():
    with pytest.raises(SpecValidationError) as exc:
        parse_spec({"rules": [
            {"id": "A", "action": "refund"},
            {"id": "A", "action": "refund"},
        ]})
    assert "duplicate" in exc.value.errors[0]


def test_yaml_spec_requires_at_least_one_rule():
    with pytest.raises(SpecValidationError):
        parse_spec({"rules": []})
