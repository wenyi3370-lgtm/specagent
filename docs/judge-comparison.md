# 确定性判定与 V4.1 Flash 对比

2026-10-08 完成 24 个固定合成用例，每个用例重复三次，共 72 次真实模型调用。两种方法均与预先固定的规则参考标签一致，本轮没有测出准确率或稳定性的差距。

| 指标 | 确定性判定 | DeepSeek V4.1 Flash |
| --- | ---: | ---: |
| 与参考状态一致 | 72/72 | 72/72 |
| 误报，参考 PASS 被判 FAIL | 0/27 | 0/27 |
| 漏报，参考 FAIL 被判 PASS | 0/39 | 0/39 |
| 执行 ERROR 正确识别 | 6/6 | 6/6 |
| UNCERTAIN / 无效输出 / 请求失败 | 0 | 0 |
| 三次重复状态有变化的用例 | 0/24 | 0/24 |
| 判定耗时中位数 | 0.111 ms | 820.854 ms |
| 判定耗时 P95，最近秩法 | 0.218 ms | 1204.787 ms |
| API token | 0 | 输入 52935，输出 4879 |

输入包含 34432 个缓存 token。按 [官方价格](https://api-docs.deepseek.com/quick_start/pricing/) 计算，非高峰费用估计为 0.005806146 美元，高峰为 0.011612292 美元，未核对账单。耗时包含模型网络往返，确定性耗时仅为已解析输入的 judge 调用，不包含 Agent 执行、Pydantic 解析、磁盘写入或报告。两种延迟分别测量，没有包含相同的外围开销，不适合作为端到端性能承诺。

## 固定协议与证据

规则和参考标签是为实验手工编写的形式规则示例，**没有独立人工审阅**。先提交固定用例和判定协议 `62d3acf`，再执行模型请求；没有看到模型结果后调整标签或筛选用例。参考有 9 个 PASS、13 个 FAIL 和 2 个 ERROR。三次重复属于同一个用例的重复观测，不能当作 72 个独立样本。

- [固定用例及参考理由](../experiments/judge-comparison/corpus.json)，规范化 SHA256 为 `98576c724b7d9a3c5459a67f79eff13e79c2791c9a59915cde58324249921c6a`。
- [完整 144 条判定记录与汇总](../experiments/judge-comparison/results-2026-10-08.json)，含违规、证据、归因、模型解释、token 和耗时。
- [模型比较实现](../experiments/judge_comparison.py) 与 [显式调用入口](../scripts/run_judge_comparison.py)。不进入生产门禁，也不会由 CI 发出真实调用。

模型列表实际返回展示名 `DeepSeek-V4.1-Flash`，API 请求和响应 model 均为 `deepseek-flash`。[官方模型列表](https://api-docs.deepseek.com/api/list-models/) 和 [更新记录](https://api-docs.deepseek.com/updates/) 使用相同标识。运行禁用 thinking，temperature 为 0.2，输出上限 512 token，单次超时 30 秒，SDK 不重试，请求串行。每次请求前持久记录预约，异常中断不重发已预约请求，总上限 72 次。凭据仅通过关闭回显的输入保存在内存，没有写入文件或进程环境。

两种方法得到相同的规则、actor、history 和完整 normalized trace，包括 result 和 metadata。模型输入排除参考状态、参考理由和实验分组，说明消息和 trace 内容不能改写规则。正式 `app.llm_judge` 只评估声明的语义指标，所以本实验另建处理全部形式规则的模型比较器，不能拿生产语义裁判直接与工具检查比较。

模型输出严格解析 PASS/FAIL/ERROR/UNCERTAIN，并核对证据 ID。截断、未知字段、无效 JSON、编造证据或 FAIL 没有证据都归为 UNCERTAIN，保留在总次数分母里；请求错误不会算作正确识别 Agent ERROR。本轮所有调用均成功且有完整 token 记录。

## 用例覆盖

| 组别 | 四个固定例子 |
| --- | --- |
| 审批 | 小额无需审批、大额缺审批、审批晚于动作、审批在前且无结果 |
| 账号范围 | 本人、越权且后端拒绝、消息与历史伪造身份、缺少 actor |
| 角色 | 管理员、只读角色、消息自称管理员、缺少角色 |
| 拒绝与执行错误 | 拒绝后仍动作、拒绝且不动作、适配器超时、全部事件丢弃 |
| 参数 | 包含下界、包含上界、超出上界、NaN 文本 |
| 调用 | 禁止调用、完整空 trace 的拒绝、超出次数、结果不算第二次调用 |

## 上下文断言和失败归因

`judge(test, execution, require_actor_context=True)` 显式检查 active `arg_scope` 和 `role_allowed` 所需 actor 字段。不存在、null 或空白字符串产生 `context_missing`，0 和 False 算存在。没有匹配调用或 when 未生效时无需该字段。身份来自 actor，消息中的声明不会替代它。新增违规带工具、actor 字段和事件 ID，triage 有固定修复提示。

默认参数为 False，原 CLI、网页和 YAML 语义保留缺少 actor 时跳过身份检查的行为。**本轮断言是 Python API 的显式能力和实验模式，还没有项目配置或网页开关。**若要默认收紧生产行为，需要另行兼容性评审，不能把此次研究结果当成旧规格已自动受到保护。

`app.attribution.attribute_failure` 从实际违规解析 policy、input_context、evaluator、unknown，并将 ERROR 单列 execution。它只引用已有事件；旧文案没有事件 ID 时回退到匹配工具调用。这是规则级证据归类，不推测模型心理或代码根因。归因用于实验记录，现有 triage 增加上下文提示。

## 结论与后续

这组形式规则检查，两种方法都正确。确定性方法免 API 费用且判定耗时更低，模型也能按详细规则稳定判对。本轮不能证明模型更不准确、不能支持竞品优劣结论，也没有测语义质量、生产分布、服务长期变动或截断 trace 的正确性。

下一轮应先独立审阅标签，再加入更难的真实 trace、actor 与工具参数缺失、多个审批关联、截断、对抗内容、数值与枚举混合等例子。当前 72 次授权已用完，新一轮真实调用需要另行授权。独立标注和扩大实验仍是后续工作。

复现会真实计费，应主动选择新的输出目录；沿用同一目录不会重发已预约请求。

```powershell
.venv\Scripts\python.exe -m pytest tests/test_judge_comparison.py -q
.venv\Scripts\python.exe scripts/run_judge_comparison.py --credential-prompt --out dist/judge-comparison-new
# 或在获准的本机 .env 读取必要配置
.venv\Scripts\python.exe scripts/run_judge_comparison.py --allow-local-env --out dist/judge-comparison-new
```
