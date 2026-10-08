# 设置与关于验证

更新日期 2026-10-08。分支 `codex/settings-about`，基于已批准合并的基线历史 PR #17，保留并完成交接时的三份设置页草稿。

## 实现与接口

顶部设置与关于窗口展示版本、数据库类型、认证模式、Agent API 启用状态、LLM 配置状态、不同用途的模型、服务端测试目标、运行与审批配置及能力范围。窗口提供刷新、关闭和 Escape，Tab 在关闭与刷新之间循环，关闭后清空内容和状态并返回入口焦点。刷新失败或 401 不保留上一份快照，迟到响应不能覆盖重新打开的窗口。窄屏使用单列和纵向滚动。

`GET /api/settings` 沿用受保护 API 的 Bearer 或 X-API-Key 认证，返回 version、db_backend、read_at、auth、llm、target 和 capabilities，并设置 `Cache-Control: no-store`。没有写设置接口，不新增 CLI 子命令。响应示例中的目标可能为 `{"configured":false,"state":"missing","adapter":null,"errors":[]}`，完整响应另包含说明与截断标记。配置状态区分 missing、invalid、unreadable 和 loaded，loaded 只表示配置文件已解析。

Agent 与草稿模型按 SPECAGENT_AGENT_MODEL、OPENAI_MODEL 和既有默认模型选择。行为编译、扩展和语义裁决使用 OPENAI_MODEL 或默认模型，保留显式空值。SDK 检测只说明包是否可找到。key 状态区分未配置、空白与已配置，连接始终为 not_checked，页面不检查实际连通、额度、权限或模型名称有效性。已有 Agent 会话保留创建时模型和源码读取权限。

被测模型与 SpecAgent 模型分别展示。OpenAI 适配器只有非空 adapter.model 可静态确认，其余由目标决定，不猜测目标实际模型。HTTP 只显示有效的环境变量名称与是否设置，不显示地址。项目选择仅筛选历史，不改变服务端执行目标。

只解析项目配置，不调用 Project.load，不导入被测模块，不读规格，不实例化适配器或 LLM 客户端，不建立网络连接，不写数据库或项目文件。显示内容经过网页脱敏和凭据模式脱敏，再限制长度。项目、模块入口和模型最多 256 字，演示 variant 最多 128 字，错误最多 20 条且每条最多 1000 字，并注明截断。密钥、完整 URL、服务器路径、数据库 DSN、Agent 指令和 allowed_hosts 不进入展示。前端使用文本节点。

## 验证记录

- 设置投影新增 38 项测试，覆盖五种适配器、缺失与无效配置、读取和 stat 失败、模型优先级与空值、key 与 SDK 状态、认证、本机 opt-in、HTTP 名称与地址隐藏、截断前脱敏、错误限量和无副作用。与原认证测试合计 51 项通过；最终全量再次覆盖这些测试及 no-store 响应。
- 浏览器保留原有 164 项断言，追加 18 项设置窗口实际操作。首轮发现 Shift+Tab 焦点循环问题，修正后 **182 项全部通过**，由全量中的真实浏览器检查确认。
- 本机全量从 728 项增至 **766 项，全部通过**，11 个既有警告，用时 335.88 秒。末次 URL 协议大小写脱敏边界调整后，设置专项 **38 项再次通过**。
- 19 组旧 CLI stdout 字节与退出码一致，见 [逐项证据](settings-about-cli-output-evidence.json)。原轨迹新增调用标记、Judge 和 CLI 输出逻辑未改，继续由旧检查覆盖。

真实 Chrome 截图使用临时数据库和普通演示标签，保存在本机附件 `settings-about.png`，已实际查看，窗口所有内容完整可读。测试不读写 `.env`，不修改用户服务或仓库示例，不调用真实 LLM 或发送真实通知。真实模型连接、PostgreSQL 实机、非 Windows 人工操作和移动系统无障碍尚未在本次验证，Linux Chromium 自动操作由 GitHub CI 覆盖。

GitHub CI 的最终状态以本分支功能 PR 的 pytest、browser、两套 specagent-gate 和全部 action-selftest 为准，所有检查通过后才请求用户批准合并。语言、主题、多用户、通知和只读接入向导尚未完成。
