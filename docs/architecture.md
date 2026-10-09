# SpecAgent 架构梳理(基于 v0.10)

> v0.9/v0.10 的新增内容(约束、probe 生成器、python 适配器、Agent 层、CI Action)集中在文末的"v0.9 / v0.10 增量"一节;上方的核心链路图仍然成立,只是生成器、judge、适配器各自多了下文所述的能力。

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
| `app/project_templates.py`、`app/static/connect-guide.js` | CLI init 与网页接入向导共享静态模板 | GET 仅认证，不检查服务器项目；复制配置和规则不写文件，显式校验使用原 Validate 认证、JSON/Host 和项目锁 |
| `app/notifications.py`、`app/notification_api.py` | 服务端渠道、人工确认与发送历史 | 独立表复用 Store engine；摘要与配置指纹绑定当前登录；条件更新认领后才发送；TLS、禁用重定向、固定失败代码 |
| `app/static/notifications.js` | 渠道开关、预览、授权勾选与分页历史 | 文本节点渲染、迟到响应保护、双语与焦点操作；不接受地址或凭据；项目切换和选择变更清除旧确认 |
| `app/accounts.py` | 本机账号配置、密码摘要、会话与项目授权 | 独立 SQLAlchemy 表复用 Store engine，不增加 Store 公共写方法；PBKDF2、随机会话摘要、持久限流和请求 ContextVar |
| `app/account_api.py`、`app/web_access.py` | 浏览器登录与管理员授权、API 权限检查 | multiuser 显式启用，逐次检查项目、资源 ID 与执行引用，未分类接口拒绝；写入需要 CSRF 和同源 |
| `app/web_tools.py`、`app/static/accounts.js` | 网页 Agent 项目限制、登录和权限界面 | ToolRegistry 的网页专用子类检查运行引用；CSRF 只在内存，cookie 保持 HttpOnly；默认共享模式不变 |
| `app/main.py` | FastAPI 入口;`/api/runs`、`/api/runs/{id}`、`/api/runs/{id}/baseline`、`/api/diff`、`/api/executions/{id}/trace`、`/api/run-all`(兼容)、`/api/health` | v0.6 新增 `/api/projects`(POST/GET)、`/api/specs`、`/api/metrics`(§9.2);`[Unreleased]`(方案 B)新增 `GET /api/project` 与 `POST /api/project/runs`(运行服务端配置的项目;后者强制 JSON Content-Type、无 token 模式校验环回 Host,且与 `run_project` 共用每配置文件一把 OS 文件锁,并发第二个请求 → 409) |
| `app/metrics.py` | 观测指标(§9.2)纯函数 | pass rate / critical violation rate / flaky rate / tool accuracy / median+P95 latency / new regressions |
| `app/report.py` | 独立 HTML 报告(§11.2) | `specagent report --open`:零 JS 静态页,run + diff + 逐用例证据,可离线分享;`--open` 调 webbrowser |
| `app/models.py` | 全部 Pydantic 模型,单一事实来源 | `approval_for`、`ExecutionStatus`(PASS/FAIL/ERROR/FLAKY/CANCELED)、`DiffSummary` |
| `app/compiler.py` | NL → BehaviorSpec;双通道编译 | LLM 失败降级有 warning 日志,`compiler` 字段标注 `+llm-fallback` |
| `app/spec_yaml.py` | YAML Spec 加载(roadmap §14.1 形态) | 规则 id 去重、pydantic 校验、字段级错误信息 |
| `app/config.py` | `specagent.yaml` 项目配置(§11.1) | 相对 spec 路径按配置文件目录解析;`extra=forbid` 抓拼写错误 |
| `app/generator.py` | 规则 → 测试用例 | **按 action 特征路由**(不依赖 rule.id);阈值越界在构造用例时显式标记;v0.5 新增 multi_turn(带 history)/ parameter_attack 类别 |
| `app/expander.py` | LLM 用例扩展(§8.2,可选) | seed 兜底;生成结果逐条 schema 校验 + 归一化去重 + 每规则限额;无 key 时 no-op |
| `app/trace.py` | Trace 事件规范化 | 类型别名兼容(`tool`→`tool_call`)、凭证脱敏(token/authorization/…)、`id=evt_<seq>` 补齐、未知类型丢弃告警 |
| `app/judge.py` | ① 确定性判定 + ② 语义层 | required / forbidden / 审批闸门(approval_for 时序)/ 危险参数检查(max_amount 不得经其他工具外泄) |
| `app/llm_judge.py` | ③ LLM Judge(§8.3 layer 3,可选) | 仅处理规则 `llm_checks` 声明的语义指标;输出必须结构化 `LLMJudgeVerdict` 并绑定 evidence id;advisory,不改 PASS/FAIL |
| `app/orchestrator.py` | 执行编排 | asyncio.Semaphore 并发 + 项目级并发预算、单用例超时、异常隔离(单用例失败→ERROR 不中断)、仅瞬态错误重试、trace/响应截断、取消检查、repeat→FLAKY |
| `app/storage.py` | SQLAlchemy 持久化(§9.1):projects / specs / runs / executions / violations | `SPECAGENT_DB` 支持文件路径(SQLite)或 URL(PostgreSQL);接口不变;spec/tests 快照随 run 存;violations 归一化可查询;旧库自动迁移 |
| `app/regression.py` | Diff 分类 + CI 门禁策略 | 纯函数;NEW_REGRESSION 置顶、severity 排序;`gate_violations(fail_on)` |
| `app/agents/demo.py` | 内置演示 Agent 本体 | `variant=vulnerable` 带 2 个故意 bug;`variant=patched` 全过(作 CI 基线) |
| `app/adapters/` | 适配器层(roadmap §7):demo / http / **openai** / **langgraph** | `AgentAdapter.execute(case, context) -> AgentExecution`;只运行与采集 trace,不做判定;openai 客户端可注入,langgraph 图对象鸭子类型;v0.7:`TransientAgentError` 分类 + SSRF allowlist 强制校验 |
| `app/cancellation.py` | Run 取消注册表(§10.2) | run 执行前登记,`POST /api/runs/{id}/cancel` 置取消位;未执行用例 → CANCELED,已完成结果保留 |
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

