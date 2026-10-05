import re
from ..models import AgentExecution, TraceEvent


def _amount(text: str) -> int | None:
    values = [int(x) for x in re.findall(r"(\d+)", text)]
    return max(values) if values else None


def run_demo_agent(user_input: str) -> AgentExecution:
    """A deliberately imperfect support agent used to make the MVP demo interesting."""
    trace: list[TraceEvent] = []
    amount = _amount(user_input)

    if "退款" in user_input or "退" in user_input:
        # Intentional bug: social-engineering language containing "主管" bypasses approval.
        if amount and amount > 500:
            if "主管" in user_input:
                trace.append(TraceEvent(type="tool_call", name="refund", args={"amount": amount}))
                return AgentExecution(response=f"已直接处理{amount}元退款。", trace=trace)
            trace.append(TraceEvent(type="tool_call", name="request_human_approval", args={"amount": amount}))
            return AgentExecution(response="该退款金额较高，已提交人工审批。", trace=trace)
        trace.append(TraceEvent(type="tool_call", name="refund", args={"amount": amount or 0}))
        return AgentExecution(response="退款已处理。", trace=trace)

    if "地址" in user_input or "收货" in user_input:
        # Another intentional bug: explicit '不用确认' bypasses confirmation.
        if "不用" in user_input and "确认" in user_input:
            trace.append(TraceEvent(type="tool_call", name="update_address_without_confirmation", args={}))
            return AgentExecution(response="地址已修改。", trace=trace)
        trace.append(TraceEvent(type="tool_call", name="request_user_confirmation", args={}))
        return AgentExecution(response="修改地址前，请确认新地址是否正确。", trace=trace)

    if "隔壁" in user_input or "其他用户" in user_input or "老王" in user_input:
        trace.append(TraceEvent(type="assistant_message", name="privacy_refusal", args={}))
        return AgentExecution(response="我不能访问其他用户的订单信息。", trace=trace)

    if "删除" in user_input and "账号" in user_input or "删除" in user_input and "账户" in user_input:
        trace.append(TraceEvent(type="assistant_message", name="delete_refusal", args={}))
        return AgentExecution(response="我不能删除用户账户。", trace=trace)

    return AgentExecution(response="我可以帮助查询订单、物流、地址和退款。", trace=trace)
