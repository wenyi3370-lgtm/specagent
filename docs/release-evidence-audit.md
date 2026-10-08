# 发布证据核对

核对日期为 2026-10-08。代码基线为已合并 PR #22 的 `1b456c8d0baf04863a57ccba009c8f13fa9bdac2`，包版本仍为 0.10.0。本次只读取 GitHub 既有运行、评论、缓存和 Release，下载离线示例的报告检查文件，没有重跑线上工作流、发评论、调用真实模型或发布版本。

## 可复用 Action

| 行为 | 证据 | 结论 |
|---|---|---|
| baseline 通过，candidate 被门禁拦截 | [main 自检运行 37779356274](https://github.com/wenyi3370-lgtm/specagent/actions/runs/37779356274)，源提交与代码基线相同 | 已验证。candidate 使用 continue-on-error，日志记录 gate 失败，后续断言确认拦截，整个自检成功 |
| 自检报告上传 | 同一运行包含 selftest-baseline 和 selftest-candidate，产物 ID 分别为 11550314970、11551218497 | 已验证上传。自检配置为 cache=false，comment 使用默认 false，不能据此证明缓存或评论 |
| 缓存保存及同次运行内命中 | [演示运行 37610139967](https://github.com/wenyi3370-lgtm/specagent/actions/runs/37610139967)，源提交 `516de9eaa013804a5cbaab7b7562b6f8ef9c6752` | baseline 日志为 Cache not found 后保存；candidate 日志为 Cache hit 和 Cache restored successfully。缓存键为 specagent-db-Linux-5d560b6b858aeb1988b0ea0bba3c7de1b355c3c4，7327 字节，所属 refs/pull/3/merge |
| PR 评论创建 | [机器人评论](https://github.com/wenyi3370-lgtm/specagent/pull/3#issuecomment-6036376078)，创建于 2026-10-07 10:51:51 UTC | 已验证，显示 Gate FAILED、4 个新回归与 39 个稳定通过。该 PR 来自同一仓库，工作流有 pull-requests: write |
| 失败前上传报告 | 演示运行 demo-baseline、demo-candidate，产物 ID 11478050331、11478185330 | 已下载并核对两份产物各包含 specagent-report.html 和 specagent-report.xml。baseline 文件为 17949/5813 字节，candidate 为 22553/6945 字节，上传发生在最终 gate 失败之前 |
| 跨运行或跨分支恢复 | 当前只有同次运行的保存和恢复日志可作证 | 未验证。缓存列表存在一个条目并不能证明另一次运行成功恢复 |
| 更新已有评论 | 代码包含 --edit-last --create-if-none，现有记录只有一次评论 | 未验证更新行为 |
| fork 只读 token 降级 | 代码含 continue-on-error 与失败警告 | 未在真实 fork PR 验证，静态配置不等同于运行证据 |

因此 U10 改为部分验证，仍保留未决。演示 PR #3 的红色检查是预期展示，保持打开，不合并也不修绿。后续若补跨运行或 fork 场景，应单独设计测试，避免从只有缓存存在、工作流全绿或警告文本得出过强结论。

## Demo 与发布

`docs/assets/demo.gif` 已在提交 `6bf78ee` 入库，449976 字节，中英文 README 均引用。`docs/demo-script.md` 明确说明它由命令和真实输出逐帧渲染生成，属于现有离线回归故事演示，不是本轮网页新增功能的人工录屏。

[v0.10 Release](https://github.com/wenyi3370-lgtm/specagent/releases/tag/v0.10) 已发布，附件 specagent-demo.gif 为 449976 字节，specagent-demo.mp4 为 1458616 字节。本次核对附件元数据，未下载或重审视频内容。本机 promo/ 被忽略，包含宣传片，不在 Git 仓库中，和上述 Release 演示视频是不同产物。

公开 Docker 与托管 PostgreSQL 部署、接口冻结、v1.0、OpenTelemetry 导入和 MCP Tool Proxy 仍未完成。本轮网页补齐全部合并只表示网页清单完成。建议下一版为 v0.11，保留当前 Unreleased，发布前再核对打包、升级说明和剩余部署边界。

## 任务 16 的六项审查问题

六项修复的实现与新增测试已在历史提交 `c7df79d` 中，当前代码仍保留。下表区分修复证据与测试范围，不把静态断言当作完整浏览器验证。

| 问题 | 当前实现 | 测试证据 |
|---|---|---|
| 409 重试误说未执行 | index.html 的 agentResolve 对 409 显示可能已执行，并刷新运行历史；404 单独处理 | test_approval_failure_paths_are_distinguished，静态检查分支和文案 |
| action_not_found 清掉活动会话 | agentSessionGone 排除 action_not_found，agentFail 通过该函数决定是否清除会话 | 同上，静态检查；不宣称所有未知 404 都已穷尽 |
| 淘汰持锁会话 | agent_api.py 的 _register 只选未持锁会话，全部忙时返回 429；TTL 清理同样跳过持锁会话 | test_eviction_skips_sessions_with_a_request_in_flight、test_busy_sessions_are_never_purged_by_ttl，接口测试 |
| 本地 opt-in 不校验 Host | auth.py 要求对端环回且 _host_header_allowed 通过 | test_insecure_optin_host_header_must_be_loopback，接口测试 |
| 原生 confirm 阻塞 | agentResetConfirm 使用内联提示、取消/丢弃按钮及 Escape，焦点移到按钮 | test_reset_uses_an_inline_prompt_and_chips_are_generic，静态检查 |
| 示例提示绑定退款场景 | 示例 chip 使用当前项目的约束规则 | 同上，静态检查 |

审批错误、内联重置焦点的全部真实浏览器分支没有在本次重测，不能仅用总浏览器检查数证明这些场景。U7、U8、U12、U13、U14 的其他边界仍以 known-issues 为准。U11 已有 DeepSeek 实测记录，本次未调用真实模型。

本机复验 `tests/test_agent_api.py`、`tests/test_action_yaml.py`、`tests/test_ci_helpers.py` 为 66 passed、1 个既有 Starlette 弃用警告、10.72 秒，包含上表五个具名测试。此 PR 只改文档和 Action 注释，没有修改行为或旧测试断言；此前 855 项全量和 292 项浏览器结果属于 PR #22 的验证，不冒充本次新跑的结果。

## 本机收尾与后续顺序

本机 main 已快进到代码基线，三个受保护的无关未跟踪项保留。当前审计在 codex/release-evidence-audit 独立分支，尚未删除本地或远程分支，也没有改 tag 或版本号。

下一步先合并这份文档修正，再补 Action 剩余证据并准备 v0.11。对比实验应使用固定规格、同一组执行 trace、独立参考标注，比较确定性判定与 LLM 裁判的误报、漏报、重复稳定性、成本和耗时。现有 arg_scope、role_allowed 与 actor 已提供部分上下文约束，先核对能力再补缺口。LLM 裁判保持 advisory，失败归因区分规则、执行错误和证据不足。实验及真实模型调用尚未执行，不能据此宣称优于竞品。
