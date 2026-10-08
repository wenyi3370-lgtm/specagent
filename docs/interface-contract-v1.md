# v1 接口冻结候选

本文件和 [机器快照](contracts/v1-candidate.json) 提交供评审。快照基于已发布的 v0.11，候选包版本为 1.0.0。合入后按以下兼容规则维护接口，正式 v1.0 tag 与 Release 尚未发布。

## 冻结范围

| 接口 | 记录内容 | 检查方式 |
| --- | --- | --- |
| HTTP API | OpenAPI 的路径、方法、参数、声明的请求和响应模型 | 每次 CI 比较完整快照 |
| 规格、配置与证据 | BehaviorSpec、TestCase、TraceEvent、AgentExecution、TestResult、DiffSummary、SpecAgentConfig 的 JSON Schema，六类约束 | 同一快照 |
| CLI | 子命令、参数名、默认值、取值、必填、互斥与退出码 | 同一快照及现有 CLI 测试 |
| 适配器 | async execute(case, context) 返回 AgentExecution，ExecutionContext 字段；HTTP 的 message/test_case_id/context/history | 快照及真实 MockTransport 请求断言 |
| GitHub Action | 已有输入、默认值与输出名 | 快照及 Action 集成工作流 |
| 门禁与复验 | 默认只拦 critical/high 的 NEW_REGRESSION；复验六种结论及成功退出码 | 快照及行为测试 |

ERROR 表示执行未完成，FAIL 表示行为违规，FLAKY 表示重复结果不一致。LLM 建议和人工复核保留各自记录，不改确定性状态。PERSISTENT_FAIL、FLAKY 和 NEW_ERROR 默认不阻断门禁，这是既有行为及 U1 限制。约束对没有匹配调用的合法拒绝放行，整次运行没有任何 tool_call 时的降级由编排器负责。

OpenAPI 中以任意对象返回的端点没有完整字段 schema。机器快照只冻结已声明的部分，不能检测这些对象内部字段的变化。现有 CLI/网页内容一致性、认证、隔离、逐次证据、通知与浏览器测试继续约束它们。纯文本 CLI 的文案、样式、HTML 内部结构、数据库内部表及私有函数不列入机器接口承诺；已有字节一致性检查继续保留。

## 兼容规则

维护者不能静默删除或重命名已有字段、枚举值、命令、参数、Action 输入输出，不能改变必填、默认值、身份来源或状态含义。消费者应忽略响应里的未知附加字段。新增可选字段或接口需同时更新快照与文档，在 PR 说明兼容性，并经过审阅。严格输入模型的新字段和默认行为变化同样需审阅。

破坏性修改留到下一个主版本。先给迁移说明和弃用期，在旧接口保留明确提示与替代方式。安全修复如必须收紧行为，要说明触发条件、影响和迁移办法，不能通过重新生成快照掩盖变化。机器检查故意要求所有声明变更都被审阅，不自动判定某个 schema 差异一定安全。

```powershell
python scripts/export_contract.py --check
# 只有明确修改契约时生成新的候选，并审阅 Git diff
python scripts/export_contract.py --write
```

导出在独立进程中运行，关闭 dotenv 和外网，使用临时 SQLite，不读取真实项目、数据库或模型配置。不执行 Agent，也不运行通知。包版本字段归一化，正常补丁升版不要求修改契约。

## v1.0 发布条件

1. 冻结候选 PR 经过批准并合入，最新提交的 pytest、browser、package、门禁与 Action 检查通过。
2. 已准备 1.0.0 版本、CHANGELOG 和迁移说明；对 v0.11 安装与升级验证，并在合入后核对 main 重建附件的哈希。
3. 真实对比实验需报告固定用例、独立审阅状态、无法判定、重复稳定性及调用成本，不能把小规模合成数据当通用能力证明。
4. 公开 Docker/托管 Postgres 按用户要求暂缓。真实 fork 缺少另一账号。正式发布说明必须保留这两项未验证状态，并保留 U7/U8/U12/U13/U14 等限制。

本候选完成契约登记和防漂移检查。它本身不表示公开部署、真实 fork 测试或 v1.0 发布已经完成。
