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
- [x] spec/tests 快照继续随 run 存档,保证 diff 可重放

### v0.7 — 安全、稳定性与隔离(规划书 §10)
- [x] 项目级 endpoint allowlist(§10.1 SSRF):`adapter.allowed_hosts` 配置 + `SPECAGENT_ALLOWED_HOSTS` 环境变量合并,HTTP 适配器强制校验(精确/通配符匹配),`specagent validate` 报告 allowlist 状态
- [x] API Token 只存环境变量、不写日志;trace 凭证字段脱敏(v0.2 已有);LLM 用例 schema 校验(v0.5 已有)
- [x] 网络错误/5xx 有限重试(§10.2):适配器把 `httpx.TransportError`/5xx 归一化为 `TransientAgentError`,编排器只重试该类型(`run.retries`,默认 1),业务 FAIL 永不重试
- [x] Run 取消:`POST /api/runs/{id}/cancel`;run 在执行前即以 status=running 落库可被定位,未执行用例标记 CANCELED(不进 diff 回归、不算失败),已完成结果全部保留,run 终态 canceled
- [x] Trace 事件数 / 响应体积上限(§10.2):`run.max_trace_events`(默认 200)/ `run.max_response_chars`(默认 20000),超限截断并标记 `truncated`(仪表盘可见)
- [x] 项目级并发限制:同项目并发 run 共享信号量预算(`SPECAGENT_PROJECT_CONCURRENCY`,默认 8),防止并行 run 打爆目标 Agent
- [x] Timeout→ERROR 分离、异常隔离、部分结果保留、run_id 幂等(v0.2 已有)

### v0.8 — DX 完善(规划书 §11)
- [x] `specagent report [--run] [--out] [--open]`(§11.2):独立 HTML 报告(零 JS/零服务,含 Diff 双栏对比、Expected vs Actual、LLM 裁决、复核记录),可离线分享
- [x] `specagent metrics`:§9.2 六项指标终端直出;`specagent export` 缺省导出项目最近 run
- [x] `specagent init --adapter demo|http|openai`:按适配器出配置;demo 模板引导「基线 → 改坏 → 回归 → 报告」四步故事;配置缺失提示 init
- [x] Web 与 CLI 同时使用(§11.3):SQLite 启用 WAL + busy_timeout,跨进程并发读写同库
- [x] Dashboard 深链 `?project=&run=`;CLI run 输出附带 Dashboard/Report 提示
- [x] `repeat_flaky_cases` 拼写兼容(§11.1 原文键名);每个命令 --help、字段级错误(§11.3,自 v0.3 起)

### v0.8.1 — 遗留修复(v1 实现规划阶段 A,设计文档 §3)
- [x] CI 与打包:`pyproject.toml` 增加 pytest 配置与 `package-data`,`tests.yml` 改用 `pip install -e ".[dev]"` + `python -m pytest`
- [x] 编排器挂钟计时:适配器未上报延迟时填充真实耗时,ERROR 同样带耗时
- [x] 审批结果语义:`approval_result` 事件 + 显式拒绝检查(`… executed after approval was denied`)
- [x] API Token 认证:`SPECAGENT_API_TOKEN` 保护 `/api/*`(`/api/health` 除外,且不再泄露数据库 URL)
- [x] 仪表盘/文档漂移:版本徽章取自 `/api/health`、补 CANCELED 与分类标签、去掉硬编码测试数

### v0.9 — 规则、生成器、示例与门禁(阶段 B+C,设计文档 §5–§6)
- [x] 声明式约束:`require_before` / `max_calls` / `arg_range` / `arg_enum` / `arg_scope` / `role_allowed` + 纯函数评估器 `app/constraints.py`
- [x] 违规文案语法 `app/violations.py`(可无歧义回解析,供分诊使用)
- [x] probe 生成器 `app/probe_generator.py`(normal/boundary/paraphrase/bypass/injection/multi_turn/parameter_attack/privacy,中英文模板,用例 id 确定性)
- [x] YAML 校验收集式报错、未知键 fail-closed、`dump_spec_yaml` round-trip
- [x] `python` 适配器(`module:function`)与模块新鲜度(reload)
- [x] FinCare 示例(5 条规则、43 个用例、3 个缺陷)
- [x] `specagent-gate.yml` 改为矩阵,同时跑电商与 FinCare 示例

### v0.10 — Agent 层与发布(阶段 D+E,设计文档 §7–§9)
- [x] 共享运行核心 `app/project.py`;工具层(14 个工具、两级风险 + human_only、哈希绑定确认、停放动作)与路径沙箱
- [x] 确定性分诊 `specagent triage`;Agent 循环(离线状态机、脱敏会话记录)、`specagent agent` / `specagent draft`
- [x] 修复建议(只写 `.specagent/`)与 `specagent verify`(六种结论)
- [x] `specagent run --fail-on`;可复用 GitHub Action(`action.yml`、`app/ci.py`、`action-selftest.yml`)已在 GitHub 验证门禁、同一次工作流内缓存、PR 评论创建与报告上传，剩余场景见 U10 和 [发布证据核对](release-evidence-audit.md)
- [x] README 中文重写、`README.en.md`、`docs/demo-script.md`;版本 0.10.0
- [x] 仪表盘 Agent 面板(设计任务 16):`/api/agent` 三个端点(会话 / 消息 / 审批)与页面入口,需 `SPECAGENT_API_TOKEN`(或 `SPECAGENT_AGENT_API_INSECURE=1` 仅环回 opt-in),否则 403;确认一律停放、经审批端点执行;会话仅存内存(≤ 8 个、1 小时 TTL,见 known-issues U8)

## 待实现(backlog,按优先级)

### 发布与部署(§12、§13)
- [ ] 补齐 GitHub Action 的剩余场景
  - [x] baseline 通过、candidate 被门禁拦截、失败前上传 HTML/JUnit
  - [x] 同一次工作流内保存和命中恢复缓存、机器人创建 PR 评论
  - [x] 跨运行缓存恢复与已有评论更新，见 [Action 专项](action-integration-validation.md)
  - [x] 真实受限 token 被拒后的评论降级，保留原有门禁失败与报告
  - [ ] main 到 PR 的跨分支缓存恢复，待本次工作流合并后建立 main 基线
  - [ ] 真实 fork 的完整行为验证，目前没有外部账号的测试 fork
- [x] 失败 Demo GIF 已入库 `docs/assets/demo.gif`，README 已引用，v0.10 Release 附有 GIF 与演示视频。GIF 根据脚本与真实输出渲染生成，未覆盖本轮新增网页功能；宣传片 `promo/` 仍未入库
- [ ] 公开部署(Docker + 托管 Postgres)
- [ ] 冻结接口、v1.0 Release

2026-10-08 已合并网页补齐 PR #8–#22。当前包版本仍为 0.10.0，新增内容保留在 CHANGELOG 的 Unreleased。下一版建议为 v0.11，正式发布仍需版本改动、发布验证及批准，不把网页补齐完成等同于生产部署或接口冻结。

### 规划书 P2 项(延后,§7.3)
- [ ] OpenTelemetry trace 导入(P2,接生产 trace,工程量大)
- [ ] MCP Tool Proxy(P2,应晚于核心回归能力)

## 远期想法(不排期,规划书 §19)
- Stateful Scenario DSL(多阶段:登录→查询→确认→退款)
- MCP Tool Scope 权限测试
- Production Trace Replay
- Auto-minimize Failure(自动缩短对抗 Prompt)
