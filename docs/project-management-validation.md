# 项目创建与资料展示验证

更新日期 2026-10-08。分支 `codex/project-management`，基于用户批准合并的趋势图 PR #13。

Projects 新增创建表单，保存项目 ID、名称和说明，成功后选择新项目。项目列表与顶部选择器显示名称，列表保留 ID、说明、适配器资料、运行数量和时间。新项目显示空的运行、规格和趋势状态。创建时禁止重复提交，错误保留已填写内容；重复 ID 提示冲突且不会覆盖既有资料。

创建与列表复用受保护的 `POST /api/projects` 和 `GET /api/projects`，没有新增配置写入接口。请求示例为 `{"id":"review","name":"评审项目","description":"行为审查记录"}`。创建响应含 `id`、`name`、`description`、`adapter_type` 和 `created_at`，列表另有 `runs` 与 `last_run_at`。API 兼容 `adapter_type` 资料字段，它不决定执行配置；页面不提供适配器输入。API 保留省略或传空 ID 后按名称转小写、空格转短横线的既有行为，派生 ID 同样有长度限制。重复 ID 返回 409 与 `project_already_exists`，无效输入返回 422。

ID 最多 64 字且不含控制字符，名称非空且最多 128 字，说明最多 2000 字，适配器资料最多 32 字；额外的配置、端点、适配器对象和规则路径字段被拒绝。验证错误不回显输入。名称、说明及适配器资料使用既有网页脱敏函数，ID 保持原样用于准确查找。列表使用文本节点与按钮事件，不把 ID 放进内联脚本。运行与指标增加响应代次保护，规格和趋势沿用已有保护，旧请求不会替换切换后的项目数据。

新项目空状态检查还复现了既有运行列表错误：代码对返回 undefined 的 DOM `append()` 继续调用 `append()`，将正常的空列表显示成失败。现在分别创建行、添加单元格和添加行；项目空列表同样使用正确的 DOM 操作。原有浏览器断言未删除或放宽。

项目资料创建为网页功能，CLI 不增加子命令。`specagent init` 仍仅在本机生成配置文件。项目选择用于查看历史，页面明确提示 Run project suite 继续运行服务端配置的项目。创建资料不导入被测模块、不修改配置或规格、不创建运行和基线。

验证仅使用临时项目、数据库和假适配器，没有读取或写入 `.env`，没有真实模型、外部服务调用或示例改动。

| 文件 | 改动 |
|---|---|
| `app/static/index.html` | 创建表单、资料展示、文本渲染、项目选择与异步响应保护、空列表修复 |
| `app/models.py`、`app/main.py`、`app/storage.py` | 元数据校验、脱敏与静态冲突错误、并发重复创建保护；复用现有 Store 和 web_payload，无新增共用模块或路由 |
| `tests/test_project_management.py`、`tests/browser/test_dashboard_browser.js`、`tests/test_browser_ui.py` | 22 项行为测试、12 项浏览器检查与期望总数增加；原有断言保留 |
| `README.md`、`README.en.md`、`CHANGELOG.md`、`docs/architecture.md`、`docs/known-issues.md`、`docs/web-completion-roadmap.md` | 使用说明、接口和配置边界、既有问题处理与完成状态 |
| 本文与 `project-management-cli-output-evidence.json` | 验收记录与 19 组 CLI 字节证据 |

- 新增 22 项接口与存储行为测试，覆盖配置与文件保持不变、已有运行和基线、重复和并发重复创建、名称派生 ID、参数边界及合法最大长度、未知配置字段、认证、脱敏、Unicode 与引号内容。
- 本机全量从 **643 项增加至 665 项，全部通过**，11 个既有警告。
- 浏览器保留全部 **111 项**已有检查，新增 **12 项**创建、资料展示、空状态、测试目标不变、项目深链接与刷新保留新选择、重复和空名称提示、HTML 文本展示、引号 ID 键盘选择及延迟响应检查，共 **123 项通过**。URL 的 project 参数只决定初始视图，后续刷新保留实际选择。既有 `◀ new` 检查继续通过，没有再把原有审批调用标为新增。
- 19 组旧 CLI stdout 字节和退出码一致，见 [逐项证据](project-management-cli-output-evidence.json)。

实际 Chrome 页面截图位于本机可视化附件目录的 `project-management.png`，由临时项目的真实网页操作生成。CI 状态以对应 PR 的 pytest、browser、两套 gate 与 action 自检结果为准。

数据库无需迁移。未验证 PostgreSQL 实机并发、大量项目性能、真实提供商或移动端系统无障碍。账户与权限、项目配置编辑，以及其余清单事项仍待后续 PR。
