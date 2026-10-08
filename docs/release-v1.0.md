# v1.0 发布与迁移

包版本为 1.0.0，公共接口约定和机器快照随源码维护。正式附件必须从通过检查的 main 重新构建、验证并发布。实际 tag、发布时间、附件和 digest 见 [v1.0 Release](https://github.com/wenyi3370-lgtm/specagent/releases/tag/v1.0)，本文件提供迁移与发布检查步骤。

公共接口的声明快照和兼容规则见 [v1 接口约定](interface-contract-v1.md)。此次冻结保留现有 CLI 参数、Action 输入输出、六类约束、Trace 格式和门禁语义，未收紧旧规格默认的 actor 缺失处理。新增或改变接口必须在 PR 中解释并审阅快照。API 的动态字典响应没有完整 schema，不能将其内部所有字段算作已被快照锁定。

## 从 v0.11 升级

1. 停止自己管理的实例，备份数据库、规格和服务端项目配置。SQLite 在停止写入后保存副本，Postgres 用数据库备份工具。备份不得提交到仓库。
2. 使用新的虚拟环境安装新版 wheel，保留 v0.11 环境和备份。不要清空数据库或重建基线来掩盖候选差异。
3. 保留原 SPECAGENT_DB、项目配置、认证模式和 API token，检查 `specagent --version` 与 `/api/health` 为 1.0.0，并检查历史运行、基线、逐次证据和账号权限。
4. 在自己的测试项目比较已保存的基线。自动升级验证使用临时 FinCare 项目和 SQLite，保留旧版 43 个通过用例及全部证据，候选缺陷版仍有四个新回归、四条门禁违规和退出码 1。
5. 回退时停止新实例，恢复升级前数据库副本，使用保留的旧环境启动。不要让新旧版本同时写入同一数据库。

```powershell
python scripts/export_contract.py --check
python scripts/check_release_package.py --previous-ref v0.11
```

检查从 Git 跟踪的包源码构建 wheel 和 sdist，在仓库外非编辑安装，核对实际导入位置、静态资源、CLI、health 和升级证据。它禁止 dotenv，使用临时数据库和离线安装。CI 的 Python 3.10 与 3.12 同时保留从 v0.10 升级的检查，并增加从 v0.11 升级。

## 发布步骤和边界

合入后对 main 执行契约、完整测试和 v0.11 升级检查，以该提交重建最终包；发布前确认版本、tag 目标、三个附件的大小和 SHA256，发布后核对 GitHub digest 和链接。未发布到 PyPI。不要把 PR 构建产物当成已经发布的正式附件，也不要将预合入包的哈希用来说明合入后的包。

公开 Docker 与托管 Postgres 部署按用户要求暂缓，托管数据库恢复还没有验证。真实 fork 仍缺少第二账号，现有受限 token 验证不能替代 fork 的完整工作流。U7 的 Python 线程超时、U8 的进程内会话、U12 的模块缓存、U13 的用例数重算和进程内锁、U14 的旧运行接口限制继续保留。公开服务必须设置 SPECAGENT_API_TOKEN。

候选已接在 [实验 PR #26](https://github.com/wenyi3370-lgtm/specagent/pull/26) 之后，包含固定 trace 的真实 LLM 对比和显式上下文断言，报告保留人工审阅和覆盖限制。v1.0 发布前需让所选的主线功能经过同一提交的检查；不能仅凭两个独立分支各自通过就声称组合版本已经验证。OpenTelemetry 导入、MCP Tool Proxy、实发通知和完整无障碍人工验收仍未完成。
