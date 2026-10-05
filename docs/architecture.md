# SpecAgent 架构梳理(基于 v0.6)

> 本文档记录当前基线的实际架构,作为后续迭代的对照基础。改动架构时请同步更新本文。
> 迭代蓝图见 `docs/roadmap.md`(对齐《SpecAgent 产品与工程迭代规划书 v1.0》)。

## 核心链路

```
自然语言需求(前端 textarea)            specs/behavior.yaml(YAML Spec)
        │                                       │
        ▼                                       ▼
┌─────────────────┐                      ┌─────────────────┐
│  Spec Compiler  │  OPENAI_API_KEY 存在 │  YAML Spec 加载  │
│  (compile_spec) │──→ OpenAI Responses  │  (spec_yaml)    │ schema 校验 + 字段级错误
└─────────────────┘   失败/无 key → demo   └─────────────────┘
        │           降级可见(compiler 字段标注)
        ▼
   BehaviorSpec(rules[]:require/forbid/approval_for/severity)
        │
        ▼
┌─────────────────┐
│ Test Generator  │  按 action 特征路由(refund/address/delete/query/默认)
│  (generator)    │  正常 / 边界(±1)/ 改写 / 社工 / 注入 / 隐私
└─────────────────┘
        │
        ▼
┌─────────────────┐   agent=http → TARGET_AGENT_URL(POST {message})
│  Orchestrator   │──┤  agent=demo → 内置 demo agent(variant: vulnerable|patched)
│ (asyncio 执行)  │   并发信号量 · 单用例超时 · 异常隔离 · repeat→FLAKY
└─────────────────┘
        │
        ▼
   AgentExecution{response, trace[], error}   ← trace.normalize(别名/脱敏/seq)
        │
        ▼
┌─────────────────┐
│      Judge      │  确定性比对:required / forbidden / 审批闸门(approval_for
│                 │  工具必须出现在审批调用之后) / ERROR 单独标记
└─────────────────┘
        │
        ▼
┌─────────────────┐   SQLite(app/storage.py,表 runs + executions;
│     Store      │──┤ spec/tests 快照随 run 保存;每项目唯一 baseline)
└─────────────────┘   v0.6 可整体替换为 PostgreSQL
        │
        ▼
┌─────────────────┐
│ Regression Diff │  PASS→FAIL=NEW_REGRESSION;FAIL→PASS=FIXED;
│ (regression)    │  FAIL→FAIL=PERSISTENT;FLAKY/NEW_TEST/NEW_ERROR
└─────────────────┘
        │
        ├─→ Dashboard(app/static/index.html):Run 历史 / Set as Baseline /
        │   Diff 视图(critical 置顶,双栏 trace 对比,高亮新增 tool_call)
        │
        └─→ CLI(cli/specagent.py):run → diff → 门禁判定
            critical/high NEW_REGRESSION → exit 1(阻断 PR)
            配置错误 → exit 2;GITHUB_STEP_SUMMARY 输出 PR 摘要
            export → JUnit XML / JSON
```

## 模块职责

