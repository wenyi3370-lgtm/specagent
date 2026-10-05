# 迭代路线图

> 约定:每个版本一个 git tag(`vX.Y`),CHANGELOG.md 同步记录;主线代码始终在仓库根目录,不再复制版本目录。

## v0.1.1 — 加固(消化 known-issues P1/P2)

- [ ] G1 金额匹配改用词边界正则 / 显式标记
- [ ] G2 run-all 单用例异常隔离,新增 error 结果状态
- [ ] G3 审批闸门泛化(不绑定 refund)
- [ ] D1 demo agent 逻辑清理
- [ ] D2 LLM 降级可感知(warning 日志 + compiler 字段标注 fallback)
- [ ] D3 引入 logging
- [ ] D4 generator 按 action 特征路由,解除 id 耦合
- [ ] 补测试:judge 三类判定(缺 required / 出现 forbidden / 审批闸门)、generator 边界用例金额正确性

## v0.2 — 持久化与回归对比(README 既定方向)

- [ ] PostgreSQL 存 projects / specs / runs / results(SQLAlchemy + Alembic)
- [ ] 两次 run 对比:新增回归高亮(newly failed),仪表盘展示 diff
- [ ] 前端:用例分类筛选、trace 折叠、run 历史列表
- [ ] run-all 并发执行(信号量限流)+ 单用例超时

## v0.3 — 生态接入

- [ ] LangGraph / OpenAI Agents SDK 适配器(本地进程内采集 trace,不走 HTTP)
- [ ] OpenTelemetry trace 导入(复用确定性 Judge)
- [ ] LLM 生成变体/对抗用例(改写、多轮、混淆指令),与确定性生成器并存
- [ ] LLM-as-judge 仅用于语义类断言(语气/解释质量),与确定性判定分层呈现

## v0.4 — CI 化

- [ ] GitHub Action:PR 触发 audit,critical 规则回归则 fail PR
- [ ] 规则基线文件入库(`spec.yaml`),支持代码评审行为规范变更
- [ ] Docker Compose:app + postgres 一键起

## 远期想法(不排期)

- 行为规范的规则库/模板市场(退款、隐私、删号等常见策略一键套用)
- 多 Agent 并行测试(同一 spec 打多个 agent 版本,横向对比)
- trace 断言 DSL(时序:审批必须在退款之前)
