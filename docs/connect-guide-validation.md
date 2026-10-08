# 只读接入向导设计与验证

更新日期 2026-10-08。分支 `codex/web-connect-guide`，基于用户批准合并的通知 PR #21，合并提交 `c86b38ef1d287061e165afe3ecdffa12b68b410b`。本项独立 PR，合并仍由用户确认。

## 内容与边界

`app/project_templates.py` 保存原有 CLI 的四种 adapter block、配置模板函数和规则模板。CLI 仍用原 `_config_template` 与 `_SPEC_TEMPLATE` 名称调用，parser、文件写入、跳过与 force 行为和提示保持原样。没有新增 langgraph 模板，因为原 init 不提供。

新增 `GET /api/project/templates/{adapter}`，adapter 只接受 demo/http/openai/python，未知值 404。返回 `adapter`、`config_yaml`、`spec_yaml`、`environment_variables`、`optional_environment_variables`、`adapter_notes`、`steps` 与 `cli_command`。响应完全来自静态内容，模板生成不读服务器配置、数据库、规则文件、环境变量值或被测模块，不创建客户端或发送网络请求。认证仍按原机制读取账号会话或共享令牌，multiuser 的所有已登录账号均可查看，包括无项目权限的账号；shared 使用原 token 或本机模式。其他项目 API 权限不放宽。

名称列表统一包含 SPECAGENT_PROJECT_CONFIG，http 加 TARGET_AGENT_URL，openai 加 OPENAI_API_KEY，可选 OPENAI_BASE_URL，demo/python 不需要外部凭据。地址、路径或凭据只能由部署者在服务器配置。页面没有填写这些值的输入框。

两个模板分别复制，YAML 与 CLI 语句始终保留原文，不随界面语言翻译。步骤说明先保存 specagent.yaml 和 specs/behavior.yaml，按实际 Agent 修改示例、配置本机入口、运行 CLI validate、在服务器设置进程环境并保留现有数据库和认证配置，最后重启自己管理的服务并核对顶部测试目标。项目选择仍只筛选历史，不切换服务器目标。浏览器所在电脑可能不是服务器，复制不等于完成接入。

API 模板使用 LF，原 CLI 的 write_text 继续采用平台换行，Windows 文件保持 CRLF。31 组字节比较覆盖前 19 组以及四种 init 的创建、已有文件跳过和 force，比较同一路径的 stdout、退出码，并直接比较创建的配置和规则文件字节。剪贴板可能转换换行，浏览器比较仅统一 CRLF/LF，源内容不翻译。

“最近一次服务端校验”可显示此前原 Validate 结果。刷新只在明确点击后调用现有 `POST /api/project/validate`，JSON/Host、认证、CSRF、服务器目标项目编辑权限和项目锁保持不变。校验会导入被测模块，页面在按钮旁明确说明。选择模板、复制、打开和关闭不触发校验或执行。模板选择不改变服务器配置，校验始终针对部署者当前配置，不假装验证浏览器里选中的模板。配置缺失或权限不足如实显示错误。viewer 可复制，不能校验。

对话框沿用现有主题，双栏模板在窄屏堆叠。使用文本节点，原始 YAML 可键盘聚焦与滚动，dialog 有名称、关闭焦点返回和 Tab 循环。切换模板、关闭和重新打开通过 generation 忽略旧响应，复制反馈也检查当前版本。校验在途不会重复提交。

## 验证记录

专项使用私有临时项目、数据库、服务端口和离线 demo。不修改真实服务，不读写 `.env`，不调用真实 LLM 或通知。后端 11 个新增测试覆盖四种 CLI 文件内容一致、模板解析、五种未知适配器、文件和数据库访问阻断、私有配置值不回显、令牌认证、无授权账号读取和原校验权限保护。接口、旧 CLI/DX 与认证合计 **40 passed, 1 warning in 11.61s**。

31 组 CLI 字节和退出码一致，见 [证据](connect-guide-cli-output-evidence.json)，原 19 组保留。首轮新增浏览器复制比较遇到 Windows 剪贴板 CRLF 转换，改用现有浏览器测试的换行比较方式，并等待复制完成；其他检查通过。五套浏览器 wrapper 最终 **8 passed in 148.41s**。旧 dashboard 182、偏好 37、账号 29、通知 22 项保留，新向导 22 项通过，共 292 项检查。新专项覆盖四种模板、环境变量名称、等价命令、两种复制、双语与原文、迟到响应、无自动校验、明确校验当前服务器目标、文件字节保持、viewer、窄屏、焦点和可访问名称。CI browser 新增向导专项。

最终完整套件 **855 passed, 11 warnings in 585.64s**，比前一项的 843 增加 11 个模板后端测试和 1 个浏览器 wrapper。11 个警告为既有收集和 Starlette 弃用警告。中文截图 connect-guide.png 已生成并实际查看，模板显示 HTTP，而校验结果明确来自临时服务器配置的 demo。Python、JavaScript 语法及暂存差异检查通过。没有删除或改动旧断言。

未验证真实 HTTP/OpenAI 接入、外部服务连通性、生产 HTTPS 部署和人工读屏器。这些不由静态模板或离线校验结果推定。
