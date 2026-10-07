"""Shared presentation data. Text rendering preserves the CLI's output."""
import inspect
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

import yaml

from .adapters import load_agent_object
from .adapters.openai_adapter import OpenAIAgentDefinition
from .adapters.python_adapter import PythonAdapter
from .config import load_config
from .errors import SpecValidationError
from .generator import generate_tests
from .probe_generator import _ArgFacts, _template_args, cases_for
from .spec_yaml import collect_spec_warnings, load_spec_file
from .trace_diff import new_call_indices


@dataclass
class ValidateReport:
    ok: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    project: str = ""
    adapter: str = ""
    gate_fail_on: list[str] = field(default_factory=list)
    referenced_tools: list[str] = field(default_factory=list)
    constraints: int = 0
    probes: int = 0
    rules: list[dict] = field(default_factory=list)

    def to_text(self) -> str:
        return "\n".join(self.lines)

    def to_dict(self) -> dict:
        return asdict(self)


def _rule_warnings(i: int, rule) -> list[str]:
    """Advisory warnings for one rule, printed by `validate` (§5.4):
    declarative constraints that cannot take effect and probe/legacy-gate
    conflicts. Exit code stays 0."""
    label = f"rules[{i}] {rule.id}"
    out: list[str] = []
    if not rule.probes:
        if rule.constraints:
            out.append(f"{label}: constraints apply to legacy-generated cases only; "
                       "no boundary/actor cases")
        return out
    if rule.require_calls or rule.approval_for:
        out.append(f"{label}: require_calls/approval_for are ignored for probe-generated "
                   "cases; express them as require_before")
    actor_fields = {f for p in rule.probes for f in p.actor}
    for c in rule.constraints:
        if c.type == "arg_scope" and c.equals_actor not in actor_fields:
            out.append(f"{label}: arg_scope on '{c.arg}' cannot be evaluated — "
                       f"no probe supplies actor.{c.equals_actor}")
        if c.type == "role_allowed" and "role" not in actor_fields:
            out.append(f"{label}: role_allowed cannot be evaluated — no probe supplies actor.role")
    facts = _ArgFacts(rule.constraints)
    template_args = {a for p in rule.probes if p.template is not None
                     for a in _template_args(p.template)}
    for arg in sorted(facts.numeric):
        if arg not in template_args:
            out.append(f"{label}: numeric threshold on '{arg}' is never referenced by a "
                       f"probe template ({{{arg}}})")
    return out