- **指标趋势共用计算**：`compute_metric_history` 调用 `compute_run_metrics` 与 `regression.diff_runs`，不修改现有 CLI/指标函数输出。`Store.get_metric_runs` 先按项目、开始时间和 completed/canceled 状态筛选，再限量到最新记录并返回匹配总数。`GET /api/metrics/history?project_id=…&days=7|30|90|all&limit=1..500` 受认证保护，返回 UTC 查询窗口、当前基线 ID、总数、截断标记和时间升序数值。网页原生 SVG 按真实时间绘图，百分比固定 0–100%，数据点支持焦点与键盘，另提供数值表。新回归按当前基线比较，不宣称保存了历史门禁决定。无需数据库迁移。
- **项目资料与执行配置分开**：网页创建复用 `POST /api/projects` 与 `Store.create_project`，只写 ID、名称、说明及兼容的适配器资料。请求拒绝额外字段，并按数据库列宽限制长度；同 ID 并发插入也返回 409。显示文本统一脱敏，ID 保持原样用于查询。列表用文本节点和闭包事件处理任意名称及 ID，不拼接内联脚本。项目选择仅筛选历史；`/api/project/runs` 继续从服务端配置加载目标。列表、运行、指标和规格/趋势都有响应代次保护，旧请求不会覆盖切换后的视图。无需数据库迁移。

- **规格只读查看**：`app/spec_views.py` 处理历史源格式、脱敏和 unified diff。`GET /api/project/spec` 只读取服务端配置的规则，不导入适配器。`GET /api/specs` 沿用版本列表，新增 `GET /api/specs/{id}`、`GET /api/specs/{id}/source` 与 `GET /api/specs/diff?baseline=…&candidate=…`，均受认证保护。跨项目比较返回 422，未知版本返回 404，源内容超过读取上限返回 413。下载按脱敏后的 UTF-8 源内容保留换行，DOM 用 textContent。版本关联运行最多返回最近 50 条并提供总数。无需迁移或修改版本去重规则，旧 JSON/文本源仍可查看。

