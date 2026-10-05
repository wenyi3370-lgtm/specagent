"""Behavior Spec YAML loader (roadmap §14.1, v1.0 DoD: "YAML 可编辑 + schema validation").

Example:
    agent: E-commerce Support Agent
    rules:
      - id: LARGE_REFUND_APPROVAL
        title: 退款超过500元必须人工审批
        severity: critical
        action: refund
        condition: refund amount > 500
        require_calls: [request_human_approval]
        approval_for: [refund]
"""
import yaml
from pydantic import ValidationError

from .errors import SpecValidationError
from .models import BehaviorRule, BehaviorSpec


def _format_validation_error(exc: ValidationError, where: str) -> list[str]:
    out = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "<root>"
        out.append(f"{where}.{loc}: {err['msg']} (got {err['input']!r})")
    return out


def parse_spec(data: dict, where: str = "spec") -> BehaviorSpec:
    if not isinstance(data, dict):
        raise SpecValidationError([f"{where}: expected a mapping, got {type(data).__name__}"])
    payload = {
        "agent_name": data.get("agent") or data.get("agent_name") or "Agent under test",
        "description": data.get("description", ""),
        "capabilities": data.get("capabilities", []),
        "compiler": "yaml",
    }
    rules = data.get("rules", [])
    if not isinstance(rules, list) or not rules:
        raise SpecValidationError([f"{where}.rules: at least one rule is required"])
    parsed_rules = []
    for i, raw in enumerate(rules):
        if not isinstance(raw, dict):
            raise SpecValidationError([f"{where}.rules[{i}]: expected a mapping"])
        if not raw.get("id"):
            raise SpecValidationError([f"{where}.rules[{i}].id: rule id is required"])
        rule_payload = {
            "id": raw["id"],
            "title": raw.get("title") or raw.get("description") or raw["id"],
            "action": raw.get("action", ""),
            "condition": raw.get("condition", "always"),
            "require_calls": raw.get("require_calls", []),
            "forbid_calls": raw.get("forbid_calls", []),
            "approval_for": raw.get("approval_for", []),
            "llm_checks": raw.get("llm_checks", []),
            "severity": raw.get("severity", "high"),
            "rationale": raw.get("rationale", ""),
        }
        try:
            parsed_rules.append(BehaviorRule.model_validate(rule_payload))
        except ValidationError as exc:
            raise SpecValidationError(_format_validation_error(exc, f"{where}.rules[{i}]")) from exc
    ids = [r.id for r in parsed_rules]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise SpecValidationError([f"{where}.rules: duplicate rule ids: {', '.join(duplicates)}"])
    payload["rules"] = parsed_rules
    try:
        return BehaviorSpec.model_validate(payload)
    except ValidationError as exc:
        raise SpecValidationError(_format_validation_error(exc, where)) from exc


def load_spec_file(path: str) -> BehaviorSpec:
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except FileNotFoundError:
        raise SpecValidationError([f"spec file not found: {path}"]) from None
    except yaml.YAMLError as exc:
        raise SpecValidationError([f"{path}: invalid YAML: {exc}"]) from None
    return parse_spec(data if data is not None else {}, where=path)
