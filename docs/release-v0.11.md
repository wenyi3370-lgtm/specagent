# v0.11 发布候选

包版本为 0.11.0，计划 tag 为 v0.11。PR 合并、tag 和 GitHub Release 尚待批准。发布日期以实际发布为准，本文件不表示已上线，也不表示已发布到 PyPI。

本版收录网页补齐 PR #8–#22，以及 Action 集成验证 PR #24。网页可运行服务端配置的项目、查看实时进度和规则、管理历史与回收站、比较 FLAKY 逐次证据、查看及复验修复建议、查看与取消基线。Agent 面板提供执行、审批和日志；设置、语言主题、账号权限、人工确认通知及只读接入向导也已补齐。行为判定仍由确定性代码产生，LLM 的语义评估仅作建议。

Action 新增三个结果输出，保留门禁退出码。演示 PR #3 已实测跨运行缓存、更新已有评论和报告上传；集成工作流已实测只读 token 的评论权限拒绝与降级。PR #25 已恢复 main 缓存并报告四个新回归和 exit 1，缺少缓存会失败。完整记录见 [Action 验证](action-integration-validation.md)。真实 fork 尚无测试仓库。

## 安装候选包

Python 3.10 或以上。在独立虚拟环境安装构建产物，依赖由 pip 正常安装。包不包含示例项目与服务端配置，示例需使用相同版本的仓库。

```powershell
python -m venv .venv-v011
.venv-v011\Scripts\python.exe -m pip install .\specagent-0.11.0-py3-none-any.whl
.venv-v011\Scripts\specagent.exe --version
```

上述 wheel 将作为批准后的 GitHub Release 附件。现在可从发布 PR 的 package 检查产物下载候选 wheel、sdist 和 release-evidence.json。无 PyPI 发布流程，不能用 `pip install specagent==0.11.0` 代替未经验证的分发来源。

## 从 v0.10 升级

1. 停止自己管理的 SpecAgent 进程，备份数据库、规格与服务端配置。SQLite 在连接关闭后备份数据库文件；Postgres 用正常数据库备份工具保存可恢复副本。备份中的凭据留在服务器。
2. 记录旧安装与数据库位置。使用新的虚拟环境安装 wheel，保留原虚拟环境以便回退。继续使用同一份 `SPECAGENT_DB` 与服务端项目配置，勿重录基线来掩盖候选版回归。
3. 启动后检查 `specagent --version` 与 `/api/health` 均为 0.11.0。确认历史运行、基线与结果可查看。新表自动建立，旧的审计和重复详情没有数据时明确显示缺失，不补造历史。
4. 默认认证仍为 shared。对外开放 shared 服务必须设置 `SPECAGENT_API_TOKEN`。多人账号模式需要显式设置 `SPECAGENT_AUTH_MODE=multiuser`，并通过部署者命令创建账号；详见 [账号登录](../README.md)。没有默认账号。通知渠道初始关闭，由部署者另行配置，发送前仍需人工确认。
5. 在自己的测试项目执行基线与候选比较。FinCare 离线升级检查保留 v0.10 的 43 个通过用例和基线，v0.11 缺陷版仍产生四个新回归、四条门禁违规与退出码 1。

回退时停止新进程，恢复升级前数据库备份，并使用保留的 v0.10 环境和配置启动。不要让新旧进程同时写库。回退验证未覆盖托管 Postgres；实际部署前需演练备份恢复。

## 可复现的发布检查

```powershell
# 使用已有构建后端与运行依赖，不联网安装，不读取 .env
.venv\Scripts\python.exe scripts/check_release_package.py --previous-ref v0.10
.venv\Scripts\python.exe -m pytest tests/test_version.py tests/test_storage.py tests/test_baseline_history.py tests/test_run_progress.py -q
```

打包脚本只复制 Git 跟踪的包源码，在临时目录构建 wheel 和 sdist，以 `--no-index --no-deps --target` 非编辑安装到仓库外。它核对 app/CLI 的实际导入位置、入口点、全部静态资源、版本与健康接口。另从已发布的 v0.10 tag 构建旧 wheel，在旧版创建临时 SQLite 基线，再用新版读取同一数据库并比较保存的规格、用例与执行证据摘要。健康检查使用另一份临时数据库，不能提前把升级数据库变成新格式。

产物保存在忽略的 `dist/`，release-evidence.json 包含文件大小和 SHA-256。第三方依赖复用当前环境，这不是全新离线依赖安装验证。CI 在 Python 3.10 与 3.12 上执行同一检查并上传附件，原有 pytest、浏览器、两套门禁、Action 自检与集成检查继续执行。

本机专项 46 passed、1 个既有警告、15.88 秒，完整测试 868 passed、11 个既有警告、476.17 秒。首轮 CI 在 Python 3.10/3.12 的构建和升级检查均通过，候选附件已下载并独立核对 SHA-256。首轮 Linux 浏览器暴露原有设置窗口关闭等待不足，已保留原断言与 182 项检查，仅等待异步 close 事件完成后检查清空与焦点。完整本机结果来自该等待修正之前，修正后的浏览器专项 4 passed、77.15 秒；最终源提交 CI 见 PR 最新检查，不把旧全量结果冒充修改后的重跑。

## 发布前仍需核对

- 发布 PR 的 pytest、browser、package、gate、selftest 与 Action 集成检查必须全部通过，以 PR 最新源提交检查为准。
- [x] main 到 PR 的缓存消费证据为 verified，四个新回归及 exit 1
- [ ] 用户批准合并发布 PR
- [ ] 对合并提交重新构建包，核对版本、附件与哈希
- [ ] 用户批准创建 v0.11 tag 与 GitHub Release，发布后验证附件及链接

本版不冻结全部接口，不等同于 v1.0。公开 Docker 与托管 Postgres、确定性判定与真实 LLM 裁判对比实验、OpenTelemetry 导入、MCP Tool Proxy 仍在路线图。U7 的线程超时、U12 的同名模块缓存、U13 的用例数重算与进程内锁、U8 的内存会话及 U14 的旧接口限制继续保留。测试没有读取真实配置、调用真实模型或发送真实通知，通知渠道实发与完整无障碍手工验收仍未验证。