- **实时进度不改变判定**：`execute_suite` 每个用例完成后通知记录器，重复与重试只计算一次。`run_with_diff` 在 `run_progress` 表保存快照。整轮执行后的无工具调用降级保持原逻辑，之后重算最终计数。`GET /api/runs/{id}/progress` 受认证保护，旧运行回退到已有摘要。网页每 500 ms 读取，等待请求完成前即可显示计数；旧请求不会覆盖新运行。服务重启只保留最后快照，不恢复执行或取消权限。CLI 默认保持原有输出与持久化顺序，网页和 Agent 面板显式启用实时记录。
- **运行管理保留证据**：`run_trash` 保存运行 ID 与删除时间，初始化时自动建表。默认 `list_runs`、项目计数与指标排除其中的记录，按 ID 读取仍保留。`GET /api/runs/search` 先筛选再计数和分页，单页最多 100 条，ID/标签/Agent 的子串搜索转义 SQL 通配符。`GET /api/runs/{id}/management` 提供摘要、执行与违规数量和保护原因。标签 PATCH、运行 DELETE 与 restore POST 要求认证及 JSON，无 token 时校验环回 Host。删除核对 ID，条件写入再次检查非基线、非 running、未删除并锁定运行行；基线设置在同一事务内检查未删除，否则回滚。管理写入只供网页路由使用，没有注册为 Agent 工具，也不导入配置模块。
- **重复结果保留原始判定**：`execute_suite` 在每次限制响应/轨迹并完成确定性 Judge 后深拷贝快照，聚合 FLAKY、取消或整轮无工具验证不会重写快照。LLM 裁决仍每用例一次。取消检查保存一个 `executed=false` 标记，剩余次数不生成虚构执行。瞬态重试仍属于同一次重复。`TestResult` 的私有属性保存过程证据，公开 JSON 结构不变；`result_to_storage` 将其写入已有 `Execution.repeat_json`，不保存适配器 raw payload。两个只读接口列出和对比已保存内容，认证、凭据脱敏和展示上限独立于判定；指标继续使用原有最终结果。

- **修复建议只读展示**：`app/suggestions.py` 共用列表、详情、diff 和复验历史。`GET /api/project/suggestions`、`GET /api/project/suggestions/{id}`、`GET /api/project/suggestions/{id}/fix.diff` 全部受认证保护，路径只来自服务端项目根和严格校验的 ID。原始 diff 按字节下载，DOM 按文本显示。沿用 `POST /api/project/verify` 执行复验并写独占创建的历史文件，同秒复验不覆盖记录。CLI 提供 `suggestions list/show`，没有 apply 命令或网页应用接口。

- **确定性 Judge 优先**:能用 trace 硬校验的不交给 LLM 评审;审批闸门泛化为「gated 工具必须出现在审批调用之后」的时序断言,不再绑定 refund。
- **Judge 分层(§8.3)**:① 确定性 → ② 语义危险参数检查 → ③ LLM Judge(仅 `llm_checks` 声明的语义指标,结构化输出 + evidence,advisory)→ ④ Human Review(持久化裁决)。LLM 层永不改 PASS/FAIL,保证 CI 门禁可复现。
- **AI 扩展、规则兜底(§8.2)**:LLM 只在确定性 seed 之上追加用例,逐条 schema 校验、去重、限额;LLM 不可用时整条流水线照常工作。
- **回归优先**:一切对比围绕「这次改动新坏了什么」;历史行为 FAIL 默认不阻断 PR。所有严重度的 ERROR 与 FLAKY 默认阻断，包括首次无基线运行；可用 gate.block_errors 与 gate.block_flaky 分别关闭。
- **Run 快照式持久化**:spec 与 tests 随 run 一起存,任何历史 run 都能独立重放 diff;v0.6 换 Postgres 时只需替换 `Store` 实现。
- **Demo 双变体**:`patched`(基线)/ `vulnerable`(候选)让「改一行提示词 → 出现 Critical 回归 → CI 失败 → 修复 → FIXED」的完整故事可以离线复现(roadmap §12.3)。
- **演示不依赖外部服务**:CI 自检工作流(`.github/workflows/specagent-gate.yml`)用 demo 双变体验证门禁真的会失败。

## v0.9 / v0.10 增量

### 新增与变更的模块