def validate_report(config_path: str) -> ValidateReport:
    report = ValidateReport()

    def emit(text):
        report.lines.extend(str(text).split("\n"))
        if "warning:" in text:
            report.warnings.append(text.strip().removeprefix("warning: "))

    try:
        config = load_config(config_path)
        spec = load_spec_file(config.spec)
    except SpecValidationError as exc:
        report.errors.extend(exc.errors)
        emit(f"Invalid configuration:\n{exc.format()}")
        if any("not found" in e for e in exc.errors):
            emit("\nHint: run `specagent init` to scaffold specagent.yaml and a behavior spec.")
        return report
    except Exception as exc:  # noqa: BLE001
        report.errors.append(str(exc))
        emit(f"Invalid configuration: {exc}")
        return report
    if config.adapter.type == "http" and not os.getenv(config.adapter.endpoint_env):
        emit(f"warning: adapter.type=http but env {config.adapter.endpoint_env} is not set")
    if config.adapter.type == "http":
        allowlist = list(config.adapter.allowed_hosts) + [
            h.strip() for h in os.getenv("SPECAGENT_ALLOWED_HOSTS", "").split(",") if h.strip()]
        if allowlist:
            emit(f"  endpoint allowlist: {', '.join(allowlist)}")
        else:
            emit("  note: no endpoint allowlist set (SPECAGENT_ALLOWED_HOSTS); "
                  "only the backend-configured env URL is reachable")
    base_dir = str(Path(config_path).resolve().parent)
    if config.adapter.type in ("openai", "langgraph", "python"):
        try:
            agent_obj = load_agent_object(config.adapter.agent or "", base_dir=base_dir)
        except SpecValidationError as exc:
            report.errors.extend(exc.errors)
            emit(f"Invalid configuration:\n{exc.format()}")
            return report
        if config.adapter.type == "openai":
            if not isinstance(agent_obj, OpenAIAgentDefinition):
                report.errors.append(f"adapter.agent: expected an OpenAIAgentDefinition, got {type(agent_obj).__name__}")
                emit(f"Invalid configuration:\n  ✗ adapter.agent: expected an "
                      f"OpenAIAgentDefinition, got {type(agent_obj).__name__}")
                return report
            emit(f"  agent: {config.adapter.agent} · model {agent_obj.model}"
                  f" · tools: {', '.join(t.name for t in agent_obj.tools) or '—'}")
            if not os.getenv("OPENAI_API_KEY"):
                emit("warning: OPENAI_API_KEY is not set; openai runs will fail")
        elif config.adapter.type == "langgraph":
            emit(f"  agent: {config.adapter.agent} · langgraph graph {type(agent_obj).__name__}")
        else:  # python (§6.1)
            try:
                PythonAdapter.validate_signature(agent_obj, config.adapter.agent or "")
            except SpecValidationError as exc:
                report.errors.extend(exc.errors)
                emit(f"Invalid configuration:\n{exc.format()}")
                return report
            params = ", ".join(inspect.signature(agent_obj).parameters)
            emit(f"  agent: {config.adapter.agent} · "
                  f"callable {getattr(agent_obj, '__name__', type(agent_obj).__name__)}({params})")
    report.project = config.project
    report.adapter = config.adapter.type
    report.gate_fail_on = list(config.gate.fail_on)
    tests = generate_tests(spec)
    report.rules = [{"id": rule.id, "cases": sum(t.rule_id == rule.id for t in tests),
                     "probe_cases": len(cases_for(rule, spec.locale)) if rule.probes else 0,
                     "warnings": _rule_warnings(i, rule)} for i, rule in enumerate(spec.rules)]
    tools = {t for r in spec.rules for t in (*r.require_calls, *r.forbid_calls, *r.approval_for)}
    emit(f"✔ config OK: {config_path}")
    emit(f"  project: {config.project} · adapter: {config.adapter.type}"
          + (f" (variant {config.adapter.variant})" if config.adapter.type == "demo" else ""))
    emit(f"  spec: {config.spec} · {len(spec.rules)} rules · gate fails on: {', '.join(config.gate.fail_on)}")
    report.referenced_tools = sorted(tools)
    emit(f"  referenced tools: {', '.join(sorted(tools)) or '—'}")
    n_constraints = sum(len(r.constraints) for r in spec.rules)
    n_probes = sum(len(r.probes) for r in spec.rules)
    report.constraints, report.probes = n_constraints, n_probes
    emit(f"  constraints: {n_constraints} · probes: {n_probes}")
    try:
        with open(config.spec, encoding="utf-8") as fh:
            raw_spec = yaml.safe_load(fh)
    except OSError:
        raw_spec = None
    if isinstance(raw_spec, dict):
        for warning in collect_spec_warnings(raw_spec):
            emit(f"  warning: {warning}")
    for i, rule in enumerate(spec.rules):
        if rule.probes:
            emit(f"  rules[{i}] {rule.id}: {len(cases_for(rule, spec.locale))} probe cases")
        for warning in _rule_warnings(i, rule):
            emit(f"  warning: {warning}")
    report.ok = True
    return report



def tool_calls(trace: list[dict]) -> str:
    calls = [
        f"{e.get('name')}({', '.join(f'{k}={v}' for k, v in (e.get('args') or {}).items())})"
        for e in trace or [] if e.get("type") == "tool_call"
    ]
    return " → ".join(calls) if calls else "(no tool call)"


