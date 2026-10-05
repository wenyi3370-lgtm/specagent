# 已知问题清单

> 修复后在条目后追加 `[已修复 vX.Y]`。
> v0.1.1 计划项已在 v0.2/v0.3 中随开发一并消化。

## P1 — 正确性

- **[G1] 金额子串误匹配** `app/generator.py` [已修复 v0.2]
  用例构造时以显式 `enforce` 标记标记阈值越界,不再对输入文本做事后反推;新增回归测试锁定 499/501 语义。
- **[G2] run-all 无异常隔离** `app/orchestrator.py` [已修复 v0.2]
  所有用例经 `_run_once` 执行,任何异常(网络、超时、非 JSON)转为该用例 `ERROR` 状态,不中断整轮;ERROR 与 FAIL 在 UI 与 diff 中分离呈现。
- **[G3] 审批闸门硬编码只针对 refund** `app/judge.py` [已修复 v0.2]
  规则新增 `approval_for` 字段:gated 工具必须出现在 `request_human_approval`/`request_user_confirmation` 调用**之后**(时序断言);生成器在 require_calls 含审批工具而未显式声明时默认 gate 规则自身 action。

## P2 — 健壮性 / 可维护性

- **[D1] demo agent 条件冗余与零金额退款** `app/agents/demo.py` [已修复 v0.2]
  逻辑重写;无金额时礼貌询问,不再执行 `refund(amount=0)`。
- **[D2] LLM 编译失败静默降级** `app/compiler.py` [已修复 v0.2]
  降级时输出 warning 日志,`spec.compiler` 标注 `+llm-fallback`。
- **[D3] 全项目零日志** [已修复 v0.2]
  `logging.basicConfig` + 命名 logger(storage / orchestrator / api / compiler / adapter)。
- **[D4] generator 与 rule.id 硬编码耦合** `app/generator.py` [已修复 v0.2]
  按 `rule.action` 特征(refund/address/delete/query/默认)路由;测试覆盖非常规 id 的 LLM 规则。

## P3 — 打磨

- **[H1] 外接 Agent trace 类型未做宽松兼容** `app/trace.py` [已修复 v0.2]
  别名映射(`tool`→`tool_call` 等)、未知类型丢弃 + 告警、非 dict 事件跳过。
- **[H2] 仪表盘无用例分类/运行历史** [已修复 v0.3]
  新增 Run 历史、Set as Baseline、Diff 视图(NEW/FIXED/PERSISTENT/FLAKY 计数,critical 置顶,双栏 trace 对比)。
- **[H3] run_all 串行执行** `app/orchestrator.py` [已修复 v0.2]
  `asyncio.Semaphore` 并发(默认 4)+ 单用例超时。
- **[H4] zip 发布包含 `__pycache__/*.pyc`** [已修复 v0.1 迁入时清理]

## 当前未决(按 roadmap 排期)

- **[U1] ERROR 用例的 diff 语义**:candidate ERROR + baseline PASS 归为 `NEW_ERROR`,默认不进门禁;是否允许配置纳入门禁待定(v0.7 稳定性再议)。
- **[U2] 原生框架 Adapter**(OpenAI Agents SDK / LangGraph):待 v0.4,当前仅 HTTP + demo。
- **[U3] LLM 生成对抗用例与 LLM-as-judge 分层**:待 v0.5;当前测试生成全部确定性。
- **[U4] PostgreSQL / projects·specs 实体化**:待 v0.6;当前 spec/tests 以快照形式随 run 存储,`Store` 接口不变。
- **[U5] SSRF allowlist 管理**:endpoint 目前仅接受后端环境变量(前端不可注入),项目级 allowlist 待 v0.7。
