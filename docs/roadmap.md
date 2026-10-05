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

## 待实现(按规划书顺序)

### v0.4 — 多 Agent 适配与 Trace 标准化(§7)
- [ ] OpenAI Agents SDK 原生 Adapter(P1)
- [ ] LangGraph Adapter(P1)
- [ ] Adapter 接口正式化(`AgentAdapter.execute(test_case, context) -> AgentExecution`)
- [ ] OpenTelemetry trace 导入(P2,可后置)

### v0.5 — 智能测试生成与混合判定(§8)
- [ ] LLM 扩展改写 / 对抗变体(确定性 seed cases 兜底)
- [ ] 用例 schema 校验 + 去重 + risk tagging
- [ ] Trace Semantic Judge(结构化事件上的高层语义)
- [ ] LLM Judge(仅礼貌性/解释充分性类指标,输出必须结构化并绑定 evidence)
- [ ] Human Review 裁决记录

### v0.6 — 项目化与 PostgreSQL(§9)
- [ ] `Store` 换 PostgreSQL 实现(接口不变)
- [ ] projects / specs / rules / test_cases 实体化(替代 run 快照)
- [ ] 观测指标:Behavior Pass Rate、Critical Violation Rate、Flaky Rate、P95 Latency
- [ ] Dashboard:Projects 页 + Spec Editor

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

## 远期想法(不排期,规划书 §19)
- Stateful Scenario DSL(多阶段:登录→查询→确认→退款)
- MCP Tool Scope 权限测试
- Production Trace Replay
- Auto-minimize Failure(自动缩短对抗 Prompt)
