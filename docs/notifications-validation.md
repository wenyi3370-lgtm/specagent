# 通知设计与验证

更新日期 2026-10-08。分支 `codex/notifications`，基于用户批准合并的 PR #20，提交 `41650152012af89d28b07c909d9e80e8a3edad56`。通知独立 PR，合并由用户确认。

## 服务端配置

部署者设置 `SPECAGENT_NOTIFICATION_CONFIG` 指向 UTF-8 JSON，文件最多 65536 字节，最多 20 个渠道。渠道 ID 唯一，为小写字母开头的字母、数字、下划线或连字符，最长 40 字；别名最长 80 字。未知字段和无效配置拒绝，网页只返回 loaded/missing/invalid，不回显路径或校验输入。`.env` 路径在读取前拒绝。

以下地址和环境变量名称仅为示例。凭据由服务器进程环境提供，JSON 不存凭据值。

```json
{
  "channels": [
    {"id": "build-hook", "name": "Build feed", "kind": "webhook", "url": "https://notifications.example.invalid/build", "secret_env": "BUILD_WEBHOOK_AUTH"},
    {"id": "qa-mail", "name": "QA mailbox", "kind": "email", "host": "smtp.example.invalid", "port": 465, "tls": "ssl", "sender": "bot@example.invalid", "recipients": ["qa@example.invalid"], "username": "notification-bot", "password_env": "NOTIFICATION_MAIL_AUTH"},
    {"id": "review-pr", "name": "Review thread", "kind": "pr_comment", "repository": "example/project", "pull_number": 123, "token_env": "NOTIFICATION_GITHUB_AUTH"}
  ]
}
```

Webhook 只接受 HTTPS，环回地址可用 HTTP，不允许 URL 用户名、密码或 fragment。可选 secret_env 为请求 JSON 字节生成 SHA256 HMAC，放在 `X-SpecAgent-Signature`，前缀 sha256=。请求包含 `event: specagent.run` 与 run 摘要。不跟随重定向，不读取响应正文，2xx 才视为接受。部署者负责选择可信接收端。

邮件使用验证证书的 SSL，或显式 `tls: starttls`（通常同时设 port 587）。sender 与 recipients 必填，接收人最多 20 个，拒绝换行和无效地址。username 与 password_env 同时配置，或同时省略供部署者的受信 SMTP 使用。部分收件人拒收记为失败且可能已送达。PR 评论只发送到固定 GitHub API，repository 与 pull_number 由部署者指定，token_env 所指凭据需对该仓库有评论权限。摘要中的提及和 HTML 转义。每次网络操作超时为 10 秒，不保证总请求时间上限。

## 页面和确认

每个新渠道默认关闭，启用不触发发送。浏览器只接收 ID、脱敏别名、类型、enabled、ready；ready 只表示声明的凭据非空，不验证外部服务。地址、用户名、接收人、凭据和环境变量引用不返回页面。页面不能填写或改写这些字段。

选择 completed/canceled 运行和已启用渠道，点击预览，核对最小脱敏摘要，再勾选授权并确认。摘要只有运行 ID、项目 ID、状态、通过、失败、错误、取消、分数和总数，不包含标签、测试输入、原始输出、规格或证据。预览有效期 300 秒，绑定当前登录摘要或共享令牌摘要、渠道配置与凭据摘要、开关 revision、运行摘要。禁用再启用也使旧预览失效。发送时重新检查，变化返回 409，完整运行 ID 不符或未明确确认返回 422。

确认在事务中通过条件更新将 prepared 改成 sending，并在网络操作前提交。重复和并发确认只能认领一次。成功记录 sent，异常只保存固定 `delivery_failed_outcome_unknown`，不保存接收端正文、异常文本或凭据。没有完成钩子、自动发送、队列或重试。sent 只表示接收服务接受，不证明最终送达；超时、部分拒收和进程中断可能已送达，先核对接收端。中断可能留下 sending，不能重放原确认。已认领请求不会因退出或关闭渠道而中止。

