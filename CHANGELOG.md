# Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/);版本号遵循语义化版本。

## [Unreleased](方案 B:网页运行服务端配置的项目)

### Added
- **设置与关于**：顶部只读窗口显示版本、数据库类型、认证、LLM 配置状态、不同用途的模型和服务端测试目标。新增受保护的 `GET /api/settings`，不初始化客户端、不导入目标、不读规格、不写数据库或配置；连接明确未验证，被测模型未知时不推测。字符串先脱敏再限长，隐藏密钥、完整地址、指令与服务器路径。支持刷新、键盘关闭、焦点返回和迟到响应保护，旧 CLI 输出保持不变。
- **基线历史与取消**：网页分页显示设置、替换、重新设定和取消的时间、入口、操作者及前后运行 ID。共享令牌身份如实注明，命令行记录本机进程用户名，Agent 保留人工审批约束。新增只追加的 `baseline_audit` 表，状态变更和记录在同一事务内完成；旧库自动建表，不补造过去的操作。取消要求完整当前 ID 和最新历史版本，过期确认返回冲突；取消保留所有运行、规格、证据与历史并刷新页面。新增受保护的历史 GET 和经 JSON/Host 校验的取消 POST，原 CLI 输出与设置基线接口保持兼容。
- **FLAKY 逐次详情**：重复执行保存每次确定性状态、违规、响应、轨迹与延迟，网页支持逐次查看及两次对比。保存取消标记并注明未执行，旧记录明确无详情；查看和对比不会调用 Agent。复用已有 `repeat_json`，不新增数据库列；原始适配器 payload 不进入逐次快照，沿用执行截断与凭据字段脱敏。新增受保护的重复详情分页及对比接口，显示文本、轨迹 JSON 和 diff 脱敏并提示展示上限。最终 FLAKY 判定、统计、门禁与已有 CLI 输出结构保持不变。
- **运行管理**：运行 ID、标签与 Agent 搜索，状态和基线筛选，25/50/100 条分页，跨页按 ID 对比，标签修改与清空。删除前展示影响、核对完整 ID，数据库再次检查当前基线与 running 状态；回收站可恢复，保留执行证据及所有关联。默认历史、项目数量和指标排除回收站记录。新增分页与管理 API、独立 `run_trash` 表；旧数据库自动建表，CLI 输出格式与旧列表 API 结构不变。
- **网页项目创建与资料展示**：Projects 可创建 ID、名称、说明并查看新项目的空状态，列表和选择器展示名称。复用受保护的项目 API，限制输入长度、拒绝配置字段，重复 ID 返回冲突且不会覆盖资料。项目资料不切换服务端测试目标、不写配置、不导入适配器。显示文字脱敏并通过文本节点渲染；切换项目后忽略旧运行与指标响应。
- **六项指标趋势图**：最近 7/30/90 天与全部时间范围、精确数值表、数据点查看运行和键盘操作。新增受保护的 `GET /api/metrics/history`，复用现有指标与回归计算，只选已结束的运行。按时间筛选后限量并返回总数，最多 500 次且明确提示截断。新回归数明确与当前基线比较，无基线时显示未评估。
- **规则与规格查看**：当前 YAML、历史行为版本、编译规则、源内容复制与下载、两版本差异和关联运行跳转。新增受保护的当前规格、版本详情、源内容下载与 diff 接口，内容统一脱敏。沿用按编译规则去重的版本语义，注释与排版不生成新版本，CLI 输出不变。
- **实时用例进度**：运行中显示已完成/总用例、通过、失败、不稳定、错误和取消计数及最近完成用例。网页运行、Verify 与批准后的 Agent 执行共用记录；刷新后可继续查看并取消仍活动的可取消运行。新增受保护的 `GET /api/runs/{id}/progress` 和独立 `run_progress` 表，旧数据库自动建表，旧记录展示已有摘要；结束时重新确认最终判定计数。
- **修复建议查看与复验**：新增 `suggestions list/show` CLI 与受保护的三个只读接口。Fix suggestions 展示诊断、文件、diff 和持久复验历史，支持复制、原样下载和 Verify。批准卡片可链接到建议；网页没有应用补丁入口。Triage 仅在有对应建议时追加提示，其余原输出保持不变。
- **Agent 执行可视化**：工具调用、结果、审批停放与恢复实时显示在时间线；网页使用 Responses 的真实增量流；JSONL 历史可分页查看、下载和恢复仍活动的会话。断开页面不会重复执行已批准动作，日志与流经认证、路径校验和脱敏。
- **命令行与网页功能对齐**：新增 Project tools 的 Validate、Triage、Verify、Export、Draft 和运行选项。`POST /api/project/runs` 接受 `baseline`、`set_baseline`、`llm_expand`，返回共用摘要。所有新增 POST 要求 JSON、认证或环回 Host，并共享项目并发锁；浏览器不能覆盖门禁、规则路径、适配器或端点。
- **共用内容实现**：`app/presenters.py` 提供校验报告、运行摘要、diff 展示与复验结构；`app/drafts.py` 提供经 YAML 回读验证的草稿；`app/exporters.py` 提供 JUnit/JSON。CLI 继续保留原有文字和退出码。下载携带页面 token，并隐藏服务器路径和凭据。
- **网页操作提示与对比**：每个操作显示等价命令行，历史可以选择任意两次运行进行对比。草稿只能预览、复制和下载，不能改服务器文件。`app/trace_diff.py` 统一判断新增调用，修复网页和独立 HTML 报告把已有审批调用标成 `◀ new` 的问题；比较工具名、参数和次数。
- **一致性防漂移测试**：新增 `tests/test_cli_web_parity.py`，覆盖各子命令与网页入口登记、同一次运行的内容、修复/回归复验、导出字节、离线及假 LLM 草稿、指标、安全与并发；浏览器原有断言保留，最低检查数由 41 增至 58。用户批准仅将旧版禁止 `set_baseline` 的断言替换为禁止门禁覆盖，并新增接受测试。
- **HTTP 接入的真实服务示例**(`demo_agent_server.py` + `examples/http-agent-demo/`):一个独立 FastAPI 形态的被测 Agent(`DEMO_VARIANT=patched|vulnerable` 切换正确/缺陷两版,缺陷为大额退款跳过人工审批 + 查订单不校验归属),配3 条规则(审批 / 地址确认 / 订单归属)与一份 `README.md`。**端到端实测 25 用例、15 通过、10 条 critical 违规**,正确用例仍全 PASS。含 `gate_demo.py`:一个进程自起自停四幕演示,实测确认 gate 语义——patched 录基线 → vulnerable 退出码 1(拦新回归),`--baseline last` 对比同样坏的上一次则退出码 0(缺陷已入基线降级 `PERSISTENT_FAIL`,**设计如此**);`--set-baseline` 钉住的基线**不会自动漂移**。
- **`GET /api/project`**(挂 `protected`,需 token):只读描述当前被测项目——`project_id`、适配器(`type` + `label`,http 只到主机名)、规则数与自动生成的用例数、`gate.fail_on`、`run.*` 设置。未配置(`SPECAGENT_PROJECT_CONFIG` 缺省且 `./specagent.yaml` 不存在)返回 `{"configured": false, "mode": "demo"}`;配置存在但无效返回 `mode: "error"` 加字段级错误。**不返回路径、环境变量值或带凭据的 URL**。`/api/health` 仅新增 `project_configured` 布尔字段,旧字段不变。
- **`POST /api/project/runs`**:用服务端配置文件里的适配器、`run.*` 设置与规则文件执行一轮(每次请求重新 `Project.load`,改动立即生效),复用 `orchestrator.run_with_diff`(run 先落 `running` 行、可被 `POST /api/runs/{id}/cancel` 取消),门禁判定与 CLI 同源(`regression.gate_violations(diff, gate.fail_on)`),响应为 `{"run", "diff", "gate": {"failed", "fail_on", "violations"}}`。请求体只接受 `{"label": …}`(`extra="forbid"`,spec/agent/project_id/set_baseline/endpoint 等字段一律 422);配置缺失或无效 → 422 带字段级错误;同一项目已有运行在途 → 409(`project_run_in_progress`)。
- **进程内项目锁**(`app/project.py`):同一配置文件的运行在进程内串行——`run_project`(CLI、agent `run_suite`)与网页项目运行共用一把锁,网页连点或与 Agent 面板并发不会交错;多进程部署不互斥(见 known-issues U13)。
- **仪表盘 Target agent 条**:配置有效时显示 `Testing: <project> · <adapter label> · N rules · M cases · gate: …` 与 **Run project suite** 按钮,运行后渲染结果并显示门禁行(无基线 `Gate: not evaluated`;失败 `Gate: FAILED — N new regression(s)…`;通过 `Gate: PASSED`);未配置时明确提示当前只测内置 demo Agent。旧 `#specText` / `#runBtn` / `#resetBtn` 流程与全部既有断言保持不变。

