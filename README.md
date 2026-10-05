# SpecAgent v0.1

**Behavior-driven testing and regression detection for AI agents.**

> Write how your agent should behave. SpecAgent turns the requirements into executable behavior tests and checks the agent's tool trace for violations.

## 仓库结构与迭代约定

```text
D:\SpecAgent\
├── app/                  # 主线代码(当前迭代始终在此,不复制版本目录)
├── tests/                # 测试
├── docs/
│   ├── architecture.md   # 架构现状(改架构须同步更新)
│   ├── known-issues.md   # 已知问题清单(修复后打勾并标注版本)
│   └── roadmap.md        # 迭代路线图
├── CHANGELOG.md          # 版本变更记录
└── README.md
```

- 版本管理:git 为主,每发布一版打 tag(`vX.Y`),历史版本靠 tag 回溯,不在仓库里堆 `versions/` 目录。
- 迭代流程:改代码 → 跑 `pytest` → 更新 `CHANGELOG.md` 与 `docs/` → commit → 打 tag。

## What this MVP proves

This first version intentionally focuses on one thin but complete loop:

1. Natural-language product rules
2. Behavior Spec compilation
3. Automatic behavior-test generation
4. Agent execution + tool trace collection
5. Deterministic PASS / FAIL judging
6. Visual regression report

The bundled demo agent contains two intentional bugs, so the dashboard immediately demonstrates that SpecAgent can catch behavioral regressions.

## Architecture

```text
Natural-language requirements
           |
           v
      Spec Compiler  ---- optional OpenAI Responses API
           |
           v
      Behavior Spec
           |
           v
      Test Generator
           |
           v
       Agent Runner  ---- built-in demo OR TARGET_AGENT_URL
           |
           v
       Tool Trace
           |
           v
   Deterministic Judge
           |
           v
    Regression Report
```

## Run locally

Python 3.10+ recommended.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

### Optional: enable AI compilation

Put an API key into `.env`:

```env
OPENAI_API_KEY=your_key_here
OPENAI_MODEL=gpt-5.5
```

Without a key, the project stays fully demoable using the deterministic Chinese e-commerce compiler.

## Connect a real agent

Set a fixed backend-controlled endpoint rather than accepting arbitrary URLs from the browser:

```env
TARGET_AGENT_URL=https://your-agent.example.com/test-hook
TARGET_AGENT_TOKEN=optional_token
```

The endpoint receives:

```json
{"message":"用户测试输入"}
```

and should return:

```json
{
  "response":"assistant response",
  "trace":[
    {"type":"tool_call","name":"refund","args":{"amount":1200}}
  ]
}
```

This trace contract is the key idea: SpecAgent tests what the agent **did**, not only what it **said**.

## Why deterministic judging first?

If a requirement says `refund > 500 requires request_human_approval`, a tool trace can be checked directly. That is more reproducible than asking another model whether the response "looks safe". LLM-as-a-judge can be added later for semantic criteria such as tone or explanation quality.

## Suggested v0.2

- Persist projects/runs in PostgreSQL
- Add LLM-generated paraphrase/adversarial cases
- Add LangGraph/OpenAI Agents adapters
- Import OpenTelemetry traces
- Compare two runs and show newly introduced regressions
- GitHub Action: fail a PR when critical rules regress

## Resume-ready description (after you actually implement/deploy it)

> Built SpecAgent, a behavior-driven testing framework for AI agents that compiles natural-language policies into executable test specs, generates boundary/adversarial cases, captures tool-call traces, and deterministically detects unsafe behavioral regressions. Added a webhook adapter for external agents and a regression dashboard designed for CI integration.
