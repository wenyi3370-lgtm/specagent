# Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/);版本号遵循语义化版本。

## [v0.7] — 2026-10-05

安全、稳定性与隔离:测试真实 Agent 时不让 SpecAgent 本身变成风险源(规划书 §10)。

### Added
- **项目级 endpoint allowlist(§10.1 SSRF 防护)**:`specagent.yaml` `adapter.allowed_hosts` 与环境变量 `SPECAGENT_ALLOWED_HOSTS` 合并生效;HTTP 适配器对目标主机强制校验(精确匹配、端口匹配、`*.suffix` 通配,拒绝后缀伪装),不在名单内的主机直接拒呼;`specagent validate` 报告 allowlist 状态
- **有限重试(§10.2 Retry)**:新增 `TransientAgentError`——HTTP 适配器把 `httpx.TransportError` 与上游 5xx 归一化为该类型;编排器只重试瞬态错误(`run.retries`,默认 1 次,退避封顶 0.5s);业务 FAIL、4xx 永不重试
- **Run 取消(§10.2 Cancellation)**:`POST /api/runs/{id}/cancel`;run 在任何用例执行前即以 `status=running` 落库(可观察、可定位),取消后未执行用例标记 `CANCELED`(diff 归为独立 `CANCELED` 类别,不算回归不算失败),已完成的用例结果完整保留,run 终态 `canceled`
- **Trace / 响应体积上限(§10.2 Trace Limit)**:`run.max_trace_events`(默认 200)/ `run.max_response_chars`(默认 20000),超限截断并标记 `AgentExecution.truncated`,仪表盘显示截断徽章
- **项目级并发限制(§10.2 Concurrency Limit)**:同一项目的并发 run 共享信号量预算(`SPECAGENT_PROJECT_CONCURRENCY`,默认 8),防止多个 run 同时打爆目标 Agent
- `.env.example`、docker-compose、validate 输出同步上述配置

### Changed
- `AgentExecution` 新增 `truncated` 字段;`DiffSummary`/`DiffEntry` 新增 `CANCELED` 分类;`RunSummary` 的 `completed_at` 允许为 null(running 状态)

## [v0.6] — 2026-10-05

项目化、数据持久化与观测:从单次运行 Demo 升级为可长期使用的开发者工具(规划书 §9)。

### Added
- 存储层迁移 SQLAlchemy 2.x:`Store` 公开接口不变;`SPECAGENT_DB` 接受文件路径(SQLite)或完整 URL(`postgresql+psycopg://…`,Postgres 16),新增 `docker-compose.yml`(app + postgres 一键起)
- §9.1 实体化:
  - `projects` 表:`POST/GET /api/projects`;运行时 `ensure_project` 懒创建;旧库的存量 run 项目自动回填
  - `specs` 表:按项目版本化(版本号递增),内容哈希去重(相同编译结果不重复入库),保存 source_text + compiled_json
  - `violations` 归一化表:rule_id / severity / reason / evidence(tool_calls 快照),`Store.list_violations(run_id)` 可查询
  - `runs.spec_id` 关联;spec/tests 快照继续随 run 存档(保证任意历史 run 可独立重放 diff)
- 观测指标(§9.2,`app/metrics.py` 纯函数 + `GET /api/metrics?project_id=`):Behavior Pass Rate、Critical Violation Rate、New Regression Count(vs Baseline)、Flaky Rate、Tool Accuracy(剔除 ERROR)、Median/P95 Latency、score_history
- Dashboard:项目选择器(全局联动 Run 历史/指标/Diff)、项目列表(运行数/最近活动)、六格指标面板——对应 §9.3 四个问题
- `.env.example` 补充 Postgres URL、项目并发、allowlist 说明

### Changed
- `summarize`/`RunSummary` 增加 `canceled` 计数字段(为 v0.7 取消机制预留)
- 存储迁移机制升级为 SQLAlchemy inspect + ALTER TABLE(旧 v0.3–v0.5 SQLite 库可直接升级)

## [v0.5] — 2026-10-05

智能测试生成与混合判定:提高覆盖度,同时保持「AI 负责扩展、规则负责兜底」的边界(规划书 §8)。

