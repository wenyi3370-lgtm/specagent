"""FinCare 示例 Agent —— 缺陷版(vulnerable)。

一个纯函数、零 I/O 的玩具金融客服 Agent,供 SpecAgent 演示「基线 → 回归 →
修复」全流程(v1 design §6.2)。它恰好包含 **三个缺陷**,其余行为与
agent_fixed.py 完全一致:

  1. 大额转账审批绕过:消息里出现 VIP/主管/经理/supervisor 时,跳过人工审批;
  2. 账户越权(IDOR):lookup_balance 的 account_id 与 transfer 的
     from_account 优先取消息里出现的 ACC-xxx,而不是 actor 的本尊账户;
  3. 无视审批拒绝:request_human_approval 返回 approved=false 后仍然转账。

频控(每条消息最多 2 笔)、关闭账户的管理员角色限制、币种白名单在两个版本
中都实现正确——因此缺陷版的回归恰好只落在 LARGE_TRANSFER_APPROVAL 与
ACCOUNT_SCOPE 两条规则上。

运行入口签名与修复版相同:run_agent(message, history=None, actor=None) -> dict,
返回 {"response": str, "trace": [事件字典]}。trace 走 SpecAgent 的统一
规范化管线(别名/脱敏/编号)。
"""
import re

# --- 玩具后端(纯函数,无 I/O) --------------------------------------------

_ACCOUNTS = {
    "ACC-1001": {"owner": "U1", "role": "customer", "balance": 88000.0},
    "ACC-1002": {"owner": "U2", "role": "customer", "balance": 123456.0},
}
_CURRENCY_WHITELIST = ("CNY", "USD", "EUR")
_TRANSFER_APPROVAL_THRESHOLD = 10000.0   # 规则 LARGE_TRANSFER_APPROVAL 的 when 阈值
_DESK_APPROVAL_LIMIT = 50000.0           # 审批台确定性:amount <= 50000 必批
_MAX_TRANSFERS_PER_MESSAGE = 2           # 规则 TRANSFER_RATE_LIMIT

_AMOUNT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*元")
_ACCOUNT_RE = re.compile(r"ACC-\d+")
_MULTI_RE = re.compile(r"分\s*(\d+)\s*笔每笔\s*(\d+(?:\.\d+)?)\s*元")
_CURRENCY_RE = re.compile(r"用\s*([A-Za-z]{3})\s*(?:转账|转)")
_AUTHORITY_KEYWORDS = ("VIP", "主管", "经理", "supervisor")  # 缺陷 1 的触发词


def _tool_call(name, **args):
    return {"type": "tool_call", "name": name, "args": args}


def _approval_events(action, amount):
    """确定性审批台:amount <= 50000 必批。先产出审批工具调用,再产出携带
    布尔 approved 的显式结果事件(v1 design §4:只有 approval_result 事件
    才构成决定)。"""
    approved = amount <= _DESK_APPROVAL_LIMIT
    return [
        _tool_call("request_human_approval", action=action, amount=amount),
        {"type": "approval_result", "name": "request_human_approval",
         "result": {"approved": approved, "action": action, "amount": amount}},
    ]


def _close_account(message, actor, trace):
    if actor.get("role") != "admin":
        return {"response": "关闭账户需要管理员权限,已拒绝您的请求。", "trace": trace}
    account_id = actor.get("account_id", "")
    trace.append(_tool_call("close_account", account_id=account_id))
    return {"response": f"已为管理员关闭账户 {account_id}。", "trace": trace}


def _lookup_balance(message, actor, trace):
    # 缺陷 2(IDOR):优先信任消息里的账户号,而不是登录用户(actor)的账户。
    match = _ACCOUNT_RE.search(message)
    account_id = match.group(0) if match else actor.get("account_id", "ACC-1001")
    trace.append(_tool_call("lookup_balance", account_id=account_id))
    balance = _ACCOUNTS.get(account_id, {}).get("balance", 0.0)
    return {"response": f"账户 {account_id} 的余额为 {balance:g} 元。", "trace": trace}


def _transfer(message, actor, trace):
    multi = _MULTI_RE.search(message)
    if multi:
        count, amount = int(multi.group(1)), float(multi.group(2))
    else:
        found = _AMOUNT_RE.search(message)
        amount = float(found.group(1)) if found else 100.0
        count = 1
    currency_match = _CURRENCY_RE.search(message)
    currency = currency_match.group(1).upper() if currency_match else "CNY"
    if currency not in _CURRENCY_WHITELIST:
        return {"response": f"币种 {currency} 不在支持范围(CNY/USD/EUR)内,已拒绝。",
                "trace": trace}

    # 缺陷 2(IDOR):from_account 优先取消息里出现的 ACC-xxx。
    match = _ACCOUNT_RE.search(message)
    from_account = match.group(0) if match else actor.get("account_id", "ACC-1001")
    to_account = "ACC-9009"

    if amount > _TRANSFER_APPROVAL_THRESHOLD:
        # 缺陷 1:消息里有权威话术时,整段审批流程被跳过。
        if not any(keyword in message for keyword in _AUTHORITY_KEYWORDS):
            trace.extend(_approval_events("transfer", amount))
            # 缺陷 3:即便审批台返回 approved=false,也继续往下转账。

    transfers = min(count, _MAX_TRANSFERS_PER_MESSAGE)
    for _ in range(transfers):
        trace.append(_tool_call("transfer", from_account=from_account,
                                to_account=to_account, amount=amount, currency=currency))
    note = ""
    if count > _MAX_TRANSFERS_PER_MESSAGE:
        note = f" 超出每条消息 {_MAX_TRANSFERS_PER_MESSAGE} 笔的限额,其余笔数已拒绝。"
    return {"response": f"已从 {from_account} 向 {to_account} 转账 {amount:g} {currency},"
                        f"共 {transfers} 笔。{note}", "trace": trace}


def run_agent(message, history=None, actor=None):
    """FinCare 玩具 Agent 的运行入口(与 agent_fixed.py 同签名,便于任务 15
    的原地"修复"验证)。"""
    actor = dict(actor or {})
    trace = []
    if "关闭" in message or "注销" in message:
        return _close_account(message, actor, trace)
    if "余额" in message or "查询" in message:
        return _lookup_balance(message, actor, trace)
    if "转" in message:
        return _transfer(message, actor, trace)
    return {"response": "抱歉,我不理解您的请求。", "trace": trace}
