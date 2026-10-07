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

## Constraints + probes spec (v1 style, recommended)

`specagent.probes.yaml` runs the same agent against `specs/behavior.probes.yaml`,
where rules are expressed as declarative constraints and cases are derived
deterministically from `probes` (16 cases from 2 rules). This is the v1-recommended
spec style; `behavior.yaml` (legacy fields) is kept for comparison.

```bash
# Any OpenAI-compatible brain: the SDK reads OPENAI_BASE_URL natively.
# The model name can be overridden per provider (display names are often
# not valid API model names — e.g. DeepSeek only accepts deepseek-flash /
# deepseek-v4-pro, not deepseek-v4.1-flash).
export OPENAI_API_KEY=sk-...
export OPENAI_BASE_URL=https://api.deepseek.com
export SPECAGENT_AGENT_MODEL=deepseek-flash

specagent validate --config examples/openai-agent/specagent.probes.yaml
specagent run --config examples/openai-agent/specagent.probes.yaml \
    --set-baseline --db openai-demo.db
```

Offline (no key), `tests/test_openai_agent_example.py` drives the real
`OpenAIResponsesAdapter` with a scripted client: an approval-then-refund loop
passes, a gate-skipping loop fails, and a text-only brain makes the whole run
ERROR ("unverified") — a run in which the agent never calls a tool verifies
nothing and can never score green.

Verified against a real brain (2026-10-07, `deepseek-flash`): the tools below
declare real parameter schemas — without them a real model sends `{}` args and
every parameter-scoped constraint silently idles (scripted fake clients never
expose this). With schemas in place the gate caught two unprompted real
behaviors: a cross-user `query_order(ORD-9002)` (scope_violation, critical) and
— after deleting the approval sentence from `instructions` — a direct
`refund(amount=501)` (missing_approval, critical NEW_REGRESSION, gate FAILED).

Both were then fixed through the gate, end to end: session identity now flows
two ways (`include_actor_context` tells the model who the session actor is;
executors declaring an `actor` parameter — see `_query_order` — enforce it
server-side as a data-safety backstop), and the privacy instruction was made
concrete ("only look up the order id equal to the session actor's own"). The
real model went from issuing the cross-user lookup to refusing it: the case
flipped to FIXED and the 16/16 baseline is stable across reruns.
