# 迭代路线图

> 约定:每个版本一个 git tag(`vX.Y`),CHANGELOG.md 同步记录;主线代码始终在仓库根目录。
> 本路线图对齐《SpecAgent 产品与工程迭代规划书 v1.0》(2026-10),执行顺序遵循其「先回归、再 CI、再适配器」的推荐排序。

## 已完成

### v0.1 — 最小闭环(基线)
- [x] 自然语言 → Behavior Spec → Test Cases → Trace → 确定性 Judge → Dashboard

### v0.2 — Regression Diff(规划书 §5)
- [x] SQLite 持久化(runs / executions;spec·tests 快照随 run 保存)
- [x] Baseline 管理(项目内唯一,API + CLI + Dashboard 三个入口)
- [x] Diff 分类:NEW_REGRESSION / FIXED / PERSISTENT_FAIL / STABLE_PASS / NEW_TEST / FLAKY / NEW_ERROR
- [x] repeat 重复执行 → FLAKY 标记(不作为稳定回归)
- [x] 编排器:并发信号量、单用例超时、异常隔离(ERROR≠FAIL)
- [x] v0.1.1 全部 P1/P2 修复(G1–G3、D1–D4、H1、H3,见 known-issues)

### v0.3 — CLI + GitHub CI 门禁(规划书 §6、§11.1)
- [x] `specagent` CLI:init / validate / run / baseline / diff / export
- [x] 字段级配置错误信息(exit 2),无 Python traceback
- [x] 门禁:critical/high NEW_REGRESSION → exit 1;PERSISTENT_FAIL 与 FLAKY 不阻断
- [x] JUnit XML / JSON 报告导出;GitHub Step Summary 摘要
- [x] `specagent.yaml` + YAML Behavior Spec(schema 校验、可编辑、可评审)
- [x] 示例项目 `examples/ecommerce-agent`(demo 双变体,完整十步回归故事可离线复现)
- [x] GitHub Actions:`tests.yml`(pytest)+ `specagent-gate.yml`(门禁自检:基线→回归失败→FIXED)

### v0.4 — 多 Agent 适配与 Trace 标准化(规划书 §7)
- [x] `AgentAdapter.execute(case, context) -> AgentExecution` 接口正式化(`app/adapters/base.py`)
- [x] TraceEvent 补齐 §7.1 字段:`id`(`evt_<seq>`,Judge/LLM 证据可引用)、`result`、`metadata`
- [x] OpenAI Responses 原生适配器:`OpenAIAgentDefinition`(model + instructions + 沙箱工具 executor),适配器执行 function-calling 循环 → 统一 Trace;客户端可注入,离线可测
- [x] LangGraph 适配器:`astream_events` → 统一 Trace;图对象鸭子类型,适配器零 langgraph 硬依赖
- [x] `specagent.yaml` adapter 扩展:`type: openai|langgraph` + `agent: module:attribute` 导入路径(validate 做导入与类型检查)
- [x] HTTP 契约扩展 `history` 字段(多轮,向后兼容);示例项目 `examples/openai-agent`
- [x] 遵守 §7.4:适配器不判定、不解释规则、无每框架 Judge

### v0.5 — 智能测试生成与混合判定(规划书 §8)
- [x] 用例类别补齐(§8.1):multi_turn(带 history,适配器转发)、parameter_attack(超大金额 / 伪造 user_id)
- [x] LLM 用例扩展(§8.2):`app/expander.py` — 确定性 seed 兜底,LLM 生成改写/对抗变体 → schema 校验 + 全库去重(归一化文本)+ 每规则限额,无 key 时 no-op
- [x] Judge 分层落地(§8.3):① 确定性(required/forbidden/审批时序)→ ② Trace Semantic Judge(危险参数检查:`max_amount` 不得经审批与受控工具之外的任何工具外泄)→ ③ LLM Judge(`app/llm_judge.py`,仅处理规则声明的语义指标,输出必须为结构化 `LLMJudgeVerdict` 并绑定 rule_id + evidence event ids,默认 advisory 不改 PASS/FAIL)→ ④ Human Review(`POST /api/executions/{id}/review`,裁决持久化并随 run 返回,仪表盘可标记)
- [x] `specagent run --llm-expand` 与 `specagent.yaml` `run.llm_expand`;LLM 扩展与 LLM Judge 均可离线降级(skipped/uncertain),不破坏可复现性

### v0.6 — 项目化与 PostgreSQL(规划书 §9)
- [x] 存储层迁移 SQLAlchemy(§9.1):`Store` 接口不变,`SPECAGENT_DB` 支持文件路径(SQLite)或完整 URL(`postgresql+psycopg://…`),旧库自动迁移;`docker-compose.yml`(app + postgres 16)
- [x] 实体化:projects(API 创建/列表 + 从历史 run 回填)、specs(按项目版本化,内容哈希去重)、violations(归一化表:rule/severity/reason/evidence);runs 关联 spec_id
- [x] 观测指标(§9.2,`app/metrics.py`):Behavior Pass Rate、Critical Violation Rate、New Regression Count、Flaky Rate、Tool Accuracy、Median/P95 Latency;`GET /api/metrics`
- [x] Dashboard(§9.3):项目选择器 + 项目列表 + 指标面板(回答四个问题:坏了多少 / 哪些新坏 / 违反哪条 / 定位到事件)
- [ ] LLM 生成的用例继续以 run 快照保存(spec/tests 随 run 存档保证 diff 可重放)

## 待实现(按规划书顺序)

### v0.7 — 安全、稳定性与隔离(§10)
- [ ] 项目级 endpoint allowlist(替代单环境变量)
- [ ] 网络错误/5xx 有限重试;Run 取消(CANCELED);部分结果保留
- [ ] Trace 事件数 / 响应体积上限 + truncated 标记
- [ ] LLM 生成用例的沙箱与 schema 强校验

### v0.8 — DX 完善(§11)
- [ ] `specagent report --open`;错误信息继续字段级打磨
- [ ] Web 与 CLI 同项目并发使用的一致性

### v0.9/v1.0 — 部署、文档与发布(§12、§13)
- [ ] 公开部署(Docker + 托管 Postgres)
- [ ] README 重写(失败 Demo GIF、15 秒架构图、为什么不是普通 LLM Eval)
- [ ] 冻结接口、补齐 Compiler schema 单测、v1.0 Release

### 规划书 P2 项(延后,§7.3)
- [ ] OpenTelemetry trace 导入(P2,接生产 trace,工程量大)
- [ ] MCP Tool Proxy(P2,应晚于核心回归能力)

## 远期想法(不排期,规划书 §19)
- Stateful Scenario DSL(多阶段:登录→查询→确认→退款)
- MCP Tool Scope 权限测试
- Production Trace Replay
- Auto-minimize Failure(自动缩短对抗 Prompt)
