# v0.1 已知问题清单

> 迭代起点:建议优先消化 P1,作为 v0.1.1 加固版;P2/P3 可并入 v0.2。
> 修复后在条目后追加 `[已修复 vX.Y @ commit]`。

## P1 — 正确性

- **[G1] 金额子串误匹配** `app/generator.py:23`
  `str(x) in text` 判断"高价值用例"是子串匹配,金额 `830` 会误匹配 `8300`、`501` 会匹配 `15012`。
  建议:用 `re.search(rf"(?<!\d){re.escape(str(x))}(?!\d)", text)` 或直接在构造用例时显式标记,不做文本反推。

- **[G2] run-all 无异常隔离** `app/main.py:47-53`
  外部 Agent 模式下,任意一条用例请求失败(httpx 抛错、返回非 JSON)会让整个 audit 500。
  建议:单用例 try/except,失败记为 `error` 状态而不是中断整轮。

- **[G3] 审批闸门硬编码只针对 refund** `app/judge.py:17`
  "要求 request_human_approval 时不得直接执行高风险动作"这条规则写死了 `refund`,换一个 action(如 delete_account + approval)就失效。
  建议:泛化为 `expected_calls 含 request_human_approval 时,forbid 所有非审批类 tool_call`,或把"审批后才能调用的动作"做成规则字段。

## P2 — 健壮性 / 可维护性

- **[D1] demo agent 条件冗余与零金额退款** `app/agents/demo.py:15,23`
  `if "退款" in user_input or "退" in user_input` 后一条件使前者无效;无金额时执行 `refund(amount=0)`。
  demo 是给用户看的第一印象,逻辑应干净可读。

- **[D2] LLM 编译失败静默降级** `app/compiler.py:146-148`
  key 配错、额度用尽都表现为"悄悄变成 demo 编译器",用户难以察觉。至少打一行 warning 日志,并在返回的 spec.compiler 上区分 `openai:{model}` 与 `openai:{model}-fallback`。

- **[D3] 全项目零日志** — 排查线上问题只能靠 print。建议引入 logging + 结构化输出(run 级、case 级各一条)。

- **[D4] generator 与 rule.id 硬编码耦合** `app/generator.py:13`
  `rule.id == "LARGE_REFUND_APPROVAL" or rule.action == "refund"` — LLM 编译出的规则一旦换个 id 命名就生成不出对应用例。建议按 `action`/`condition` 特征路由,id 仅作展示。

## P3 — 打磨

- **[H1] 外接 Agent 的 trace 类型未做宽松兼容** — 上游返回 `"type":"tool"` 之类时整个 run 失败,可考虑宽松解析 + 违规提示。
- **[H2] 仪表盘无用例分类筛选/折叠**,用例多后不可读(v0.2 前端项)。
- **[H3] `run_all` 串行执行**,外接 Agent 时整轮 audit 慢;可并发(asyncio.gather,注意限流)。
- **[H4] zip 发布包曾包含 `__pycache__/*.pyc`** — 已在迁入本仓库时清理;以后打包用 `git archive`。