multiuser 的管理员控制全局开关，项目 editor/admin 可预览发送，viewer 只读所属项目历史。预览不能被其他账号或同账号的新登录使用；每次确认重新检查当前项目权限和 CSRF。历史公开给当前项目成员，操作者取服务端实际账号，共享 token 如实记为 shared_token_user。无 token 本机模式只读。所有通知响应 no-store，非法输入不回显敏感值。

## API

| 接口 | 请求与结果 |
|---|---|
| `GET /api/notifications/channels?project_id=…` | 返回配置 state、公共渠道和 configure/send 权限 |
| `POST /api/notifications/channels/{id}` | JSON `{enabled: true/false}`；管理员或共享 token 修改全局开关 |
| `POST /api/notifications/preview` | JSON `{run_id, channel_id}`；返回 prepared 的 ID、最小 summary、expires_at 与实际 actor |
| `POST /api/notifications/send` | JSON `{delivery_id, confirm_run_id, confirm: true}`；单次尝试，返回最终状态 |
| `GET /api/notifications/history?project_id=…&offset=0&limit=20` | 返回 items/total/has_more；offset 最大 10000，limit 最大 50 |

两张独立表 notification_channels 与 notification_deliveries 自动加入原 Store engine，不改旧运行、证据或 Store 公共写方法。通知只在网页提供，没有新 CLI 子命令或 Agent 工具。页面运行选择只列最近 200 次；接口支持按旧运行 ID 创建预览。项目切换、选择变更、关闭或刷新清除旧确认，忽略迟到响应。对话框支持中英文、窄屏和键盘焦点。

## 验证记录

所有验证使用私有临时数据库和项目、离线适配器、假 HTTP/SMTP 或假通知 sender，不使用真实服务或凭据，不读取或写入 `.env`。后端专项覆盖关闭默认值、最小摘要、认证和项目权限、CSRF、登录归属、授权撤销、5 分钟过期、配置与凭据变更、禁止重复发送、并发单次认领、失败不确定与脱敏、旧库升级、分页、HMAC、固定 PR 地址、验证 TLS、部分拒收及 HTTP 状态。真实浏览器验证三种账号、渠道开关、预览和授权勾选、历史、失败提示、两种语言、窄屏和焦点。

通知后端专项为 **30 passed, 1 warning in 45.29s**。四套真实浏览器 wrapper 为 **7 passed in 141.00s**，保留旧 dashboard 182 项、偏好与无障碍 37 项、账号 29 项，并增加通知 22 项，合计 270 项检查。旧断言未修改；conftest 仅追加通知配置环境隔离，CI browser 新增通知专项。

首轮全量为 842 passed、1 failed、11 个既有警告，566.03 秒。失败是前一项也曾出现的 Windows 目录别名建议测试 FileExistsError，修复建议模块独立复验 **15 passed, 1 warning in 28.30s**，未修改其代码或断言。完整复验最终为 **843 passed, 11 warnings in 588.58s**，比前一项 812 增加 30 个通知后端测试和 1 个浏览器 wrapper。11 个警告为既有收集与 Starlette 弃用警告。

19 组原 CLI stdout 字节与退出码一致，见 [证据](notifications-cli-output-evidence.json)。截图 notifications.png 使用临时演示账号与假通知，已生成并实际查看，不包含真实地址或凭据。最终检查补充了账号模式中错误 JSON 字段类型的测试，在数据库查找前拒绝，专项重新通过后再开始完整验证。Python、JavaScript 语法与暂存差异检查通过。

未验证真实邮件、Webhook 接收端、PR 评论、生产网络、PostgreSQL 跨进程并发、生产 HTTPS 代理及人工读屏器。分页期间新增记录可能改变 offset。以上离线结果不代表外部服务已配置或可用。