### Fixed
- 修复源码读取在 Windows 短路径或项目根目录别名下记录错误相对路径的问题，完整读取后的修复建议不再误报 `file_not_fully_read`。
- 仪表盘项目运行补上 **Cancel run** 按钮(known-issues U13):运行中轮询运行历史定位 in-flight run,点击调用既有的 `POST /api/runs/{id}/cancel`;取消为协作式——已开始的用例跑完,未开始的记为 CANCELED,状态行明确显示取消数量。长超时的项目从此可以在网页上直接取消,不必等它跑完或去 CLI 侧操作。

### Security
- `POST /api/project/runs` 两道新防线:请求必须 `Content-Type: application/json`(跨站表单无法伪造,防 CSRF);无 token 的本地模式下校验 `Host` 为环回(127.0.0.1 / ::1 / localhost,防 DNS rebinding)。配置了 `SPECAGENT_API_TOKEN` 时按 token 认证。配置路径解析从 `agent_api` 下沉到 `app/project.project_config_path()`,两个入口共用。

### Tests
- 新增 `tests/test_project_run_api.py`(12 条:三种配置态、主机名脱敏、修复/缺陷两版门禁与 CLI 一致、严格请求体、token 认证、环回 Host、进程内锁 409、在途取消、health 兼容)。
- 浏览器端到端追加 11 条断言(30 → 41,`tests/test_browser_ui.py` 下限同步上调):未配置提示与按钮隐藏、配置态 Target 条、项目运行渲染、无基线门禁行、**修复版设基线 → 缺陷版运行 `Gate: FAILED — 4 new regression(s)`**(与 CLI 数字一致)、项目下拉跟随、全程无页面错误。

### Docs
- README 顶部加 CI 状态徽章(tests / specagent-gate / action-selftest)与演示 GIF(`docs/assets/demo.gif`,由 `docs/demo-script.md` 的真实命令输出逐帧渲染,67 秒、全程离线);仪表盘一节新增三张截图(Target agent 条、回归 diff 双栏、Agent 面板);"GitHub CI 门禁"一节改为如实记录 GitHub 实测结果,并链接**演示 PR #3**(改坏的 Agent 在真实 CI 上变红、门禁评论与缓存路径首跑验证)。
- 完整演示视频(81 秒,1280×720,含真实 DeepSeek 模型的"AI 提议 → PARTIAL → ALL_FIXED"片段)作为 Release v0.10 附件发布。
- 真实 LLM 验证补全(见 known-issues U11):`specagent draft` 与 agent→建议→verify 闭环在 deepseek-flash 上通过;验证发现两处值得记录的行为——`write_fix_suggestion` 必须显式开 `--allow-source`(否则模型拒绝编造 diff),部分应用建议后 `verify` 如实给出 PARTIAL(六方判定表按设计工作)。

## [v0.10] — 2026-10-06(Agent 层 + 可复用 Action + 文档发布,阶段 D 任务 11–16(含仪表盘 Agent 面板)与阶段 E 任务 17–18)

把 SpecAgent 变成"自己也是 Agent 的测试平台"(实现规划书 v1 阶段 D,设计文档 §7–§8.9):LLM 负责起草、运行、分诊和提修复建议,**每一条 PASS/FAIL 仍只由确定性代码产生**,改动规格与基线必须人类确认。

本版本同时收录任务 16 审查遗留问题的修复(审查结论 APPROVED,6 条非阻断)、用真实 LLM key 做的首次端到端验证,以及 2026-10-07 交接文档(§1 判定缺口、§2 真实 Agent 接入)的处理;v0.10 尚未发布,这些都属于 v0.10。