| 模块 | 职责 | 关键点 |
|---|---|---|
| `app/main.py` | FastAPI 入口;`/api/runs`、`/api/runs/{id}`、`/api/runs/{id}/baseline`、`/api/diff`、`/api/executions/{id}/trace`、`/api/run-all`(兼容)、`/api/health` | v0.6 新增 `/api/projects`(POST/GET)、`/api/specs`、`/api/metrics`(§9.2) |
| `app/metrics.py` | 观测指标(§9.2)纯函数 | pass rate / critical violation rate / flaky rate / tool accuracy / median+P95 latency / new regressions |
| `app/models.py` | 全部 Pydantic 模型,单一事实来源 | `approval_for`、`ExecutionStatus`(PASS/FAIL/ERROR/FLAKY/CANCELED)、`DiffSummary` |
| `app/compiler.py` | NL → BehaviorSpec;双通道编译 | LLM 失败降级有 warning 日志,`compiler` 字段标注 `+llm-fallback` |
| `app/spec_yaml.py` | YAML Spec 加载(roadmap §14.1 形态) | 规则 id 去重、pydantic 校验、字段级错误信息 |
| `app/config.py` | `specagent.yaml` 项目配置(§11.1) | 相对 spec 路径按配置文件目录解析;`extra=forbid` 抓拼写错误 |
| `app/generator.py` | 规则 → 测试用例 | **按 action 特征路由**(不依赖 rule.id);阈值越界在构造用例时显式标记;v0.5 新增 multi_turn(带 history)/ parameter_attack 类别 |
| `app/expander.py` | LLM 用例扩展(§8.2,可选) | seed 兜底;生成结果逐条 schema 校验 + 归一化去重 + 每规则限额;无 key 时 no-op |
| `app/trace.py` | Trace 事件规范化 | 类型别名兼容(`tool`→`tool_call`)、凭证脱敏(token/authorization/…)、`id=evt_<seq>` 补齐、未知类型丢弃告警 |
| `app/judge.py` | ① 确定性判定 + ② 语义层 | required / forbidden / 审批闸门(approval_for 时序)/ 危险参数检查(max_amount 不得经其他工具外泄) |
| `app/llm_judge.py` | ③ LLM Judge(§8.3 layer 3,可选) | 仅处理规则 `llm_checks` 声明的语义指标;输出必须结构化 `LLMJudgeVerdict` 并绑定 evidence id;advisory,不改 PASS/FAIL |
| `app/orchestrator.py` | 执行编排 | asyncio.Semaphore 并发、单用例超时、异常隔离(单用例失败→ERROR 不中断)、repeat→FLAKY |
| `app/storage.py` | SQLAlchemy 持久化(§9.1):projects / specs / runs / executions / violations | `SPECAGENT_DB` 支持文件路径(SQLite)或 URL(PostgreSQL);接口不变;spec/tests 快照随 run 存;violations 归一化可查询;旧库自动迁移 |
| `app/regression.py` | Diff 分类 + CI 门禁策略 | 纯函数;NEW_REGRESSION 置顶、severity 排序;`gate_violations(fail_on)` |
| `app/agents/demo.py` | 内置演示 Agent 本体 | `variant=vulnerable` 带 2 个故意 bug;`variant=patched` 全过(作 CI 基线) |
| `app/adapters/` | 适配器层(roadmap §7):demo / http / **openai** / **langgraph** | `AgentAdapter.execute(case, context) -> AgentExecution`;只运行与采集 trace,不做判定;openai 客户端可注入,langgraph 图对象鸭子类型 |
| `cli/specagent.py` | CLI:init / validate / run / baseline / diff / export | exit 0/1/2 语义;`--json`;`--baseline last`;JUnit XML;GitHub Step Summary |
| `app/static/index.html` | 深色单页仪表盘,原生 JS 无构建 | Run 历史、Set as Baseline、Diff 视图(NEW/FIXED/PERSISTENT/FLAKY 计数) |

## 数据模型链

```
BehaviorSpec ──1:N── BehaviorRule(require_calls / forbid_calls / approval_for / severity)
      │
      ▼
TestCase(rule_id 关联;category ∈ normal/boundary/paraphrase/bypass/injection/privacy)
      ▼
AgentExecution(response + trace: TraceEvent[] + error)
      ▼
TestResult(status: PASS|FAIL|ERROR|FLAKY + violations[] + execution_id)
      ▼
Store:runs(id, project_id, is_baseline, spec_json, tests_json, 统计)
           └─ executions(id, run_id, test_case_id, status, trace_json, violations_json)
      ▼
RegressionDiff(baseline_run_id, candidate_run_id, entries[]:diff_type + severity)
```

## 对外契约(外部 Agent 需实现)

```
POST TARGET_AGENT_URL
请求:  {"message": "用户测试输入"}
响应:  {"response": "文本",
        "trace": [{"seq": 1, "type": "tool_call", "name": "refund", "args": {"amount": 1200}}]}
```

trace 事件类型(roadmap §7.1):`user_message | assistant_message | tool_call | tool_result | approval_request | approval_result | error`。`type` 常见别名会被自动归一化;缺失 `seq`/`timestamp` 自动补齐;凭证类字段自动脱敏。

## 设计决策记录

- **确定性 Judge 优先**:能用 trace 硬校验的不交给 LLM 评审;审批闸门泛化为「gated 工具必须出现在审批调用之后」的时序断言,不再绑定 refund。
- **Judge 分层(§8.3)**:① 确定性 → ② 语义危险参数检查 → ③ LLM Judge(仅 `llm_checks` 声明的语义指标,结构化输出 + evidence,advisory)→ ④ Human Review(持久化裁决)。LLM 层永不改 PASS/FAIL,保证 CI 门禁可复现。
- **AI 扩展、规则兜底(§8.2)**:LLM 只在确定性 seed 之上追加用例,逐条 schema 校验、去重、限额;LLM 不可用时整条流水线照常工作。
- **回归优先**:一切对比围绕「这次改动新坏了什么」;PERSISTENT_FAIL 默认不阻断 PR,FLAKY 不进门禁(roadmap §6.4)。
- **Run 快照式持久化**:spec 与 tests 随 run 一起存,任何历史 run 都能独立重放 diff;v0.6 换 Postgres 时只需替换 `Store` 实现。
- **Demo 双变体**:`patched`(基线)/ `vulnerable`(候选)让「改一行提示词 → 出现 Critical 回归 → CI 失败 → 修复 → FIXED」的完整故事可以离线复现(roadmap §12.3)。
- **演示不依赖外部服务**:CI 自检工作流(`.github/workflows/specagent-gate.yml`)用 demo 双变体验证门禁真的会失败。