### Added
- 测试类别补齐(§8.1):`multi_turn`(TestCase.history 随适配器契约转发,HTTP 契约带 `history` 字段)、`parameter_attack`(超大金额 / 伪造 user_id)
- LLM 用例扩展(§8.2,`app/expander.py`):LLM 生成改写/对抗变体 → 逐条 schema 校验、对既有套件归一化去重、每规则限额(默认 3);无 OPENAI_API_KEY 时为 no-op,离线可复现
- Judge 四层落地(§8.3):
  - 语义层(Trace Semantic Judge):`max_amount` 危险参数检查 — 阈值金额不得经审批工具与受控动作之外的任何工具外泄(如把退款金额塞进 `transfer_money()`),确定性、可单测
  - LLM Judge(`app/llm_judge.py`):仅处理规则 `llm_checks` 声明的语义指标;输出必须是结构化 `LLMJudgeVerdict`(verdict/confidence/rule_id/evidence_event_ids/reason,§8.4),evidence 自动过滤为真实存在的 trace 事件 id;**advisory**——存储与展示,但不改变确定性 PASS/FAIL
  - Human Review(§8.3 layer 4):`POST /api/executions/{id}/review` 持久化人工裁决,run 详情与仪表盘展示复核记录
- 编排器对带 `llm_checks` 的用例自动附加 LLM 裁决;`specagent run --llm-expand` 与 `specagent.yaml` `run.llm_expand`
- 存储:executions 表新增 `llm_verdict_json` / `review_json` 列,旧库自动迁移(`PRAGMA table_info` 检查 + ALTER TABLE)
- 示例 spec 增加 `llm_checks` 示例;仪表盘展示 LLM 裁决徽标与 FAIL 用例的人工复核按钮

### Notes
- LLM 扩展与 LLM Judge 失败/未配置时分别退化为 no-op 与 `uncertain`(skipped),门禁行为完全不变 —— CI 判定保持确定性

## [v0.4] — 2026-10-05

多 Agent 适配与 Trace 标准化:核心测试引擎与具体框架解耦,适配器只负责"运行并采集 trace",Judge 永远只看统一 TraceEvent(规划书 §7)。

### Added
- `AgentAdapter` 接口正式化(§7.2):`async execute(case, context) -> AgentExecution`,新增 `app/adapters/` 包(base / demo / http / openai / langgraph)
- **OpenAI Responses 原生适配器**(§7.3 P1):`OpenAIAgentDefinition`(model + instructions + 工具 executor 注册表),适配器执行 function-calling 循环并把每轮调用归一化为 `tool_call`/`tool_result` 事件;OpenAI 客户端可注入,全套离线单测
- **LangGraph 适配器**(§7.3 P1):`astream_events`(v2)→ 统一 Trace;图对象鸭子类型,适配器本身零 langgraph 依赖
- TraceEvent 补齐 §7.1 字段:`id`(自动 `evt_<seq>`)、`result`、`metadata`;AgentExecution 增加 `raw`(适配器原生响应)
- `specagent.yaml` 适配器扩展:`type: openai|langgraph` + `agent: module:attribute` 导入路径;`specagent validate` 做导入、类型与工具清单检查
- HTTP 契约新增可选 `history` 字段(多轮用例前置,向后兼容)
- 示例项目 `examples/openai-agent`:OpenAI Agent 定义模板 + mock 沙箱工具
- 适配器测试:demo/http(MockTransport)/openai(脚本化假客户端)/langgraph(假图)与解析错误路径

### Changed
- 编排器不再 import 任何框架:统一经 `resolve_adapter()` 构造适配器;demo/http 逻辑迁入适配器层(原 `app/agents/http_agent.py` 移除)
- 遵守 §7.4:适配器不判定 PASS/FAIL、不解释业务规则、无每框架 Judge

## [v0.3] — 2026-10-05

CLI 与 GitHub CI 门禁:SpecAgent 进入真实研发流程,PR 阶段自动阻断关键行为回归(规划书 §6)。

### Added
- `specagent` CLI:`init` / `validate` / `run` / `baseline` / `diff` / `export`,每个命令有 `--help`
- CI 门禁策略(§6.4):critical/high `NEW_REGRESSION` → exit 1;`PERSISTENT_FAIL` 显示但不阻断;`FLAKY` 不进门禁;配置错误 exit 2
- 退出码约定:0=通过,1=门禁失败,2=配置错误;本地与 CI 行为一致
- `specagent.yaml` 项目配置 + YAML Behavior Spec(schema 校验、规则 id 去重、字段级错误信息,无 traceback)
- `specagent export --format junit|json`:JUnit XML 供 CI 原生展示
- GitHub Actions:`tests.yml`(单测)+ `specagent-gate.yml`(门禁自检:基线全过 → 候选出现 Critical 回归被 exit 1 阻断 → 修复转 FIXED)
- 示例项目 `examples/ecommerce-agent`:demo 双变体(patched 基线 / vulnerable 候选)离线复现十步回归故事
- CLI `--json` 机器可读输出;`--baseline last` 相对最近一次 run 对比(演示「修复后 FIXED」);`GITHUB_STEP_SUMMARY` PR 摘要
- `pyproject.toml`(`pip install -e .` 提供 `specagent` 入口)