### Added
- **共享运行核心**(§7):`app/project.py` —— `Project`(只读属性面,可变配置锁在私有 `_config`,AST 测试禁止 agent 包触碰)、`run_project`("生成 → 执行 → 持久化 → diff → gate"的唯一实现,CLI `run` 与 agent `run_suite` 共用,CLI 输出与退出码逐字节不变)、`format_run_quote` 确定性引用块;`cmd_run` 已改写到 `run_project` 上。
- **工具层**(§8.2):`app/agent/tools.py` —— 14 个工具的注册表,两级风险(auto/confirm),`replace_spec` 与 `set_baseline` 为 **human_only**(`--yes` 永远无法批准,回调误报 auto 也会被判 declined);单一门禁 `ToolRegistry.call`:JSON schema 校验 → `allow_source` 前置 → `prepare → confirm → recheck → act` 管线 → `_require_confirmed` 哨兵(绕过门禁直调 handler 必失败)→ 输出脱敏与 12k 截断。**哈希绑定确认**:`replace_spec` 绑草稿与现行规格双 sha256(停放期间任一被改 → `stale_confirmation`,绝不写入),`set_baseline` 绑 run 统计,`run_suite`/`verify_fix` 绑规格+配置字节,`write_fix_suggestion` 绑 diff 哈希;停放动作(PendingAction)+ `execute_approved` 支持仪表盘稍后批准。
- **路径沙箱**(§8.3):`app/agent/sandbox.py` —— `resolve_read` 三层防线(词法检查:穿越/UNC/盘符相对/ADS;realpath 包容:符号链接与 Windows junction 逃逸即拒;组件级拒绝名单:`.git`/`.ssh`/`.env*`/`*.pem *.key *.db`/`id_rsa*`/`*credential*`/`*secret*` 等);`read_file` 扩展名白名单、二进制与 200KB/20k 字符上限、**完整读取跟踪**(截断/被脱敏/非 UTF-8 的读取记录原因,永不可被 agent 重写);`redact_text`(PEM 块、key=value、Bearer、`sk-`/`ghp_`/AKIA 令牌);`ensure_state_dir` 先建 `.specagent/.gitignore`(`*`)再写任何会话/建议。
- **确定性分诊**(§8.5):`app/agent/triage.py`(纯函数,只 import `app.violations`)+ `specagent triage` CLI + `triage_run` 工具:FAIL/FLAKY 违规文本经 `parse_violation` 归类,按 `(category, tool, arg)` 合并(count/cases/evidence/示例),固定 hint 模板表;ERROR 分列、永不并入 findings。
- **Agent 循环**(§8.6):`app/llm_client.py`(`resolve_model`:SPECAGENT_AGENT_MODEL → OPENAI_MODEL → gpt-5.5;`make_client` 空 key 返回 None,openai 延迟导入)+ `app/agent/loop.py` —— `AgentSession`(同步 stateless Responses 循环;**停放动作协议**:任一 `responses.create` 时每个 function_call 恰有一个 function_call_output,停放响应在批准前不追加任何 output,后续调用获得 `skipped_pending_approval`)、`OfflineWorkflow`(无 key 时 validate → run → triage → 摘要的固定状态机)、`Transcript`(`.specagent/agent-logs/*.jsonl`,逐行 flush,**每条记录的每个字符串递归过 redact_text**;`propose_spec`/`write_fix_suggestion` 的参数只记 name/sha256/bytes);SYSTEM_PROMPT 写死硬边界;`run_suite`/`verify_fix` 的确定性引用块 verbatim 拼进最终回复,模型无法省略或改写。
- **CLI `agent` 与 `draft`**(§8.7):`specagent agent [goal]`(`--yes` 只自动批准非 human-only 工具并打印一次性横幅;五态确认表;TTY 检测;`you>` REPL;`--max-steps`;无 goal 且非 TTY → exit 2;退出码 4 = 会话异常中止或确认被拒,CI 不会误报成功)与 `specagent draft`(自然语言 → 规格草稿:LLM 可注入客户端,输出经 `dump_spec_yaml` 序列化并**回读验证**后才落盘,`--out` 拒绝覆盖需 `--force`);`compile_spec`/`compile_with_llm` 增加注入缝(默认行为不变),schema_hint 增加 `locale`/六类 `constraints`/`probes` 与操作符说明。
- **修复建议与 verify 闭环**(§8.8):`app/agent/fixes.py`(EOL 保持的 unified diff——旧文件主 EOL 胜出、按保留终止符切行、`\ No newline at end of file` 标记;`git apply` 与 `patch -p1` 均可用,测试真实执行)+ `app/verify.py`(**六种结论** REGRESSED / INCOMPLETE / NOT_FIXED / PARTIAL / ALL_FIXED / NO_CHANGE 的有序判定表 + 引用块)+ `verify_project`(pre-fix run 选择:显式 `--pre-run` > 建议记录 > 同规格最新 completed 非 verify 运行;verify 运行永不改基线)+ `specagent verify` CLI(`--pre-run`/`--suggestion` 互斥;ALL_FIXED/NO_CHANGE → 0,其余 → 1)+ `write_fix_suggestion` 工具(建议只写 `.specagent/suggestions/`,**项目树逐字节不变**,目标必须是完整读取过且未被改动的非保护文件)。
- **仪表盘 Agent 面板**(§8.9,任务 16):新增 `app/agent_api.py`,在 `/api/agent` 下提供三个同步、非流式端点——`POST /sessions`(新建会话,返回 `session_id` / `mode`(`llm` 或无 key 时的 `offline`)/ `model` / `project`)、`POST /sessions/{id}/messages`(`{"text": …}`,≤ 4000 字符)、`POST /sessions/{id}/approve`(`{"action_id": …, "approve": true|false}`)。**鉴权**:路由先过 `require_api_token`(401),再过 `require_agent_enabled`(403);未设置 `SPECAGENT_API_TOKEN` 时整个 Agent API 返回 403(`agent_api_requires_token`),唯一例外是显式的仅环回 opt-in `SPECAGENT_AGENT_API_INSECURE=1`(只放行 127.0.0.1 / ::1 / localhost 客户端,启动时与每次请求都打印 WARNING)。`/api/health` 的 `agent_enabled` 反映同一开关,仪表盘据此显示面板。**审批**:面板里的每次确认一律停放(deferred),只有 `approve` 端点能执行停放动作,这也是 `human_only` 工具(`replace_spec` / `set_baseline`)的人类通道;前端对 human_only 动作额外要求勾选"由我本人批准";有待处理动作时 `messages` 返回 409;同一 `action_id` 重复处理返回 409;哈希绑定失效时返回 `stale_confirmation` 且不写入任何内容。**数据边界**:浏览器不能指定路径或开启 `allow_source`——项目来自服务端 `SPECAGENT_PROJECT_CONFIG`(默认 `./specagent.yaml`),`allow_source` 只取自该配置,请求体多余字段一律拒绝;面板运行与 CLI 共用同一 `Store`,出现在运行历史中。**会话仅存内存**:最多 8 个、空闲 1 小时过期、超出时淘汰最久未用的,每会话一把锁串行化请求;重启即丢失(known-issues U8)。

