# SpecAgent 架构梳理(基于 v0.1)

> 本文档记录 v0.1 基线的实际架构,作为后续迭代的对照基础。改动架构时请同步更新本文。

## 核心链路

```
自然语言需求(前端 textarea)
        │
        ▼
┌─────────────────┐   OPENAI_API_KEY 存在 → OpenAI Responses API
│  Spec Compiler  │──┤
└─────────────────┘   无 key / LLM 失败 → 确定性中文电商匹配器(compile_demo)
        │
        ▼
   BehaviorSpec(规则列表)
        │
        ▼
┌─────────────────┐
│ Test Generator  │  每条规则生成 正常/边界/绕过/注入/隐私 用例
└─────────────────┘
        │
        ▼
┌─────────────────┐   TARGET_AGENT_URL 存在 → run_http_agent(POST {message})
│  Agent Runner   │──┤
└─────────────────┘   否则 → 内置 demo agent(含 2 个故意 bug)
        │
        ▼
   AgentExecution{response, trace[]}
        │
        ▼
┌─────────────────┐
│     Judge      │  确定性比对:required calls 必须出现,forbidden calls 不得出现
└─────────────────┘
        │
        ▼
   RunAllResponse{spec, tests, results, passed, failed, score}
        │
        ▼
   前端仪表盘(app/static/index.html,单文件无构建)
```

## 模块职责

| 模块 | 职责 | 关键函数 |
|---|---|---|
| `app/main.py` | FastAPI 入口;`/api/compile`、`/api/run-all`、`/api/health` | `run_all()` 串行跑全部用例 |
| `app/models.py` | 全部 Pydantic 模型,单一事实来源 | — |
| `app/compiler.py` | NL → BehaviorSpec;双通道编译 | `compile_spec()` / `compile_with_llm()` / `compile_demo()` |
| `app/generator.py` | 规则 → 测试用例(按 rule.id / rule.action 分支) | `generate_tests()` |
| `app/judge.py` | 确定性判定,无 LLM 参与 | `judge()` |
| `app/agents/demo.py` | 内置演示 Agent,**故意埋 2 个 bug**:①"主管"话术绕过大额退款审批;②"不用确认"绕过地址确认 | `run_demo_agent()` |
| `app/agents/http_agent.py` | 外接真实 Agent 适配器,30s 超时,Bearer Token 可选 | `run_http_agent()` |
| `app/static/index.html` | 深色单页仪表盘,原生 JS,无构建步骤 | — |

## 数据模型链

```
BehaviorSpec ──1:N── BehaviorRule
      │                 (id / action / condition / require_calls / forbid_calls / severity)
      ▼
TestCase(rule_id 关联;category ∈ normal/boundary/bypass/injection/privacy)
      ▼
AgentExecution(response + trace: TraceEvent[])
      ▼
TestResult(passed / violations[] + 完整 execution 快照)
      ▼
RunAllResponse(spec + tests + results + 统计)
```

## 对外契约(外部 Agent 需实现)

```
POST TARGET_AGENT_URL
请求:  {"message": "用户测试输入"}
响应:  {"response": "文本",
        "trace": [{"type": "tool_call", "name": "refund", "args": {...}}]}
```

trace 是整个产品的核心抽象:**测的是 Agent 做了什么(工具调用),不是说了什么**。

## 设计决策记录

- **确定性 Judge 优先**:能用 trace 硬校验的不交给 LLM 评审,保证可复现;LLM-as-judge 留给语义类标准(语气、解释质量)。
- **编译双通道**:LLM 编译失败时静默降级到 demo 编译器,MVP 保证任何情况下可演示(副作用:可能掩盖 LLM 配置错误,见 known-issues)。
- **demo agent 故意不完美**:2 个 bug 保证开箱即可在仪表盘上看到 FAIL,证明回归捕获能力。
