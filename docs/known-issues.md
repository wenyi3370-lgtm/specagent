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
- **[U2] 原生框架 Adapter** [已修复 v0.4]:OpenAI Responses 与 LangGraph 原生适配器落地(`app/adapters/`),见 CHANGELOG v0.4。
- **[U3] LLM 生成对抗用例与 LLM-as-judge 分层** [已修复 v0.5]:`app/expander.py` + 四层 Judge(确定性/语义/LLM/人工复核),LLM 裁决 advisory,见 CHANGELOG v0.5。
- **[U4] PostgreSQL / projects·specs 实体化** [已修复 v0.6]:SQLAlchemy 存储层 + projects/specs/violations 实体,`SPECAGENT_DB` 支持 Postgres URL,见 CHANGELOG v0.6。
- **[U5] SSRF allowlist 管理** [已修复 v0.7]:`adapter.allowed_hosts` + `SPECAGENT_ALLOWED_HOSTS`,HTTP 适配器强制校验,见 CHANGELOG v0.7。
- **[U6] API 认证** [已修复 v0.8.1]:`SPECAGENT_API_TOKEN` 保护 `/api/*`(`/api/health` 除外,且不再泄露数据库 URL),见 CHANGELOG v0.8.1。
- **[U7] python 适配器的超时无法强杀线程**(v0.9 起):`adapter.type: python` 用 `asyncio.wait_for` 放弃等待,用例记为 `ERROR`,但被测函数所在的工作线程无法被强制终止,可能滞留到函数自行返回(死循环的函数会一直占着线程)。缓解:被测函数应自带超时;测试用的 Agent 保持纯函数、零 I/O。
- **[U8] 仪表盘 Agent 会话仅存内存**(v0.10 起,设计限制,§8.9):面板的会话(≤ 8 个、空闲 1 小时过期、超出时淘汰最久未用的**且未持锁**的)不落库,进程重启即丢失,也不支持多进程共享;停放中的待批准动作同样随之消失。8 个名额全被在途请求占用时,新建会话返回 429 `too_many_sessions`(不会踢掉任何人的工作)。执行状态仍不能跨重启恢复，但网页现在可分页回看 `.specagent/agent-logs/` 中的历史日志。活动会话可以明确恢复；失效会话的审批记录只读，不自动重放。
- **[U9] 仪表盘 Agent 面板未实现** [已修复 v0.10]:设计任务 16 已交付——`/api/agent` 三个端点与页面入口,需 `SPECAGENT_API_TOKEN`(或 `SPECAGENT_AGENT_API_INSECURE=1` 仅环回 opt-in),否则 403,见 CHANGELOG v0.10。
- **[U10] 可复用 GitHub Action 未经 GitHub 实测**(v0.10):`action.yml` 与 `action-selftest.yml` 只做了本地 YAML 结构断言、`app/ci.py` 单元测试和等价 CLI 命令的干跑;缓存命中、PR 评论(含 fork 只读 token 降级)、产物上传等行为需要在 GitHub 上首次运行时确认。
- **[U11] 真实 LLM 下的 Agent 行为**[已验证 2026-10-06]:全部 LLM 路径原先只用注入的假客户端测试。已用 `OPENAI_BASE_URL=https://api.deepseek.com` + `SPECAGENT_AGENT_MODEL=deepseek-flash` 跑通工具往返、CLI `agent`/`triage` 与仪表盘三个端点;`function_call_output` 按 call_id 配对追加被真实接受(设计 §15 第 17 条的离线悬案就此落定),停放动作在真实模型下正常恢复。**验证中发现并修掉一个真 bug**:tools 曾用 Chat Completions 的嵌套形状,DeepSeek 报 422;同时补了形状断言,否则这类错误还会再溜过去。2026-10-07 补全:draft(LLM 编译器,产出可直接 validate/生成 34 个 probe 用例)与 agent → write_fix_suggestion → verify 闭环也在 deepseek-flash 上跑通;建议三选二应用后 verify 如实给出 **PARTIAL**,补齐后再验为 **ALL_FIXED**——六方判定表在真实模型条件下工作正常;`write_fix_suggestion` 需要显式源码访问(`--allow-source` 或 `agent.allow_source: true`),未开时模型拒绝编造 diff(数据边界按设计生效)。残留:其他 provider(Anthropic、Gemini 等)未验证,模型名需按各自文档填写(`deepseek-v4.1-flash` 这类展示名不是合法 API 模型名)。
- **[U12] 跨项目同名 sibling 模块缓存**(v0.9):python 适配器 reload 只清理 `base_dir` 下的模块与目标模块名;两个项目若各有同名的 sibling 模块(如 `utils.py`),后加载的项目可能命中前者缓存。测试用唯一名规避;单进程内同时测多个项目时请避免同名 sibling。
- **[U13] 网页项目运行的取消** [按钮已补,Unreleased]:`POST /api/project/runs` 的 run 先落 `running` 行并注册取消位;页面在运行中轮询运行历史拿到 in-flight run id,显示 **Cancel run** 并调用既有 `POST /api/runs/{id}/cancel`(与 CLI 同一套 `CancelRegistry`)。取消仍是协作式:已开始执行的用例会跑完(受 `run.timeout_seconds` 约束),只有未开始的用例记为 CANCELED,响应 `run.status=canceled`。`GET /api/project` 每次页面加载都会重算用例数(`generate_tests`,纯确定性计算);超大规格时可感知变慢,尚未做缓存。同一项目的并发互斥是**进程内**锁(`run_project` 与新端点按配置文件路径共用),多进程部署(uvicorn workers > 1)不互斥——与 U8 的内存会话同一边界。
- **[U14] 旧 `POST /api/runs` 的 CSRF 暴露是历史遗留**(方案 B 时记录):它接受任意来源的 JSON/表单体并可用 `req.agent` 选择 demo/http 适配器;新接口(`/api/project/runs`)已强制 JSON Content-Type + 无 token 模式的环回 Host 校验,旧接口按兼容承诺不改行为。**对外暴露服务时必须设置 `SPECAGENT_API_TOKEN`**(token 存在时 `/api/*` 全部要求认证,风险随之闭合)。