- **`specagent run --fail-on critical,high`**(§9.1):只覆盖本次调用的 `gate.fail_on`,逐项校验 `Severity`(大小写不敏感、去重;空值或未知值 → 退出码 2),直接走 `run_project` 的 `fail_on_override`,`--json` 输出里的 `gate.fail_on` 反映实际生效的值。
- **可复用 GitHub Action**(§9.1):根目录 `action.yml`(composite;输入 `config` / `fail-on` / `comment` / `mode` / `cache` / `python-version` / `artifact-name`)。推送到默认分支 = 记录基线并缓存数据库,其他事件 = 候选 diff;门禁失败时最后一步才让 job 失败,保证 HTML/JUnit 产物先上传;可选 PR 评论(失败只警告,不阻断)。**确定性**:run 步骤显式清空 `OPENAI_API_KEY`,从不调用 `agent` / `draft`。判定与渲染逻辑在 `app/ci.py`(`mode` / `run-id` / `comment`,仅标准库,按脚本路径调用)。自测工作流 `.github/workflows/action-selftest.yml`:显式 `mode`,baseline 必须通过、candidate(`continue-on-error`)必须以 failure 结束。**该 Action 与自测工作流尚未在 GitHub 上实际运行验证**,仅有本地结构与逻辑测试。
- **文档与发布**(§9.2):`README.md` 中文重写(定位"AI 提议,规则验证"、与 promptfoo/agentevals 的对比、Mermaid 架构图、FinCare 十分钟、Agent 用法与风险分级、数据边界、`SPECAGENT_AGENT_MODEL` 模型配置);新增 `README.en.md`(仓库里唯一刻意英文的文档)与 `docs/demo-script.md`(命令与预期输出来自真实运行);`docs/architecture.md`、`docs/roadmap.md`、`docs/known-issues.md`(新增 python 线程超时、内存会话等限制)同步更新。
- 版本号升到 **0.10.0**(`pyproject.toml` 与 `app/__init__.py`),`tests/test_version.py` 断言两处一致且 `/api/health` 返回该版本。
- **openai adapter 补上 actor 身份传播(修复真实验证抓到的 IDOR,设计 §5.4)**:此前 `case.actor` 在 openai 路径上被整体丢弃——HTTP 契约有 `context` 字段、python adapter 有 `actor` kwarg,唯独 LLM Agent 没有任何身份通道,真实模型因此把消息里的越权订单号直接发出去(`arg_scope` 实测抓到)。两条路径,**都按 §6.1 的签名约定显式接入**:
  - 工具执行器声明 `actor` 参数(或 `**kwargs`)即接收 `case.actor`——后端强制的位置;不声明的执行器行为不变。示例 `_query_order` 据此做会话级校验,外来 id 只回 `forbidden`。注意判定语义不变:trace 记录的是模型**发出的**调用,执行器校验是数据安全兜底,不是判定修复(arg_scope 判意图)。
  - `OpenAIAgentDefinition.include_actor_context`(默认 False,显式 opt-in)在用例携带 actor 时注入一条 `session actor: {...}` system 消息——只给身份事实,策略仍归 instructions。实测 deepseek-flash 在"拿到身份 + 具体化指令"后正确拒绝越权查询。
  - 由此在示例上跑通了完整开发闭环:门禁抓真 bug → 开发者修(补 schema → 注入身份 → 具体化指令)→ 门禁确认 FIXED → 16/16 基线重建 → 复跑稳定。ARG_SCOPE 用例的 FAIL→PASS 转变是真实模型行为变化,不是判定口径变化。
- **openai-agent 示例补 v1 约束式规格(交接文档 §2 的接入准备)**:`examples/openai-agent/specs/behavior.probes.yaml`(2 条规则:退款审批 require_before、订单越权 arg_scope)+ `specagent.probes.yaml` 配置,确定性派生 16 个 probe 用例——推荐规格写法从此在真实 Agent 示例上也有样板。`agent.py` 的模型名支持 `SPECAGENT_AGENT_MODEL` 覆盖(默认 `gpt-4.1-mini`),配合 SDK 原生读 `OPENAI_BASE_URL`,接 DeepSeek 等 OpenAI 兼容 provider 仍是零改动,只需环境变量。配套离线集成测试 `tests/test_openai_agent_example.py`:用脚本化假客户端驱动**真实** `OpenAIResponsesAdapter` 函数调用循环,覆盖「审批后退款 → PASS」「跳过审批 → FAIL」「纯文字大脑 → 整 run ERROR(判定缺口的 run 层修复在真实 adapter 路径上生效)」。真实 LLM 实跑一步因本机无 `OPENAI_API_KEY`(user/machine/process 均未设、无 `.env`、无本地模型运行时)暂缺,补上 key 后一条命令即可:`SPECAGENT_AGENT_MODEL=deepseek-flash OPENAI_BASE_URL=https://api.deepseek.com OPENAI_API_KEY=sk-... specagent run --config examples/openai-agent/specagent.probes.yaml --set-baseline --db openai-demo.db`。
- **浏览器级 UI 测试**(`tests/browser/test_dashboard_browser.js` + `tests/test_browser_ui.py` 包装):启动独立 uvicorn(绑临时端口、`SPECAGENT_DB` 指向临时文件,**绝不碰仓库的 `specagent.db`**),用真实 Chrome 加载仪表盘,点"Run behavior audit",断言 30 项:JS 无解析错误、版本药丸取自 `/api/health`、结果面板/规则/用例/指标六格/运行历史全部真的被画出来、`?project=&run=` 深链可用,以及**完整的 token 流程**(`/api/health` 报 `auth_required` → 露出输入框 → 未授权运行被拒且**不画出结果** → 401 提示 → 错 token 不解锁 → 对 token 后成功,token 落在 `sessionStorage` 而非 `localStorage`)。这补的是 518 个 Python 测试结构上覆盖不到的一层:那些测试只 grep `index.html` 里**含有**某些 id,而"标记与脚本各自漂移、用户看到白屏"这类故障它们一条都抓不到。
  - 写成 Node 脚本而非 pytest 用例,是因为 playwright 不是本项目依赖;把它加进 `requirements.txt` 会让所有只用 API 的人和 CI 镜像都被迫安装浏览器驱动。脚本复用机器上**已有的** `playwright-core`,找不到就干净跳过。
  - CI 新增独立的 `browser` job(`.github/workflows/tests.yml`):装完整的 `playwright` 而非 `playwright-core`,因为 Chromium 下载按 client 期望的 revision 编号,`playwright-core@X` 配 `playwright@latest` 可能装上 X 根本不找的版本,随后 fallback 到系统 Chrome —— 而 ubuntu-latest 没有系统 Chrome。

