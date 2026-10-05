# Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/);版本号遵循语义化版本。

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
