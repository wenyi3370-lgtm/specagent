# CLI/Web parity 验证记录

验证日期 2026-10-07。分支 `feature/cli-web-parity`。本记录只验收 PR A，其他网页缺口见 [补齐清单](web-completion-roadmap.md)。

网页新增项目校验、运行选项、分诊、任意两次运行对比、复验、规格草稿和 HTML/JUnit/JSON 下载。每个入口提供等价命令。网页使用服务端选定的项目配置，并沿用原有取消操作。

## 共用代码

| 文件 | 职责 |
|---|---|
| `app/project.py` | CLI 与网页共用运行流程、基线解析、扩展用例、差异与 gate；互斥锁保护写操作 |
| `app/presenters.py` | 校验报告、运行摘要、差异文字和复验内容 |
| `app/exporters.py` | JUnit 与 JSON 导出 |
| `app/drafts.py` | 草稿编译、YAML 输出和回退警告 |
| `app/trace_diff.py` | 按工具名与参数的出现次数识别新增调用 |
| `app/web_presenters.py` | 网页响应中的敏感值与服务端路径脱敏 |
| `app/main.py` | 新增受认证保护的项目操作与下载接口 |
| `app/static/index.html` | 项目工具、运行选项、结果显示和下载 |

新增接口包括 `POST /api/project/validate`、`POST /api/project/verify`、`POST /api/project/draft`、`GET /api/runs/{id}/triage`、`GET /api/runs/{id}/export?format=junit|json` 和 `GET /api/runs/{id}/report.html`。`POST /api/project/runs` 支持 `baseline`、`set_baseline` 和 `llm_expand`。示例请求 `{"baseline":"last","set_baseline":false,"llm_expand":false}`。

请求不能指定配置路径、适配器、目标地址或 gate 策略。复验只接受已存在运行或合法建议 ID。服务端错误、校验诊断、导出和报告均经过脱敏；下载请求携带认证。

## 测试结果

- 改动前全量测试 552 项通过。
- 改动后全量测试 583 项通过，11 个既有警告，无失败或错误。
- 浏览器保留原有 41 个断言，新增 17 个，共 58 个。实际启动独立临时服务器和 Chrome，验证 fixed、broken、修复后复验得到 ALL_FIXED；认证下载、草稿只读和新增工具标记均通过。
- `tests/test_cli_web_parity.py` 覆盖相同记录的 CLI/API 内容、独立运行判定、下载字节、LLM 假客户端与离线回退、并发互斥、认证、输入校验和敏感值脱敏。
- 用户已批准将旧测试中拒绝 `set_baseline` 的一条断言改为拒绝 `fail_on`，新增接受 `set_baseline` 的测试。其余原有断言保留。

`scripts/check_cli_output_parity.py` 用改动前 CLI 与当前 CLI 比较 19 组命令的 UTF-8 输出字节和退出码。覆盖 run、validate、verify、draft、baseline、triage、diff、metrics、export 和 report，普通与 JSON 输出均包含在内。全部一致，证据见 [cli-output-parity-evidence.json](cli-output-parity-evidence.json)。脚本固定同一份真实执行记录与进度，避免时间和运行 ID 的自然变化影响比较；没有通过删除或归一化输出来制造一致。

HTML 报告和网页现在仅标记新增工具调用。原先错误标记基线中已有调用的行为已修复。工具参数变化、重复调用次数和 JSON 参数键顺序有独立覆盖。

## 验证范围

本机 Windows/Python 3.10 的全量测试和实际浏览器流程已验证。真实 LLM、非 Windows 人工演示和外部通知不在本次验证范围。GitHub CI 的结果以 PR 检查页面为准。没有读取或写入 `.env`，没有修改目标 Agent 源文件、仓库示例或其他任务的未跟踪文件。