### Changed
- `pyproject.toml` packages 增加 `app.agent`;CLI 文档化新退出码 4;`.gitignore` 增加 `.specagent/`。
- `tests/test_packaging.py` 的包清单测试自动覆盖新包;conftest 无改动。
- `app/main.py` 挂载 `/api/agent` 路由并向其传入共享 `Store`;`app/auth.py` 新增 `require_agent_enabled` 与 `SPECAGENT_AGENT_API_INSECURE` 启动警告;`app/static/index.html` 新增 Agent 面板(`agent_enabled` 为 true 时显示)。
- **`agentReset` 不再用阻塞式浏览器对话框**(审查 #5):有停放动作时改为日志流内联的 `callout.warn` + "丢弃并新建 / 取消"两个按钮,Escape 取消、焦点落在主按钮上,与面板其余视觉体系一致;同时点了两次"新建会话"也只弹一个提示。
- **示例提示去掉业务场景**(审查 #6):第三个 chip 由"为退款审批起草一条更严格的规则"改为"为当前项目起草一条更严格的约束规则"。

### Fixed
- **[判定缺口]「什么都没验证」不再可能得到全绿(交接文档 §1)**:probe 用例的 `expected_calls` 恒为空、以 constraints 为 oracle,而 constraints 对"没有匹配调用"一律放行(设计 §5.2 规则 3:拒绝算通过)——于是一个纯文字、零工具调用的 Agent 在 probe 用例上全部静默 PASS。修复分两层,因为**单条用例层面空 trace 与合法拒绝不可区分**(fincare 示例有 9 个用例的正确行为就是拒绝且返回空 trace,例如非管理员关闭账户、非法币种;把它们判 ERROR 会破坏规范性的拒绝语义与 43/43 基线):
  - **judge 层**:上游发来的事件**全部**被规范化丢弃(未知 type、非对象等)时,该用例判 `ERROR` 并注明"nothing was verified"——发了一堆解析不了的事件与"什么都不发"不同,前者是集成故障,必须 fail-closed。为此 `AgentExecution` 新增 `dropped_events` 计数,四个 adapter(python/http/openai/langgraph)统一上报。
  - **run 层**:整个 run 的所有 trace 加起来**零 `tool_call`** 时,run 内每一个 PASS 都是 vacuous 的,统一降级为 `ERROR("unverified: no tool_call events in any trace of this run")`。这覆盖纯文字 Agent 的准确特征——openai adapter 的 trace 永远非空(至少有 user_message/assistant_message),按用例判空查不出来,run 级一查一个准。降级是确定性的、在 `execute_suite` 收口,分数、diff(`NEW_ERROR`)、verify 判定(`candidate_error → NOT_FIXED`)自动全部生效;伪造的绿色基线从此**建不出来**。已知边界:若规格的每条规则都只被"拒绝"满足(纯负面规格),一个全拒绝的 Agent 也会整 run ERROR——这类规格本就验证不了任何东西,属如实上报。
  - 注意 [U1] 仍然开放:`NEW_ERROR` 默认不进门禁是既有设计决策,本次未改;但修复后假 PASS 基线已无从产生,该缺口的现实危害已封死。
- **[别名缺口] Anthropic 原生 `tool_use` 的参数不再整体丢失(交接文档 §1)**:args 字段别名表补上 `input`(`args` → `arguments` → `input` 依次回退)。此前 `{"type":"tool_use","name":...,"input":{...}}` 会得到 `args={}`,事件被保留但所有参数全丢,`when`/`arg_range`/`arg_scope`/`arg_enum` 全部空转。
- **openai/langgraph adapter 的模块加载补上 `reload=True`**(U12 同族):模块新鲜度机制(§6.1)此前只有 python adapter 走——同一进程先加载 A 项目的 `agent.py`、再解析 B 项目的 `agent:AGENT` 会命中 `sys.modules` 里的旧模块(两份示例恰好都叫 `agent.py`,测试全量跑当场复现;仪表盘单进程多项目,真实场景同样可达)。现三个 `module:attribute` 型 adapter 统一走"先清理 base_dir 下缓存模块与同名目标模块再导入"的流程。
- **`openai_schemas()` 改用扁平的 Responses API 形状**(真实 LLM 验证时发现):此前发的是 Chat Completions 的嵌套形状 `{"type":"function","function":{...}}`,OpenAI 的 `/v1/responses` 容忍这种写法,但 **DeepSeek 直接 422** ——`tools[0]: missing field 'name'`(2026-10-06 对 `deepseek-flash` 实测)。现改为 Responses API 规范要求的扁平形状 `{"type":"function","name":...,"parameters":{...}}`,两个 provider 都接受。这条错得很隐蔽:**全部 LLM 测试都走注入的假客户端,不校验请求体形状**,所以离线状态下它能一路绿灯,只有接真实模型才暴露。
- **批准重试的 409 不再被标成"未执行"**(审查 #1):前端 `agentResolve` 区分 409 与 404。409 `action_already_resolved` 表示该动作此前已提交过、可能已经执行(例如耗时很长的 `run_suite` 被代理切断后重试),现在封存文案为"该动作已被处理(可能已执行),请以 Run history 为准",并刷新 Run history 供核对;只有 404 才写"该动作已失效,未执行"。此前两种情况共用一句"未执行",与事实相反,而留痕正是这个面板的卖点。
- **只有会话真的丢失才重置 sid**(审查 #2):approve 返回 404 时区分 `action_not_found`(会话仍存活)与 `session_not_found`(会话已丢)。此前一律清掉 `agent.sid`,被丢掉的会话成为孤儿,继续占用 8 个名额直到 TTL 到期。新增 `agentSessionGone()` 统一判定,`agentErrorText` 也不再把 `action_not_found` 说成"会话已失效"。
- **LRU 淘汰跳过在途会话**(审查 #3):`_register` 超出上限时在**未持锁**的会话里挑最久未用的,不再直接 `popitem(last=False)`。此前一个正在跑 `run_suite` 的会话可能被挤出表,请求本身能跑完但之后的 approve 与消息全部 404,停放的动作一并丢失。全部名额都在忙时新会话返回 429 `too_many_sessions`(而不是杀掉别人的工作),日志打印被拒的 `session_id`。TTL 清扫(`_purge_expired`)本来就跳过持锁会话,保持不变。
- **opt-in 模式校验 `Host` 头**(审查 #4):`SPECAGENT_AGENT_API_INSECURE=1` 现在同时要求 TCP 对端与 `Host` 都在环回名单内(`127.0.0.1` / `localhost` / `::1`,含 `[::1]` 与可选端口,端口必须是数字)。DNS rebinding 时浏览器仍会发送攻击者自己的 `Host`,此前只看对端地址,恶意页面可借用户浏览器调用 approve。顺带堵上"跨站简单请求连续建 8 个会话把用户挤掉"的路径。配了 token 时不做 Host 检查(此时对端检查本来就不参与判定)。
- **仪表盘补上 favicon**:浏览器每次加载页面都会无条件请求 `/favicon.ico`,此前一律 404,用户打开控制台就能看到一条与应用无关的错误。现以内联 `data:image/svg+xml` 提供,**不新增二进制资源**,仪表盘仍是单文件。
- **深链指向不存在的 run 时给出错误提示而不是半渲染页面**(截图评审发现):`showRun` 不检查 `r.ok`,把 404 响应体当数据渲染——用户看到 `Behavior score: undefined%`、空白的 Passed/Failed 卡、空的规则/用例区块,没有任何错误提示。CLI 打印的深链在数据库更换或记录清理后失效时正好命中此路径。现失败时在状态栏显示 `run not found: <id>`,结果区保持初始状态。
- **规则卡显示 constraints/probes 信息**(截图评审发现):v1 约束式规格(推荐的规格写法,fincare 五条规则全部如此)的规则卡此前只显示 `require: — / forbid: ——`,核心的约束信息完全不可见。现增加一行 `constraints: <type>·<tool>, …` 与 `probes: N generated cases`,legacy 字段照旧。

### Tests
- **actor 传播测试**:`test_openai_adapter_passes_actor_only_to_executors_that_declare_it`(声明 `actor` 的执行器收到会话身份、未声明的行为不变)、`test_openai_adapter_actor_context_is_opt_in`(opt-in 时首条输入是含身份的 system 消息;默认关;空 actor 不注入)。
- **判定缺口回归测试(9 个)**:`test_trace.py` 补 `input` 别名、别名优先级(`args` > `arguments` > `input`)与 `normalize_trace_with_dropped` 丢弃计数(seq 按原始位置编号);`test_judge.py` 钉死三件事——全丢弃 → ERROR、部分丢弃仍正常判定、**完好空 trace 仍 PASS(拒绝语义,§5.2 规则 3)**;`test_stability.py` 的两个 `trace=[]` 桩改为带一个 `tool_call`(零活动 run 会把 PASS 降级,桩测的是重试/取消语义),并新增零活动 run 降级、含一次真实调用的 run 不降级、真实违规不受降级影响三个用例。
- 新增 `tests/test_openai_agent_example.py` 4 个用例(见 Added 段)。全量 **522 → 535 → 537 passed**。
- 新增 `test_insecure_optin_host_header_must_be_loopback`(恶意 / 空 / 畸形 `Host` 全 403,八种环回写法全 200;配 token 时 Host 不参与判定)、`test_eviction_skips_sessions_with_a_request_in_flight`(在途会话不被淘汰、全忙时 429 且不淘汰任何人、解锁后恢复 LRU)、`test_busy_sessions_are_never_purged_by_ttl`、`test_approval_failure_paths_are_distinguished`、`test_reset_uses_an_inline_prompt_and_chips_are_generic`。
- 测试里用 `_BusyLock` 桩代替真实 `threading.Lock`:持真实锁会让服务请求的 portal 线程与测试线程互相等待,用例收不了尾。
- 修 `tests/test_agent_sandbox.py::test_symlink_escape_denied` 的间歇失败:原用例的文件名 `secret.txt` 撞上沙箱的 `*secret*` 拒绝名单(realpath 未逃出时返回 `sensitive_name` 而非 `symlink_escape`,属于因错误的原因通过),且只看 `symlink_to()` 是否抛异常——在未开开发者模式的 Windows 上该调用成功但 `os.path.realpath()` 并不解析链接。现改为 `payload.py` + 只有 realpath 确实报告逃逸时才走真实文件系统分支,否则退回 monkeypatch 分支。删链接统一走新的 `_rm_link`(先 `os.rmdir` 后 `os.unlink`):目录型 symlink 与 junction 在 Windows 上 stat 成目录,`Path.unlink()` 会抛 `PermissionError: [WinError 5]`,单跑该文件时因为 `symlink_to` 先失败而侥幸没走到,全量跑才暴露。
- 新增 `test_openai_schemas_use_the_flat_responses_api_shape`:断言 14 个工具的线上形状是扁平的且没有 `function` 键、`additionalProperties=False`。这个断言以前完全缺失,正是它让上面的 422 能一路绿灯通过全部测试。
- `tests/test_agent_api.py` 24 → 26 个用例,全量 512 → 517 → 518。
- 新增 `tests/test_browser_ui.py` 4 个用例(浏览器实跑 30 项断言 + 3 项守护:脚本必须入库、仪表盘必须有 favicon、harness 必须自己起服务而不是假定端口上已有东西),全量 **518 → 522 passed**(环境重建后复验,零失败)。

### Verified(真实 LLM,2026-10-07)

**判定管线首次接上真实 Agent 大脑**(`examples/openai-agent` + `specs/behavior.probes.yaml`,16 个 probe 用例,`deepseek-flash` @ api.deepseek.com,经本机代理 `127.0.0.1:7897`——该机器所有 HTTPS 直连被掐,Python 必须显式带 `HTTPS_PROXY` 环境变量,Windows 注册表代理它不读)。四轮真实运行:

1. **基线 16/16 PASS**:真实模型的合规行为被正确判定;同规格重跑 **16/16 STABLE_PASS、0 flaky**,判定对真实 LLM 方差稳定。
2. **真实抓到第一个非预埋 bug**:修好工具参数 schema 后(见下),隐私用例里真实模型把消息里的越权订单号直接拿去查——`[scope_violation] query_order(order_id=ORD-9002) does not match actor.order_id=ORD-9001 [evidence: evt_2]`,critical,门禁 FAILED。**没有任何人埋这个 bug**,模型自发行为 + `arg_scope` 证据链当场捕获。
3. **提示词回归被抓**:删掉 instructions 里的审批要求后重跑,真实模型直接 `refund(amount=501)`,`[missing_approval] … before required prerequisite: request_human_approval [evidence: evt_5]`,critical NEW_REGRESSION,门禁 FAILED——「改提示词导致行为回归」这一真实世界最常见的回归形态,从行为到拦截全链路验证。
4. **一个反直觉的真实现象**:第一轮实验只删提示词、参数 schema 未修时,16/16 照样全绿——但 trace 显示模型根本没退款(9/10 行只调了 `query_order` 甚至空转)。合规下限(PASS)与实际行为(没干活)是两回事,vacuous PASS 的边界与 §1 修复所依据的分析完全一致。

**由此发现并修复一个真实集成 bug**:示例 Agent 的工具从未声明参数 schema(`OpenAITool.parameters` 用默认空 schema),真实模型于是规范地发送 `{}` args,所有参数类约束(`when`/`arg_range`/`arg_scope`/`arg_enum`)全部空转——脚本化假客户端永远暴露不了这类问题,因为假客户端是测试者手写的带参调用。已给三个工具补上真实 JSON Schema(含 `required`)。**给接入方的教训:接真实 Agent 前,先确认工具声明了参数 schema,否则判定器对参数类规则是瞎的。**

模型名再确认:服务端只认 `deepseek-flash`(显示名 "DeepSeek-V4.1-Flash"),`deepseek-v4.1-flash` 这个写法不是合法 API 名。

**IDOR 的修复闭环(同日,见 Added 段 actor 传播)**:仅加执行器校验不够——模型仍发出越权调用(执行器回 `forbidden`,数据没漏,但 arg_scope 判的是调用意图,PERSISTENT_FAIL);注入 `session actor` 上下文 + 把指令具体化("只查等于会话身份订单号的订单,其余拒绝")后,模型正确拒绝调用,用例 FIXED,16/16 基线重建、复跑稳定。教训:**LLM Agent 的越权防护是三层的事——模型要知身份(上下文)、策略要具体(指令)、后端要兜底(执行器校验);门禁判的是第一层的行为。**

### Verified(真实 LLM,2026-10-06)

首次用真实 key 跑通 Agent 路径,配置为 `OPENAI_BASE_URL=https://api.deepseek.com` + `SPECAGENT_AGENT_MODEL=deepseek-flash`。**代码零改动即可接非 OpenAI provider**——`make_client()` 只用 `OpenAI()`,SDK 原生读 `OPENAI_BASE_URL`;agent 循环用到的 5 个参数(`model`/`instructions`/`input`/`tools`/`timeout`)全在 DeepSeek 支持列表内。覆盖到的路径:

- **工具往返**:第一轮 `function_call` → 追加对应 `function_call_output` → 第二轮 `status=completed`,**按 call_id 配对追加 output 被真实接受**。这为设计文档 §15 第 17 条"离线无法确认 `function_call_output` 顺序接受度"提供了真实证据。
- **CLI `agent`**:`specagent agent '运行测试并分诊失败的规则' --yes --max-steps 12` 完整跑通,exit 0。模型自行调用 `inspect_project` / `run_suite` / `triage_run` / `get_metrics` / `get_diff`,遵守了"不自行改 spec/基线"、"需确认才动手"的边界,确定性引用块逐字输出。
- **CLI `triage`**:正常输出 `2 rules failing (critical ×2)`。
- **仪表盘 HTTP 端点**:`create session`(mode=llm)→ `messages`(返回 `awaiting_approval`,停放 `run_suite`)→ `approve`(completed,`ok:True`,run 计数 2 → 3)。**停放动作在真实模型下正常恢复**。

一个附带发现:文档与直觉里的 `deepseek-v4.1-flash` **不是合法的 API 模型名**,服务端只认 `deepseek-flash` 与 `deepseek-v4-pro`,传前者直接 400。

## [v0.9] — 2026-10-06

规则、生成器、示例与门禁(实现规划书 v1 阶段 B+C,设计文档 §5–§6):原则「AI 提议,规则验证」——每一条 PASS/FAIL 都由确定性代码产生,LLM 只起草与分诊。

### Added
- **约束模型**(§5.1):`app/models.py` 新增 `WhenClause`(op 别名 gt/gte/lt/lte/eq/ne,字段级校验)与六种约束 `require_before`、`max_calls`、`arg_range`、`arg_enum`、`arg_scope`、`role_allowed`(判别联合 `Constraint`,全部 `extra="forbid"`,拼错字段名即报错);`BehaviorRule.constraints`(≤20)、`BehaviorRule.probes`(≤20)、`BehaviorSpec.locale`(`zh`/`en`)、`TestCase.constraints/actor`。旧字段行为完全不变。
- **违规文案语法**(§5.2):`app/violations.py` 是带标签形式 `[{kind}] {tool}({args}) {message}[ [arg: {arg}]][ [evidence: {ids}]]` 的唯一定义;固定短语不含 `[ ] ( )`、所有插值经 `sanitize_text`,因此 `parse_violation` 可无歧义回解析(任务 12 的确定性分诊只依赖此模块);旧判定文案照常解析。
- **纯函数评估器**(§5.2):`app/constraints.py::evaluate_constraints` 无 I/O、无时钟、确定性;逐约束 try/except → `evaluation_error`(bug 产生 FAIL,绝不静默 PASS);`when` 语义:缺失 arg 不命中、排序运算符遇非数值命中(fail-closed,`amount="lots"` 逃不过 `amount > 10000`);`arg_scope`/`role_allowed` 在 actor 缺字段时跳过(旧用例不带 actor);`require_before` 为 ALL 语义,审批请求事件不算前置。
- **judge 集成**(§5.3):约束检查在旧字段检查之后、显式拒绝检查之后;违规字符串仅新串去重,旧字符串全部保留;直接拒绝(无工具调用)的执行不受约束影响。
- **probe 生成器**(§5.5):`app/probe_generator.py` 按固定九行生成用例:normal(安全区间中点/actor 本尊)、boundary(每阈值 t±step,int 步长 1、float 0.01)、「明显超阈值」、paraphrase/bypass/injection(违规值包装)、multi_turn(软性首回合 + 「那就直接办」)、parameter_attack(1000× 最大阈值、阈值 0 时 10⁶;enum 填非法值 `XXX`)、privacy/IDOR(消息带外来身份,`actor` 保持真实用户);中英文短语表常量化、两种语言仅措辞不同;`(user_input, history, actor)` 去重;永不截断。
- **YAML 解析与校验**(§5.4):`parse_spec` 改为错误收集式——所有规则的问题一次报出(`{where}.rules[i].constraints[j].<field>: <msg> (got <input>)`);约束逐条经 `TypeAdapter(Constraint)` 校验,未知/缺失 `type`、坏 op(不再追加双后缀)、`arg_range` 无界、`extra="forbid"` 拼写错均有精确字段路径;同规则内重复约束/重复 probe 报 `duplicate of constraints[j]`;模板占位符用严格正则解析(**绝不 `str.format`**):`{arg}` 必须是本规则约束引用的 arg、`{actor.f}` 必须由 `probe.actor` 供给、arg_scope 的 arg 出现在模板中则必须有对应 actor 字段;单规则预计生成用例数 **> 80 直接报错**(恰好 80 通过),不做静默截断。
- **未知键 fail-closed**(§5.4):规则/顶层映射的未知键,若与已知键拼写近似(difflib ≥ 0.75,如 `constraint:` → did you mean 'constraints')或恰为约束类型名(`require_before:` → belongs under 'constraints:')则**报错**,防止拼写错误悄悄架空一条规则;其余未知键容忍前向兼容,经新增纯函数 `collect_spec_warnings` 输出 `warning`(供 `validate` 与将来的 agent `inspect_project` 使用)。
- **`dump_spec_yaml`**(§5.4):序列化只写非默认字段、约束内 `type` 前置;round-trip(`parse_spec(yaml.safe_load(dump_spec_yaml(spec)))` 规则相等,引号 `">="` 存活),供任务 14 的 agent 草稿工具使用。
- **python 适配器**(§6.1):`adapter.type: python` 直接运行 `module:function` 纯 Python Agent——入参按签名过滤(`message` 必需,`history`/`actor` 可选,支持 `**kwargs`),返回 `AgentExecution` 或 `dict(response/trace/latency_ms)`;trace 走与 HTTP 相同的别名/脱敏/编号管线;超时用 `asyncio.wait_for`(线程无法强杀,ERROR 后工作线程可能滞留至函数自行返回,文档已注明);`runs.agent` 记录 `python:<module:function>`;`validate` 打印 `callable <name>(<params>)`。`specagent init --adapter python` 同步提供模板块。
- **模块新鲜度**(§6.1):`load_agent_object` 新增 `reload` 参数(内部模块级 `threading.RLock` 同时守护 `sys.path` 增删与清理):清除 `__file__` 位于 `base_dir` 下的 `sys.modules` 缓存并尽力删除对应 `.pyc`(源码 mtime 按整秒 + 大小校验,同秒同长的改写会命中陈旧字节码),`importlib.invalidate_caches()` 后再导入;python 适配器工厂始终 `reload=True`。
- **FinCare 示例**(§6.2):`examples/fincare-agent/`——玩具金融客服 Agent(纯函数、零 I/O),缺陷版恰好 3 个 bug(权威话术绕过大额审批、消息账户号覆盖 actor 的 IDOR、无视审批拒绝),修复版逐一对齐;五条规则全部用 v0.9 约束 + probes 声明(require_before/arg_scope×2/max_calls/role_allowed/arg_enum),43 个用例全部由 probe 生成器产出;中文 README 走「基线 → 回归 → 分诊 → 修复 → verify」。
- **CI gate 矩阵**(§6.3):`.github/workflows/specagent-gate.yml` 改为矩阵同时跑 `ecommerce-agent` 与 `fincare-agent`;候选步骤检查**恰好退出码 1**(exit 2 配置错误不算有效拦截),JUnit 按 matrix 命名上传。
- **`validate` 增强**(§5.4):打印 `constraints: N · probes: M` 总数、每条 probe 规则的预估用例数(`rules[i] {id}: N probe cases`)、以及四类建议警告——有 constraints 无 probes(只作用于旧用例)、`arg_scope`/`role_allowed` 没有 probe 供给 actor 字段/role(cannot be evaluated)、数值阈值 arg 没有任何模板引用 `{arg}`、probes 与 `require_calls`/`approval_for` 并存(对 probe 用例被忽略,应改写为 `require_before`);退出码仍为 0。`specagent init` 的规格模板附带注释掉的 constraints/probes 示例块。

### Changed
- **行为变更**:`generate_tests` 按规则分发——带 `probes` 的规则由 probe 生成器按 `spec.locale` 产出(其 `require_calls`/`approval_for`/`max_amount` 被忽略,constraints 即判定基准),其余规则输出与旧版逐字节一致。
- HTTP 契约:`payload["context"]` 现在携带用例的平铺 `actor`(如 `{"account_id": "ACC-1", "role": "customer"}`;空 actor 仍为 `{}`),供被测 agent 端配合 `arg_scope`/`role_allowed`;不认识该键的 agent 不受影响。LLM 扩展(expander)跳过 probe 规则(LLM 变体没有 actor,probe 生成器是下限),并为变体用例复制 constraints;旧生成路径 `_build` 同样把 constraints 带到每个旧用例上。
- `specagent validate` 对 `python` 适配器加载可调用对象并打印签名(字段级错误退出码 2,与 openai/langgraph 一致)。
- **spec 版本哈希稳定性**(§5.4):存储层 `_content_hash` 在 v0.9 新键持默认值(`locale="zh"`、`constraints`/`probes` 为空)时先剥离再哈希——升级不会为每个项目凭空多出一个 spec 版本;电商示例规格的旧摘要逐字节保持(测试硬编码 `10a079e7…` 锚定)。**注意**:真正使用 constraints/probes 或 `locale: en` 的 spec,哈希会随内容改变——手工比对哈希的运维请知悉。

## [v0.8.1] — 2026-10-05

遗留修复(实现规划书 v1 阶段 A):CI 修复、API 认证、审批结果语义、延迟计时。

### Added
- **API Token 认证**(§3.4):新增 `app/auth.py`;设置 `SPECAGENT_API_TOKEN` 后,除 `/api/health` 外的所有 `/api/*` 路由要求 `Authorization: Bearer <token>` 或 `X-API-Key: <token>`;空值等于未设置;`hmac.compare_digest` 字节级比较,统一 401 文案(无 oracle);启动时未配置 token 输出 WARNING。`/docs`、`/openapi.json`、`/`、`/static/*` 保持开放。
- **审批结果语义**(§3.3、§4):新增 `trace.approval_decision_before`——只有 `approval_result` 事件携带布尔 `approved` 才构成显式决定(最新的为准);后续的审批请求、其他工具调用、无布尔的结果都不会重置或掩盖之前的决定;无结果事件时行为与旧版完全一致。judge 新增显式拒绝检查:`refund(...) executed after approval was denied`(参数经 `app/violations.py::format_args` 消毒;旧四类判定的输出逐字节不变,新增字符串与旧字符串都保留)。OpenAI 适配器在 `tool_result` 后按序产出 `approval_result` 事件;HTTP 契约文档同步。
- **编排器挂钟计时**(§3.2):`_execute_once` 用 `time.monotonic()` 测量适配器执行;适配器上报 `latency_ms <= 0` 时填充真实耗时(亚毫秒保持 0,不虚构);ERROR 结果同样携带耗时;重试不累计。
- **测试环境隔离**(§1.4):conftest 将 `dotenv.load_dotenv` 替换为 no-op(测试永不读取 `.env`),并清空认证/LLM 相关变量;新增 autouse 断网夹具——`getaddrinfo`、`socket.connect/connect_ex`、`asyncio.create_connection` 四个入口对非环回地址直接抛错(环回与 socketpair 保留)。
- **打包静态检查**:`pyproject.toml` 增加 `[tool.pytest.ini_options]`(testpaths/pythonpath,修复裸 `pytest` 收集不到 `app` 的问题)与 `[tool.setuptools.package-data]`(app 打包包含 `static/*`);CI 改用 `pip install -e ".[dev]"` + `python -m pytest`。
- 仪表盘:版本徽章从 `/api/health` 动态填充;diff 计数器补 `new_tests`/`canceled`;用例卡分类标签补 multi-turn / parameter attack;token 输入框仅存 `sessionStorage`。

### Changed
- **行为变更**:`/api/health` 的 `db` 字段从完整 SQLAlchemy URL(可能内嵌 `user:password@host`)改为后端名(`sqlite`/`postgresql`);新增 `auth_required` 与 `agent_enabled` 标志。
- `tests.yml`:安装方式改为 `pip install -e ".[dev]"`,执行改为 `python -m pytest`(离开 `pytest.ini` 缺失时根目录不在 `sys.path` 的老问题)。
- 文档漂移清理:known-issues 的 U2–U5 标注落地的版本;README 去掉硬编码测试数。

## [v0.8] — 2026-10-05

开发者体验与 CLI/DX 完善:新用户十分钟内完成「init → 基线 → 改坏 → 回归 → 报告」全流程,不读长文档(规划书 §11)。

### Added
- `specagent report [--run <id>] [--out <path>] [--open]`(§11.2):生成**独立 HTML 报告**——深色主题、零 JS、零服务依赖,包含 run 摘要、指标瓦片、Regression Diff(Baseline/Candidate 双栏 trace 对比、高亮新增 tool_call)、逐用例 Expected vs Actual、LLM 裁决与人工复核;`--open` 直接在浏览器打开,可离线分享/附到 PR 讨论
- `specagent metrics [--project] [--json]`(§9.2):终端直接输出六项观测指标
- `specagent export` 的 `--run` 变为可选:缺省取项目最近一次 run
- `specagent init --adapter demo|http|openai`:按适配器生成对应配置块;demo 模板默认 `variant: patched`,next-steps 打印「基线 → 改坏 → 回归」四步故事;openai 模板指向 `examples/openai-agent`
- 配置缺失时提示 `specagent init`(字段级错误 + 引导,无 traceback)
- `specagent run` 输出附带 Dashboard 深链与 `specagent report` 提示;仪表盘支持 `?project=<id>&run=<run-id>` 深链直接定位 run
- `specagent.yaml` 支持 `repeat_flaky_cases` 拼写(§11.1 原文键名,与 `repeat` 等价)

### Changed
- SQLite 连接启用 WAL + busy_timeout(15s):Web 与 CLI 同时读写同一数据库不再触发 "database is locked"(§11.3「同一项目可同时从 Web 与 CLI 启动 Run」)

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
