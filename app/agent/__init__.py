"""SpecAgent agent layer (v1 design §8.1).

Dependency direction: this package may import `app.project`, `app.storage`
(read methods only), `app.regression` (diff_runs for verify), `app.spec_yaml`,
`app.trace._redact`, `app.metrics`, `app.models`, `app.violations` and
`app.llm_client`. Verdict-computing modules (judge, llm_judge, constraints,
orchestrator, adapters, generator, compiler) are reachable only through
`app.project` — enforced by the allow-list AST test in tests/test_agent_tools.py.
"""
