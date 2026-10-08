# 登录与多用户设计和验证

更新日期 2026-10-08。分支 `codex/login-multiuser`，基于用户批准合并的 PR #19，提交 `ffc368baae7ce3dd935dd8bd29dbcb8302ccf6ab`。本项独立 PR，合并仍由用户确认。

## 访问规则

`SPECAGENT_AUTH_MODE` 默认 shared，原共享令牌和本机模式保持原行为。显式 multiuser 使用账号 cookie，共享 token 不能绕过；非法模式返回 503。管理员可访问全部项目、创建项目、查看设置并管理授权。普通账号仅可访问授予 viewer/editor 的项目，viewer 不能写入或启动 Agent，editor 可执行服务端配置的已授权项目。运行、规格、执行、报告、进度和对比按资源所属项目检查，跨项目执行引用也在加载适配器前拒绝。省略项目的列表过滤为已授权项目；未分类 API 默认拒绝。

Agent 活动会话绑定 user ID 和当前登录摘要，其他账号和新登录均不能继续旧会话。新日志只对拥有者及其当前项目权限开放，管理员也不能读取其他账号的新聊天。旧无拥有者日志只对管理员开放。网页专用 ToolRegistry 检查所有运行引用属于当前配置项目，CLI 工具行为不变。基线操作及 Agent 人工审批记录实际账号。全局 8 个内存会话上限保留，淘汰只针对自己账号的空闲会话。

## 登录与部署

本机 `python -m app.accounts` 创建账号、重设密码、停用账号和授予项目角色，隐藏提示读取密码。没有默认管理员或公开注册。用户名为 3 至 64 个小写 ASCII 字母、数字、点、下划线或连字符；密码 12 至 256 字。PBKDF2-SHA256 使用 600000 次和随机 16 字节 salt。未知账号也执行 KDF，失败文案统一。数据库只保存密码摘要和 cookie 的 SHA256 摘要。

随机会话 8 小时绝对过期，HttpOnly、SameSite=Strict，HTTPS 时 Secure。远程 HTTP 登录拒绝，环回 HTTP 同时检查对端和 Host。重新登录轮换当前 cookie，退出、重设和停用撤销服务器会话。修改 API 要求源检查及与 cookie 绑定的 CSRF；CSRF 只在页面内存。请求上下文 finally 清理，防止线程池和并发请求身份混用。所有账号模式 API 响应 no-store，登录体最大 4096 字节，校验错误不回显密码。持久限流同时检查用户名和对端，每 15 分钟 20 次尝试，成功也计数。并发首次限流记录冲突时失败关闭。

四个独立账号表自动添加到已有 Store engine，不改旧表、不删除历史。不新增原 CLI parser 命令或 Store 公共写方法。默认模式与旧断言保留。当地 CLI 和文件系统仍由部署者管理，网页授权不限制本机操作。已批准并开始执行的动作会完成。

## API 示例

`POST /api/auth/login` 接受 JSON `{username, password}`，成功设置 HttpOnly cookie 并返回 `{ok, csrf}`。`GET /api/auth/me` 返回用户名、管理员标志、已授权项目角色、服务端目标角色和 csrf，不返回 cookie 凭据。`POST /api/auth/logout` 撤销当前会话。管理员 `GET /api/accounts/users` 返回账号 ID、用户名和启用状态；`GET /api/accounts/memberships?project_id=…` 读取角色，`PUT /api/accounts/memberships` 接受 `{user_id, project_id, role}`，`DELETE /api/accounts/memberships/{user_id}?project_id=…` 撤销授权。角色只接受 viewer/editor。

## 验证记录

专项在私有数据库、临时项目、离线 demo 与假客户端上进行，不读取或写入 `.env`，不碰真实服务，不请求外部模型或通知。44 项账号测试覆盖密码与 cookie 摘要、共享 token 绕过、源和 CSRF、限流、失效、账号停用、旧库升级、项目与资源权限、Agent 会话及日志隔离、上下文并发、跨项目工具引用、会话名额保护、旧无拥有者日志及流式审批线程的实际账号身份。浏览器覆盖三种角色、真实登录和失败、两种语言、窄屏、焦点、授权和撤销、审计身份、退出及再次登录。

最终完整套件为 **812 passed, 11 warnings in 370.35s**，由前一项的 767 增加 45 项。11 个警告为既有收集和 Starlette 弃用警告。旧 dashboard 182 项、偏好与无障碍 37 项和新账号浏览器 29 项全部通过，合计 248 项真实浏览器检查；CI 加入第三套浏览器专项。旧断言未修改，旧测试只有 conftest 增加模式环境隔离。首次补充的流式审批测试缺少待审批动作，修正测试准备后两项专项通过，再完整运行并得到上述结果。

19 组原 CLI stdout 字节与退出码一致，见 [证据](login-multiuser-cli-output-evidence.json)。英文登录窄屏截图 `login-multiuser.png` 和项目权限截图 `project-access.png` 已生成并实际查看，使用临时演示账号，无真实凭据。前端、Python 语法及 Git 差异检查通过。新账号接口清单及请求响应见本文件 API 示例，共用确定性判定和既有 CLI 保持不变。

未验证 PostgreSQL 实例和跨进程并发、生产 HTTPS 反向代理、MFA、读屏器人工操作及真实模型；这些不应由离线测试结果推定。