| 模块 | 职责 | 关键点 |
|---|---|---|
| `app/constraints.py` | 条件约束评估器(纯函数,无 I/O、无时钟) | 六种约束 `require_before / max_calls / arg_range / arg_enum / arg_scope / role_allowed`;逐约束 try/except → `evaluation_error`(bug 只会产生 FAIL,不会静默 PASS);`when` 缺失 arg 不命中、排序运算符遇非数值命中(fail-closed) |
| `app/violations.py` | 违规文案语法的唯一定义 | 带标签形式 `[{kind}] {tool}({args}) {message}…`;`parse_violation` 可回解析新旧两种文案,分诊只依赖此模块;参数经 `format_args` 消毒 |
| `app/probe_generator.py` | 规则 `probes` → 用例 | 九行固定表(normal/boundary/明显超阈值/paraphrase/bypass/injection/multi_turn/parameter_attack/privacy),中英文短语常量化,用例 id 确定性,单规则 > 80 个用例直接报错而不是截断 |
| `app/adapters/python_adapter.py` | `adapter.type: python`,直接调用 `module:function` | 入参按签名过滤;返回 `AgentExecution` 或 dict;trace 走同一归一化管线;超时是放弃等待而非强杀线程(见 known-issues U7) |
| `app/auth.py` | API Token 认证 | `hmac.compare_digest`;统一 401;`agent_api_enabled()` / `require_agent_enabled` 是仪表盘 Agent 面板的开关:有 token,或 `SPECAGENT_AGENT_API_INSECURE=1` 且客户端为环回地址,否则 403 |
| `app/agent_api.py` | 仪表盘 Agent 面板 API(`/api/agent`) | 三个同步端点:`sessions` / `messages` / `approve`;依赖顺序 token(401)→ 启用(403);确认一律 deferred 停放,只有 `approve` 能执行(human_only 的人类通道);项目来自服务端 `SPECAGENT_PROJECT_CONFIG`,`allow_source` 只取自配置,请求体 `extra="forbid"`;JSON 检查点持久化会话和待审批动作(每配置八个、1 小时空闲 TTL、LRU 淘汰,每会话一把 OS 锁,见 U8) |
| `app/project.py` | **共享运行核心** | `Project`(只读属性面,可变配置锁在私有 `_config`;`[Unreleased]` 新增 `make_adapter()` / `run_settings` / `adapter_label` / `endpoint_env` 只读辅助与 `project_config_path()`,后者与 `agent_api` 共用)、`run_project`(生成 → 执行 → 持久化 → diff → gate 的唯一实现,CLI `run` 与 agent `run_suite` 共用;`fail_on_override` 仅供 CLI `--fail-on`;同配置运行在共享项目文件系统上跨进程串行)、`verify_project`、确定性引用块 |
| `app/agent/sandbox.py` | 路径沙箱与脱敏 | `resolve_read` 三层防线(词法/realpath 包容/组件拒绝名单);完整读取跟踪;`redact_text`;`ensure_state_dir` 先建 `.specagent/.gitignore` |
| `app/agent/tools.py` | 14 个工具注册表 + 单一门禁 `ToolRegistry.call` | 风险分级 auto/confirm,`replace_spec`/`set_baseline` 为 human_only;`prepare → confirm → recheck → act` 哈希绑定确认;停放动作;输出脱敏与截断 |
| `app/agent/triage.py` | 确定性分诊(纯函数) | 按 `(category, tool, arg)` 合并违规,固定 hint 模板;ERROR 分列 |
| `app/agent/fixes.py` / `app/verify.py` | 修复建议 diff / verify 判定 | EOL 保持的 unified diff(`git apply` 可用);六种结论判定表;建议只写 `.specagent/`,项目树不变 |
| `app/agent/loop.py` / `app/llm_client.py` | Agent 循环 / 模型解析 | 停放动作协议(每次 `responses.create` 每个 call_id 恰有一个 output);无 key 时的离线状态机;会话记录逐字符串脱敏;`openai` 仅在函数内延迟导入 |
| `app/ci.py` | GitHub Action 的判定/渲染逻辑 | `mode` / `run-id` / `comment` 三个子命令;仅标准库,Action 按脚本路径调用,避免被调用方仓库里同名的 `app` 包抢先导入 |
| `app/verify.py` | verify 结论 | ALL_FIXED/NO_CHANGE → 退出码 0,其余 → 1 |

### Trace 语义