### Changed
- 测试生成器覆盖四类以上用例:normal / boundary / paraphrase / bypass / injection / privacy
- Dashboard 新增 Run 历史、Set as Baseline、Diff 视图(critical 置顶、双栏 trace 对比、高亮新增 tool_call)
- v0.1 接口 `/api/run-all` 保留并自动持久化 + 返回 `run_id`/`diff`

## [v0.2] — 2026-10-05

Regression Diff:从「一次性测试器」升级为「回归检测器」(规划书 §5)。

### Added
- SQLite 持久化(`SPECAGENT_DB`,默认 `./specagent.db`):runs / executions;spec 与 tests 快照随 run 保存,历史 run 可独立重放 diff
- Baseline 管理:项目内唯一(Store 约束 + API `POST /api/runs/{id}/baseline` + CLI `specagent baseline`)
- Diff 引擎(`app/regression.py`):NEW_REGRESSION / FIXED / PERSISTENT_FAIL / STABLE_PASS / NEW_TEST / FLAKY / NEW_ERROR,NEW_REGRESSION 置顶、severity 排序
- 重复执行检测 FLAKY(`run.repeat`),不作为稳定回归
- 编排器(`app/orchestrator.py`):asyncio.Semaphore 并发、单用例超时(超时→ERROR)、异常隔离(单用例失败不中断整轮)
- 核心 API(规划书 §14.4):`POST /api/runs`、`GET /api/runs/{id}`、`POST /api/runs/{id}/baseline`、`GET /api/runs/{id}/report`、`GET /api/diff`、`GET /api/executions/{id}/trace`、`POST /api/specs/compile`
- 统一 Trace 事件(§7.1 前置):类型别名归一化、seq/timestamp 补齐、凭证字段脱敏(token/authorization/password/cookie/…)
- 判定泛化:审批闸门改为「gated 工具必须出现在审批调用之后」的时序断言(`approval_for`),不再硬编码 refund
- 测试体系:compiler / generator / judge / trace / regression / storage 单测 + API 集成 + CLI 门禁端到端(57 个用例)

### Changed
- demo agent 重构:无金额时不再执行 `refund(0)`;新增 `variant` 参数(`vulnerable` 默认 / `patched` 基线)
- LLM 编译降级可感知:warning 日志 + `compiler` 字段标注 `+llm-fallback`
- 测试生成器按 `rule.action` 特征路由,解除对 rule.id 的硬编码依赖
- `status` 语义:PASS / FAIL / ERROR / FLAKY 分离(超时是 ERROR 不是 FAIL)

### Fixed
- v0.1 全部 P1/P2 已知问题(G1 金额子串误匹配、G2 异常隔离、G3 审批闸门泛化、D1–D4、H1、H3),见 `docs/known-issues.md`

## [v0.1] — 2026-10-05(基线)

MVP:打通「自然语言规则 → 规格编译 → 测试生成 → Agent 执行 + trace 采集 → 确定性判定 → 可视化回归报告」完整闭环。

### Added
- Spec Compiler 双通道:OpenAI Responses API 编译,无 key 或失败时降级为确定性中文电商匹配器
- 测试生成器:正常 / 边界(阈值±1)/ 社工绕过 / 提示注入 / 隐私 五类用例
- 内置演示 Agent(含 2 个故意 bug:主管话术绕过退款审批、"不用确认"绕过地址确认)
- 外接 Agent HTTP 适配器(`TARGET_AGENT_URL` + Bearer Token,trace 契约)
- 确定性 Judge:required / forbidden calls 比对 + 退款审批闸门
- 深色单页仪表盘(行为得分、通过/失败、规则卡、逐用例 trace 与违规详情)
- Dockerfile、`.env.example`、基础冒烟测试 `tests/test_core.py`
