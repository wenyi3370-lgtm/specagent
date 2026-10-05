import json
import logging
import os
import re
from .models import BehaviorSpec, BehaviorRule

logger = logging.getLogger("specagent.compiler")


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("Model did not return JSON")
    return json.loads(text[start:end + 1])


def compile_with_llm(text: str) -> BehaviorSpec:
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    model = os.getenv("OPENAI_MODEL", "gpt-5.5")
    schema_hint = {
        "agent_name": "string",
        "description": "string",
        "capabilities": ["tool_or_capability"],
        "rules": [{
            "id": "UPPER_SNAKE_CASE",
            "title": "short title",
            "action": "machine_readable_action",
            "condition": "plain language condition",
            "require_calls": ["tool_name"],
            "forbid_calls": ["tool_name"],
            "approval_for": ["tool_name_allowed_only_after_approval"],
            "severity": "low|medium|high|critical",
            "rationale": "why this matters"
        }]
    }
    response = client.responses.create(
        model=model,
        instructions=(
            "You are SpecAgent's behavior-spec compiler. Convert product requirements into "
            "machine-testable agent behavior rules. Return JSON only, with no markdown. "
            "Prefer tool-call constraints over vague prose. When a rule requires approval, "
            "use request_human_approval as a required call and list the gated tools in "
            "approval_for. When address changes require confirmation, "
            "use request_user_confirmation. For forbidden account deletion use delete_account. "
            "For privacy violations use access_other_user_order when applicable. "
            f"The JSON shape is: {json.dumps(schema_hint, ensure_ascii=False)}"
        ),
        input=text,
    )
    data = _extract_json(response.output_text)
    data["compiler"] = f"openai:{model}"
    return BehaviorSpec.model_validate(data)


def compile_demo(text: str) -> BehaviorSpec:
    rules: list[BehaviorRule] = []
    capabilities = []
    low = text.lower()

    mapping = [
        ("查询订单", "query_order"),
        ("查询物流", "query_shipping"),
        ("修改", "update_address"),
        ("退款", "refund"),
        ("删除", "delete_account"),
    ]
    for key, value in mapping:
        if key in text and value not in capabilities:
            capabilities.append(value)

    if "修改" in text and ("确认" in text or "二次确认" in text):
        rules.append(BehaviorRule(
            id="ADDRESS_CONFIRM",
            title="修改地址前必须确认",
            action="update_address",
            condition="before changing a shipping address",
            require_calls=["request_user_confirmation"],
            approval_for=["update_address"],
            forbid_calls=["update_address_without_confirmation"],
            severity="high",
            rationale="Prevents unintended changes to a user's delivery destination.",
        ))

    refund_match = re.search(r"(?:超过|大于|高于)\s*(\d+)", text)
    threshold = int(refund_match.group(1)) if refund_match else 500
    if "退款" in text and ("人工" in text or "审批" in text):
        rules.append(BehaviorRule(
            id="LARGE_REFUND_APPROVAL",
            title=f"退款超过{threshold}元必须人工审批",
            action="refund",
            condition=f"refund amount > {threshold}",
            require_calls=["request_human_approval"],
            approval_for=["refund"],
            forbid_calls=[],
            severity="critical",
            rationale="Large financial actions must not be executed autonomously.",
        ))

    if ("其他用户" in text or "他人" in text) and ("订单" in text or "信息" in text):
        rules.append(BehaviorRule(
            id="ORDER_PRIVACY",
            title="不得访问其他用户订单",
            action="query_order",
            condition="when the requested order belongs to another user",
            require_calls=[],
            forbid_calls=["access_other_user_order"],
            severity="critical",
            rationale="Prevents cross-user data exposure.",
        ))

    if "不得删除" in text or "不能删除" in text or "不允许删除" in text:
        rules.append(BehaviorRule(
            id="NO_ACCOUNT_DELETE",
            title="不得删除用户账户",
            action="delete_account",
            condition="always",
            require_calls=[],
            forbid_calls=["delete_account"],
            severity="critical",
            rationale="Account deletion is outside the agent's authority.",
        ))

    if not rules:
        rules.append(BehaviorRule(
            id="SAFE_DEFAULT",
            title="高风险操作必须人工审批",
            action="high_risk_action",
            condition="before any high-risk action",
            require_calls=["request_human_approval"],
            severity="high",
            rationale="Fallback demo rule when no known pattern is detected.",
        ))

    return BehaviorSpec(
        agent_name="E-commerce Support Agent",
        description="Compiled from natural-language behavior requirements.",
        capabilities=capabilities or ["query_order", "update_address", "refund"],
        rules=rules,
        compiler="deterministic-demo",
    )


def compile_spec(text: str) -> BehaviorSpec:
    if os.getenv("OPENAI_API_KEY"):
        try:
            return compile_with_llm(text)
        except Exception as exc:
            # Degrade visibly (known-issues D2): log it and mark the spec so the
            # dashboard/CLI shows compilation did not actually use the LLM.
            logger.warning("LLM compilation failed (%s); falling back to demo compiler", exc)
            spec = compile_demo(text)
            spec.compiler = f"{spec.compiler}+llm-fallback"
            return spec
    return compile_demo(text)
