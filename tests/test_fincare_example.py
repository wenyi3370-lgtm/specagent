"""Integration tests: FinCare example end to end (v1 design §6.2, task 9).

The example project is copied into tmp_path for every test (ignoring
__pycache__), so importing the agents never dirties the repository and the
module-freshness machinery is exercised against real files.
"""
import asyncio
import shutil
from pathlib import Path

from app.adapters import load_agent_object, resolve_adapter
from app.adapters.base import ExecutionContext
from app.adapters.openai_adapter import OpenAIAgentDefinition
from app.config import load_config
from app.generator import generate_tests
from app.orchestrator import execute_suite
from app.regression import diff_runs
from app.spec_yaml import load_spec_file
from cli.specagent import EXIT_GATE_FAILED, EXIT_OK, main

REPO_ROOT = Path(__file__).resolve().parents[1]


def _copy_example(name: str, tmp_path: Path) -> Path:
    src = REPO_ROOT / "examples" / name
    dst = tmp_path / name
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return dst


def _load(tmp_path, config_name):
    proj = _copy_example("fincare-agent", tmp_path / config_name.removesuffix(".yaml"))
    config = load_config(str(proj / config_name))
    spec = load_spec_file(config.spec)
    adapter = resolve_adapter(config.adapter, base_dir=str(proj))
    return proj, config, spec, adapter


def _suite(spec, adapter):
    tests = generate_tests(spec)
    results = asyncio.run(execute_suite(
        spec, tests, adapter=adapter, concurrency=4, timeout_seconds=30,
        repeat=1, retries=1, max_trace_events=200, max_response_chars=20000))
    return tests, results


def _run_dict(run_id, spec, tests, results):
    """Shape a run dict like Store.get_run() (diff_runs consumes test_case_id)."""
    return {"id": run_id, "spec": spec.model_dump(),
            "tests": [t.model_dump() for t in tests],
            "results": [{
                "id": f"{run_id}-{r.test.id}", "run_id": run_id,
                "test_case_id": r.test.id, "rule_id": r.test.rule_id,
                "status": r.status, "violations": r.violations,
                "trace": [e.model_dump() for e in r.execution.trace],
            } for r in results]}


def test_configs_load_and_adapters_resolve(tmp_path):
    for name, label in (("specagent.yaml", "python:agent:run_agent"),
                        ("specagent.baseline.yaml", "python:agent_fixed:run_agent")):
        _proj, config, spec, adapter = _load(tmp_path, name)
        assert len(spec.rules) == 5 and spec.locale == "zh"
        assert adapter.name == label


def test_validate_prints_python_callable(tmp_path, capsys):
    proj = _copy_example("fincare-agent", tmp_path)
    assert main(["validate", "--config", str(proj / "specagent.baseline.yaml")]) == EXIT_OK
    out = capsys.readouterr().out
    assert "callable run_agent(message, history, actor)" in out
    assert "constraints: 6 · probes: 7" in out  # 6 constraints across 5 rules


def test_fixed_agent_passes_every_case(tmp_path):
    _proj, _config, spec, adapter = _load(tmp_path, "specagent.baseline.yaml")
    tests, results = _suite(spec, adapter)
    assert len(tests) == 43  # 10 + 6 + 6 + 5 + 5 + 5 + 6 probe-generated cases
    failing = [(r.test.id, r.violations) for r in results if r.status != "PASS"]
    assert not failing, failing


def test_vulnerable_regressions_are_exactly_two_rules(tmp_path):
    proj = _copy_example("fincare-agent", tmp_path)
    config = load_config(str(proj / "specagent.yaml"))
    spec = load_spec_file(config.spec)
    fixed = resolve_adapter(load_config(str(proj / "specagent.baseline.yaml")).adapter,
                            base_dir=str(proj))
    vuln = resolve_adapter(config.adapter, base_dir=str(proj))
    fixed_tests, fixed_results = _suite(spec, fixed)
    vuln_tests, vuln_results = _suite(spec, vuln)
    diff = diff_runs(_run_dict("base", spec, fixed_tests, fixed_results),
                     _run_dict("cand", spec, vuln_tests, vuln_results))
    regs = [e for e in diff.entries if e.diff_type == "NEW_REGRESSION"]
    assert {e.rule_id for e in regs} == {"LARGE_TRANSFER_APPROVAL", "ACCOUNT_SCOPE"}
    text = "\n".join(v for e in regs for v in e.violations)
    assert "executed after approval was denied" in text      # ×1000 case, desk denies
    assert "executed before required prerequisite" in text   # VIP/主管 bypass
    assert "[scope_violation]" in text                       # IDOR with a foreign account


def test_cli_gate_story_with_temp_db(tmp_path, capsys):
    proj = _copy_example("fincare-agent", tmp_path)
    db = str(tmp_path / "fincare.db")
    assert main(["run", "--config", str(proj / "specagent.baseline.yaml"),
                 "--set-baseline", "--db", db]) == EXIT_OK
    capsys.readouterr()
    assert main(["run", "--config", str(proj / "specagent.yaml"), "--db", db]) == EXIT_GATE_FAILED
    capsys.readouterr()
    # re-run the fixed agent against the most recent (vulnerable) run: all FIXED
    assert main(["run", "--config", str(proj / "specagent.baseline.yaml"),
                 "--baseline", "last", "--db", db]) == EXIT_OK
    out = capsys.readouterr().out
    assert "FIXED" in out
    assert "Result: PASSED" in out


def test_module_collision_across_examples_is_resolved_by_reload(tmp_path):
    """Both examples define a top-level agent.py. Importing openai-agent's
    `agent` module first must not shadow fincare's (§6.2) — the python adapter
    loads with reload=True."""
    openai_proj = _copy_example("openai-agent", tmp_path)
    fincare_proj = _copy_example("fincare-agent", tmp_path)
    openai_obj = load_agent_object("agent:AGENT", base_dir=str(openai_proj))
    assert isinstance(openai_obj, OpenAIAgentDefinition)
    config = load_config(str(fincare_proj / "specagent.yaml"))
    adapter = resolve_adapter(config.adapter, base_dir=str(fincare_proj))
    assert adapter.name == "python:agent:run_agent"
    execution = asyncio.run(adapter.execute(
        __import__("app.models", fromlist=["TestCase"]).TestCase(
            id="T-1", rule_id="R", category="normal", user_input="帮我查一下余额"),
        ExecutionContext(test_case_id="T-1", timeout_seconds=5)))
    assert "余额" in execution.response  # fincare's run_agent answered