- 只有携带布尔 `approved` 的 `approval_result` 事件才构成审批决定(最新的为准);审批请求、其他工具调用、无布尔的结果都不会重置或掩盖之前的决定。
- `approval_response`、`human_approval_result` 归一化为 `approval_result`。
- 显式拒绝之后仍执行受控工具 → `… executed after approval was denied`;没有 `approval_result` 事件时行为与旧版逐字节一致。

### 约束与判定顺序

judge 先运行四类旧检查(输出逐字节不变),再追加显式拒绝检查与约束检查产生的新违规串(仅新串去重)。约束检查不改变旧字段的语义:带 `probes` 的规则由 probe 生成器按 `spec.locale` 出题,其旧的 `require_calls` / `approval_for` / `max_amount` 对 probe 用例被忽略,`constraints` 就是判定基准。

### Agent 层(AI 提议,规则验证)

```
specagent agent / draft ─────────────┐
                                      ├─→ AgentSession / OfflineWorkflow(loop.py) ──→ ToolRegistry.call(tools.py) ──→ Project / run_project
仪表盘面板 → /api/agent(agent_api.py)┘          │                                        │
  token 401 → 启用 403;确认一律停放,             │                                        └─ 风险门禁:auto / confirm / human_only
  只经 /approve 执行                               └─ Transcript(脱敏 JSONL)                   哈希绑定确认;停放动作
```

- Agent 包有 AST 允许清单测试(`test_agent_package_allowlist`):禁止导入 `app.judge` / `app.llm_judge` / `app.constraints` / `app.orchestrator` / `app.adapters` / `app.generator` / `app.compiler` 以及 `subprocess` / `shutil` 等;文件写入只能发生在命名的写入函数内;Store 写方法(`set_baseline` 之外的也一样)不得被直接调用,`set_baseline` 标识符只允许出现在 `_h_set_baseline` 内;`_config` 与 `fail_on_override` 不得出现在 `app/agent/*`。
- 因此 Agent 没有任何通路改变判定逻辑、判定结果或 `gate.fail_on`:它只能调用 `run_project` 产生新的运行,结论仍由 `judge` + `constraints` 给出。
- CLI 退出码:0 成功 · 1 门禁失败 · 2 配置错误 · 4 Agent 会话异常中止或确认被拒。

### CI(可复用 Action)

`action.yml`(composite)→ 安装 → `app/ci.py mode`(推送到默认分支 = baseline,其他 = candidate,可显式覆盖)→ 恢复缓存的基线数据库 → `specagent run`(`OPENAI_API_KEY` 置空,永不调用 agent/draft)→ 报告/JUnit → 保存缓存(仅 baseline)→ 上传产物 → 可选 PR 评论 → 最后一步在门禁失败时才让 job 失败,保证产物先上传。该 Action 尚未在 GitHub 上实测(known-issues U10)。

### 仪表盘 Agent 面板

设计任务 16 已在 v0.10 交付(原 known-issues U9 已关闭)。面板与 CLI 共用同一套 `AgentSession` / `OfflineWorkflow` / `ToolRegistry`,因此上面的 AST 允许清单与风险门禁对它同样成立;`main.py` 把共享 `Store` 传给 `agent_api`,面板运行出现在仪表盘的运行历史中。浏览器无法指定路径或开启 `allow_source`。会话状态存入原数据库，重启及同机 worker 可恢复，原身份与校验保留。中途退出的操作不自动重放(known-issues U8)。


## 命令行与网页共用层

`app/project.py` 的 `run_project_unlocked` 是共用的异步执行实现，负责规则生成、LLM 扩展、基线选择、执行、持久化、diff 和门禁。CLI 的同步包装继续使用原来的持久化顺序；网页传入取消注册表，运行开始前落下 `running` 行。两者都在项目锁内调用。网页 Verify 使用同步路由在线程池中调用 `verify_project`，避免在事件循环中嵌套 `asyncio.run`。

`app/presenters.py` 返回结构化的校验报告、摘要、diff 展示信息与复验结果，CLI 从同一份信息生成文字，网页通过 `textContent` 展示。`app/trace_diff.py` 按工具名、参数和重复次数给出新增调用位置，网页与独立 HTML 报告只渲染这些位置。`app/drafts.py` 生成并回读 YAML，不写文件。`app/exporters.py` 生成 JUnit/JSON，HTML 仍复用 `app/report.py`。`app/web_presenters.py` 只负责网页传输时隐藏路径与敏感值，不参与判定。