## 网页与命令行的边界

- 运行进度的执行中计数是暂定结果，整轮结束后会按共用 judge 的最终状态重新确认。服务器中断后保留最后快照，但不能从快照恢复执行；取消只对当前进程仍注册的运行有效。没有实时记录的旧运行只展示已有摘要。网页 Verify 和 Agent 批准执行可显示进度，但沿用原有不可取消的行为。
- 规格版本按编译规则去重。注释与排版变动不会创建行为版本，历史源内容保留首次保存的文本，当前 YAML 另从配置项目读取。历史详情只列最近 50 次关联运行并显示总数。复制、下载和差异使用脱敏后的源内容，剪贴板可能由系统转换换行；下载保持显示内容的 UTF-8 字节。旧记录没有版本 ID 时不提供版本跳转。源历史与版本对比暂为网页专属功能。
- 趋势只展示 completed/canceled 运行，执行中或中断未结束的运行不进入曲线。时间筛选使用运行开始时间，最多显示最新 500 次并提示截断，保留每次运行的数值。新回归数按响应注明的当前基线重新比较，基线改变时历史比较会改变，它不是历史 CI 门禁日志。无基线时不绘制新回归曲线。取消、错误、FLAKY 与空套件继续采用原有指标分母，数值与 CLI 同源。尚未做大型数据库或 PostgreSQL 实机性能验证。

- 修复建议只保存在服务器配置项目的 `.specagent/suggestions/` 中，不会同步到浏览器所在电脑。网页可以查看、复制和下载，不能应用补丁。先在项目目录用 `git apply` 应用，再点 Verify。diff 是源码原文，复制与下载保留原始内容；目录与文件禁止通过符号链接或 junction 读取，单文件读取上限为 2 MB。

- `init` 仅在命令行写入配置和规则文件。网页不能编辑配置、规则、适配器、目标地址或 CI 门禁策略。只读接入向导留待后续 PR。
- 网页校验会导入配置中的被测模块，模块初始化代码也会执行。它使用 JSON POST、认证/环回 Host 和项目锁，不能作为普通只读 GET 访问。
- Project tools 的 Draft 只生成预览。真实 LLM 的网页草稿与扩展尚未在本次变更中验证；离线编译、失败回退和假客户端已覆盖。需要写入规则时使用 Agent 面板的审批流程。
- 交接文档提到网页无取消按钮，但本次开始时 U13 的按钮已存在，所以继续保留。没有为 Verify、Validate 或 Draft 添加取消按钮。Verify 仍使用同步共用实现，不提供执行中取消。
- 新接口与下载会隐藏服务器路径、带凭据 URL 和敏感环境变量值。这是网页传输边界的差别，判定、分诊和门禁内容保持共用。已有 API 的兼容行为不在本次统一脱敏范围内。
- 非 Windows 的人工点击未在本机验证，由 Linux CI 的浏览器测试覆盖自动操作。
