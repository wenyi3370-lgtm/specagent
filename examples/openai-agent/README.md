# Example: OpenAI Responses API agent (native adapter)

This example shows SpecAgent v0.4's native OpenAI support: the agent is a
plain Python object — model + instructions + sandboxed tool executors
(`agent.py`) — and SpecAgent's `OpenAIResponsesAdapter` runs the function-
calling loop, harvesting the same normalized tool trace the HTTP contract
produces. The Judge never knows or cares which framework produced it.

```bash
export OPENAI_API_KEY=sk-...   # the agent's brain is the LLM; tools are mocks

# validate resolves `agent: agent:AGENT` relative to this directory
specagent validate --config examples/openai-agent/specagent.yaml

specagent run --config examples/openai-agent/specagent.yaml \
    --set-baseline --db openai-demo.db
```

The tools in `agent.py` are **mocks on purpose** — a tool executor that calls
a real refund/banking API would make the test suite itself dangerous
(README → Safety). Point executors at sandbox endpoints.

To regress-test a prompt change: edit `instructions` in `agent.py`, re-run
without `--set-baseline`, and the diff vs the recorded baseline shows any
newly broken behavior rule.