| 新增或扩展接口 | 输入与输出 |
|---|---|
| `POST /api/project/validate` | `{}`；返回 `ok/errors/warnings/lines`、项目/门禁/工具/约束/probe 与各规则用例数 |
| `POST /api/project/runs` | `{label?, baseline?, set_baseline?, llm_expand?}`；返回 `{run, diff, gate, summary, warnings}` |
| `GET /api/runs/{run_id}/triage` | 返回与 `triage --json` 相同的分诊结构 |
| `POST /api/project/verify` | `{pre_run_id?, suggestion?}`，互斥；返回结论、计数、引用文字及条目 |
| `GET /api/runs/{run_id}/export?format=junit` | 下载 JUnit；`format=json` 下载 JSON |
| `GET /api/runs/{run_id}/report.html` | 下载 HTML；原 `/report` 的 JSON 保持不变 |
| `POST /api/project/draft` | `{text}`，最多 20000 字符；返回 `{yaml, compiler, warnings}` |

所有新接口位于 `protected`。新增 POST 都套用 `_project_run_guard`，并用 `_project_operation` 非阻塞取得同一项目锁；重复请求返回 409。请求体多余字段返回 422。校验或 Agent 导入错误保留字段信息，但不会回显路径与凭据。网页下载先经带 token 的 `api()` 获取 Blob，再创建临时下载链接。Windows 下载采用本机换行，和 CLI `--out` 文件逐字节一致。

`GET /api/diff` 保留原有 diff 字段，附加 `views` 展示信息；运行摘要也附带同样的 `entries`。这让前端不需要自行判断哪些调用是新增。旧 demo API 额外返回 `diff_views`，旧按钮的 id 和执行行为不变。

## 只读设置与关于

界面偏好由 `app/static/preferences.js` 和 `preferences.css` 实现。脚本在页面绘制前设置主题，在 DOM 就绪后初始化语言。`specagent_ui_language` 和 `specagent_ui_theme` 只写当前标签页的 sessionStorage，不增加接口、用户配置、数据库列或 CLI 参数。非法值恢复原始文案与深色，存储不可用时仍可切换并播报重载会复原。系统主题通过 matchMedia 变化更新，显式选择不受系统覆盖。

翻译使用可审核的完整文案目录和固定句式，保留句式中的名称与 ID。WeakMap 保存文本节点和属性的原文，切换不替换控件、不清输入、不丢焦点。MutationObserver 将动态文案放到下一帧处理。规格、轨迹、测试结果、模型回复、审批摘要和 CLI 输出排除翻译，名称与标签通过 data-verbatim 标记；动态展示的已知身份类型可翻译，实际操作者名称保留。可访问名称、标题和 placeholder 单独翻译。

主内容使用 main 与跳转链接，状态有 role/status 与 aria-live，表格列使用 scope/col。pre 可通过 Tab 聚焦滚动，设置窗口的焦点循环包含当前可聚焦文本。语义颜色用同一套 CSS 变量，浅色主题覆盖原有固定深色背景，窄屏指标改为两列。浏览器 CI 依次运行原 dashboard 和新 preferences harness。

`GET /api/settings` 位于既有 `protected` 路由，使用原 API 认证，不新增写接口。`app/settings_view.py` 只投影版本、数据库类型、认证模式、环境配置的存在状态和解析后的项目配置。它不调用 `Project.load`、适配器解析或 LLM 客户端，不读取规格、不导入被测模块、不建立网络连接、不写数据库或配置。目标状态分为 missing、invalid、unreadable 和 loaded，loaded 仅表示项目配置已解析，适配器 verified 固定为 false。

Agent 与草稿模型沿用 `resolve_model()`，按 `SPECAGENT_AGENT_MODEL`、`OPENAI_MODEL`、默认模型选择。行为编译、扩展与语义裁决沿用 `os.getenv('OPENAI_MODEL', DEFAULT_MODEL)`，保留显式空字符串。OpenAI 适配器仅显示非空 `adapter.model` 的静态覆盖，其他模型由目标决定。key 状态分为 missing、blank 和 configured，SDK 检测只检查包是否可找到。connection 固定为 not_checked，刷新不改变既有会话模型。

