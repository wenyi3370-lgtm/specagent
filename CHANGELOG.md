# Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/);版本号遵循语义化版本。

## [v0.1] — 2026-10-05(基线)

MVP:打通「自然语言规则 → 规格编译 → 测试生成 → Agent 执行 + trace 采集 → 确定性判定 → 可视化回归报告」完整闭环。

### Added
- Spec Compiler 双通道:OpenAI Responses API 编译,无 key 或失败时降级为确定性中文电商匹配器
- 测试生成器:正常 / 边界(阈值±1)/ 社工绕过 / 提示注入 / 隐私 五类用例
- 内置演示 Agent(含 2 个故意 bug:主管话术绕过退款审批、"不用确认"绕过地址确认)
- 外接 Agent HTTP 适配器(`TARGET_AGENT_URL` + Bearer Token,trace 契约)
- 确定性 Judge:required / forbidden calls 比对 + 退款审批闸门
- 深色单页仪表盘(行为得分、通过/失败、规则卡、逐用例 trace 与违规详情)
- Dockerfile、`.env.example`、基础冒烟测试 `tests/test_core.py`

### Known issues
- 见 `docs/known-issues.md`(v0.1.1 修复目标)