def diff_entry_view(entry: dict, tests: dict[str, dict]) -> dict:
    icon = "✅" if entry["diff_type"] == "FIXED" else "❌"
    sev = entry.get("severity", "medium")
    user_input = entry.get("input") or tests.get(entry["test_case_id"], {}).get("user_input", "")
    lines = [f"{icon} {entry['test_case_id']}  [{sev} · {entry['diff_type']}]",
             f"   Input: {user_input}"]
    if entry.get("expected"):
        lines.append(f"   Expected calls: {', '.join(entry['expected'])}")
    lines.extend(f"   Violation: {v}" for v in entry.get("violations") or [])
    if entry["diff_type"] in ("NEW_REGRESSION", "PERSISTENT_FAIL", "FLAKY"):
        lines.append(f"   Actual trace: {tool_calls(entry.get('candidate_trace'))}")
    return {"test_case_id": entry["test_case_id"], "input": user_input, "lines": lines,
            "new_call_indices": new_call_indices(entry.get("baseline_trace"), entry.get("candidate_trace"))}


def diff_views(diff, tests=None) -> list[dict]:
    diff = diff.model_dump() if hasattr(diff, "model_dump") else diff
    return [diff_entry_view(e, tests or {}) for e in (diff or {}).get("entries", [])
            if e["diff_type"] != "STABLE_PASS"]


def summary_text(project: str, run: dict, diff: dict | None) -> str:
    stats = f"{run['total']} tests | {run['passed']} passed | {run['failed']} failed | {run['errors']} errors"
    lines = [
        "SpecAgent Behavior Check",
        f"project: {project} · run {run['id']}" + (f" · baseline {diff['baseline_run_id']}" if diff else ""),
        stats,
    ]
    if diff:
        lines.append(
            "New regressions: {nr} | Fixed: {fx} | Persistent: {pf} | Stable: {sp} | Flaky: {fl}".format(
                nr=diff["new_regressions"], fx=diff["fixed"], pf=diff["persistent_fail"],
                sp=diff["stable_pass"], fl=diff["flaky"],
            )
        )
    return "\n".join(lines)


def run_summary(project: str, run: dict, diff, gate: dict, *, set_baseline=False) -> dict:
    diff = diff.model_dump() if hasattr(diff, "model_dump") else diff
    n = len(gate["violations"])
    fail_on = gate["fail_on"]
    if n:
        result = f"Result: FAILED ({n} new regression(s) at or above [{', '.join(fail_on)}])"
    elif diff:
        result = "Result: PASSED"
    elif set_baseline:
        result = f"Result: baseline set → {run['id']}"
    else:
        result = ("Result: first run for this project — no baseline diff yet "
                  "(use --set-baseline to record one)")
    gate_text = (f"Gate: FAILED — {n} new regression(s) at or above {', '.join(fail_on)}" if n
                 else f"Gate: PASSED (fail_on: {', '.join(fail_on)})" if diff
                 else "Gate: not evaluated (no baseline)")
    return {"project_id": project, "run_id": run["id"],
            "baseline_run_id": diff["baseline_run_id"] if diff else None,
            "stats": {k: run[k] for k in ("total", "passed", "failed", "errors")},
            "diff_counts": {k: diff[k] for k in ("new_regressions", "fixed", "persistent_fail", "stable_pass", "flaky")} if diff else None,
            "lines": summary_text(project, run, diff).split("\n"),
            "entries": diff_views(diff, {t["id"]: t for t in run.get("tests", [])}),
            "result": result, "gate_text": gate_text}


def verify_view(outcome) -> dict:
    return {"pre_run_id": outcome.pre_run_id, "run_id": outcome.run_id,
            "verdict": outcome.verdict, "counts": outcome.counts,
            "spec_changed": outcome.spec_changed, "quote": outcome.quote,
            "entries": [e.model_dump() for e in outcome.entries]}