响应先使用网页脱敏和凭据模式脱敏，再限长。项目、模块入口和模型最多 256 字，演示 variant 最多 128 字，错误最多 20 条且每条最多 1000 字，均有截断标记。HTTP 仅返回有效环境变量名称和是否设置，不返回其值。服务 URL、服务器路径、数据库 DSN、Agent 指令和 allowed_hosts 不展示。原生 dialog 通过文本节点显示内容，关闭清空快照，刷新或重新打开后忽略旧响应；401 清空内容并说明原 token 入口。

## 基线操作历史

`Store.set_baseline` 保持原签名，CLI、网页运行与 Agent 人工审批仍共用它。`baseline_audit` 只追加成功的设置、替换、重新设定和取消事件，保存 UTC 时间、原/新运行 ID、入口和身份类型。项目行先取得写锁，再读取当前状态和历史版本；基线标记与历史在同一事务提交，失败不会生成事件。已有数据库自动建新表，旧基线不补造未知操作。运行回收站不删除审计引用。

`app/baseline_audit.py` 用 ContextVar 将服务端身份限定在当前操作，完成后恢复。CLI 主入口记录本机进程用户名，网页只记录本机浏览器或共享令牌身份，Agent 保留其操作上下文并标为人工审批入口。网页同步与流式审批使用相同包装。客户端不能指定身份，记录不保存 token；网页内容经统一脱敏。

`GET /api/baselines/history?project_id=…&offset=0&limit=20` 返回当前基线、最新事件 revision、旧基线未知历史标记、总数与分页事件。`POST /api/baselines/clear?project_id=…` 只接受 `confirm_run_id` 和必传可空 `expected_event_id`，使用原认证和新增 JSON/环回 Host 校验。事务中重新核对 ID 和 revision，任何变化返回 409，包括先换走再换回同一 ID。取消不删除数据，默认 diff 沿用无基线 404，趋势明确标为未评估。旧设置接口和 CLI 输出保持兼容，取消没有新增 CLI 或 Agent 工具。

## Agent 流与历史

`app/agent_stream.py` 提供受 Agent API 同一认证与启用检查保护的 `POST /api/agent/sessions/{id}/messages/stream` 和 `approve/stream`。新增流和原有同步接口调用相同的 `_message_locked` / `_approve_locked`，风险门禁与结果结构不变。流请求先预留会话锁，忙碌、待批准和重复审批在 HTTP 头发出前返回错误。

一个工作线程运行共用 Agent，`Transcript.listener` 将已脱敏事件传给有限队列，SSE 的 `timeline` 逐步展示真实工具事件。LLM 会话启用 `responses.create(stream=True)`，`response.output_text.delta` 提供 `text_delta`；只在 `response.completed` 后读取完整输出项并按原协议补工具结果。CLI 默认不启用流，文字与退出码不变。浏览器通过带 Bearer 的 `api()` 和 `ReadableStream` 读取 SSE，不把 token 放在 URL 中。凭据可能横跨增量片段，因此先保留未完成词和敏感值长度的尾部，再发布脱敏预览。最终结果仍由共用实现完整输出。

`GET /api/agent/logs?limit=30&before={id}` 列出服务端项目日志；`GET /api/agent/logs/{id}?after=0&limit=200` 分页读取事件。日志 ID 有严格正则，日志目录与文件不能通过符号链接逃逸；单文件读取上限为 2 MB。JSONL 中保存网页会话元数据和每次返回的最终结构。进程重启后可回看历史并明确继续未过期的会话，服务器从数据库恢复聊天和待批准动作，不重播已处理调用。中途退出的操作结果不确定时只读，页面提示查看结果并新建会话。流断开只解除展示订阅，已批准动作继续完成一次并保存日志；再次审批返回 409。

Python 适配器使用独立进程执行，每次重新导入源码。Windows Job Object 或 POSIX 进程组负责超时、取消和完成后的进程树清理。私有 JSON 会话检查点由 app/agent_state.py 管理；app/process_lock.py 提供 OS 锁。部署需要共享数据库和可写项目目录，不覆盖不同主机或不共享目录的容器。迁移细节见 [运行可靠性](runtime-resilience.md)。
