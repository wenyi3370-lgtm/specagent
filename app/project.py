"""Shared run/verify core (v1 design §7, task 11) — print-free.

Extracted from the CLI so that `cmd_run`, the agent's `run_suite` tool and
`verify_project` share one implementation of "load → generate → run → persist
→ diff → gate". Everything the agent layer may see is exposed through
read-only properties returning immutable values or deep copies; the mutable
:class:`SpecAgentConfig` lives in the private ``_config`` and is touched only
inside this module (an AST test forbids ``._config`` elsewhere in app/agent*).
"""
import asyncio
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from .adapters import resolve_adapter
from .config import AgentSettings, SpecAgentConfig, load_config
from .errors import SpecValidationError
from .expander import expand_tests
from .generator import generate_tests
from .models import BehaviorSpec, TestCase
from .regression import DiffEntry, DiffSummary
from . import regression
from . import orchestrator
from .spec_yaml import load_spec_file
from .storage import Store


def _git_sha() -> str | None:
    sha = os.getenv("GITHUB_SHA")
    if sha:
        return sha
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5,
            check=True,
        ).stdout.strip()
    except Exception:
        return None


@dataclass
class RunOutcome:
    run_id: str
    run: dict
    diff: DiffSummary | None
    gate: list[DiffEntry]
    stats: dict
    tests: list[TestCase]
    adapter_name: str


@dataclass
class VerifyOutcome:
    pre_run_id: str
    run_id: str
    counts: dict
    verdict: str
    entries: list[DiffEntry]
    quote: str = ""
    spec_changed: bool = False


class Project:
    """Loaded project; the sandbox root is :attr:`root` (the config dir)."""

    def __init__(self, config_path: Path, config: SpecAgentConfig, store: Store,
                 spec: BehaviorSpec):
        self._config = config
        self.config_path = config_path
        self.root = config_path.parent
        self._spec = spec
        self.store = store

    @classmethod
    def load(cls, config_path: str | Path, db: str | None = None, *,
             store: Store | None = None) -> "Project":
        """``store`` (v1 design §8.9): the dashboard passes its shared Store;
        when given, ``db`` and ``config.db`` are ignored."""
        config_path = Path(config_path)
        config = load_config(str(config_path))
        spec = load_spec_file(config.spec)
        if store is None:
            store = Store(db if db is not None else config.db)
        return cls(config_path, config, store, spec)

    # -- read-only surface (§7): immutable values or deep copies -------------

    @property
    def project_id(self) -> str:
        return self._config.project

    @property
    def adapter_type(self) -> str:
        return self._config.adapter.type

    @property
    def agent_path(self) -> str | None:
        return self._config.adapter.agent

    @property
    def spec_path(self) -> str:
        return self._config.spec

    @property
    def gate_fail_on(self) -> tuple[str, ...]:
        return tuple(self._config.gate.fail_on)

    @property
    def agent_settings(self) -> AgentSettings:
        return self._config.agent.model_copy(deep=True)

    @property
    def db_backend(self) -> str:
        return self.store.backend

    @property
    def spec(self) -> BehaviorSpec:
        return self._spec.model_copy(deep=True)

    @property
    def spec_bytes(self) -> bytes:
        return Path(self._config.spec).read_bytes()

    @property
    def config_bytes(self) -> bytes:
        return self.config_path.read_bytes()


def _resolve_baseline(store: Store, project_id: str, baseline: str | None) -> dict | None:
    if baseline == "last":
        recent = store.list_runs(project_id, limit=1)
        if not recent:
            raise SpecValidationError([
                "Configuration error: --baseline last, but the project has no runs yet."])
        baseline_run = store.get_run(recent[0]["id"])
        if baseline_run is None:
            raise SpecValidationError([
                "Configuration error: --baseline last, but the project has no runs yet."])
        return baseline_run
    if baseline:
        baseline_run = store.get_run(baseline)
        if baseline_run is None:
            raise SpecValidationError([
                f"Configuration error: baseline run not found: {baseline}"])
        return baseline_run
    return store.get_baseline(project_id)


def run_project(project: Project, *, label: str = "", set_baseline: bool = False,
                baseline: str | None = None, llm_expand: bool = False,
                fail_on_override: list[str] | None = None,
                announce: Callable[[int, str], None] | None = None) -> RunOutcome:
    """One full "generate → execute → persist → diff → gate" pass (§7).

    ``announce(code, text)`` carries progress lines: code 0 = warnings
    (printed in every mode), code 1 = progress (may be skipped in --json).
    Raises :class:`SpecValidationError` for configuration problems.
    """
    config = project._config
    spec = project.spec
    adapter = resolve_adapter(config.adapter, base_dir=str(project.root))
    if adapter.name == "http" and not os.getenv(config.adapter.endpoint_env):
        raise SpecValidationError([
            f"Configuration error: adapter.type=http but env "
            f"{config.adapter.endpoint_env} is not set."])

    def _announce(code: int, text: str) -> None:
        if announce:
            announce(code, text)

    tests = generate_tests(spec)
    if llm_expand or config.run.llm_expand:
        if not os.getenv("OPENAI_API_KEY"):
            _announce(0, "warning: llm_expand requested but OPENAI_API_KEY is not set — "
                         "skipping expansion")
        else:
            tests = asyncio.run(expand_tests(spec, tests))
    _announce(1, f"Running {len(tests)} behavior tests against {adapter.name} …")

    store = project.store
    baseline_run = _resolve_baseline(store, project.project_id, baseline)
    results = asyncio.run(orchestrator.execute_suite(
        spec, tests, adapter=adapter,
        concurrency=config.run.concurrency,
        timeout_seconds=config.run.timeout_seconds,
        repeat=config.run.repeat,
        retries=config.run.retries,
        max_trace_events=config.run.max_trace_events,
        max_response_chars=config.run.max_response_chars,
    ))
    stats = orchestrator.summarize(results)
    run_id = orchestrator.persist_run(
        store, project_id=project.project_id, spec=spec, tests=tests, results=results,
        agent_label=adapter.name, label=label, commit_sha=_git_sha(),
        set_baseline=set_baseline,
        spec_source=Path(config.spec).read_text(encoding="utf-8"),
    )
    diff = None
    if baseline_run and baseline_run["id"] != run_id:
        diff = regression.diff_runs(baseline_run, store.get_run(run_id))
    run = store.get_run(run_id)
    fail_on = list(fail_on_override) if fail_on_override is not None else list(config.gate.fail_on)
    gate = regression.gate_violations(diff, fail_on) if diff else []
    return RunOutcome(run_id=run_id, run=run, diff=diff, gate=gate, stats=stats,
                      tests=tests, adapter_name=adapter.name)


def case_count(spec: BehaviorSpec) -> int:
    """Number of test cases the spec compiles to (agent tools need the count
    without importing app.generator, §8.1 allow-list)."""
    return len(generate_tests(spec))


def _choose_pre_fix_run(project: Project, pre_run_id: str | None,
                        suggestion: dict | None) -> dict:
    """§8.8 precedence: explicit --pre-run > the suggestion's pre_fix_run_id >
    the latest completed run of the same project with the same spec_id as the
    current spec whose label does not start with 'verify:'."""
    store = project.store
    if pre_run_id:
        run = store.get_run(pre_run_id)
        if run is None:
            raise SpecValidationError([f"pre-fix run not found: {pre_run_id}"])
        return run
    if suggestion is not None:
        suggested = suggestion.get("pre_fix_run_id")
        if suggested:
            run = store.get_run(suggested)
            if run is None:
                raise SpecValidationError([f"pre-fix run not found: {suggested}"])
            return run
    from .storage import _content_hash
    current_spec_id = _content_hash(project.spec.model_dump())
    for row in store.list_runs(project.project_id, limit=50):
        if row.get("label", "").startswith("verify:"):
            continue
        if row.get("status") != "completed":
            continue
        run = store.get_run(row["id"])
        if run is None:
            continue
        if _run_spec_hash(store, run) == current_spec_id:
            return run
    raise SpecValidationError(["no pre-fix run for this spec; pass --pre-run <id>"])


def _run_spec_hash(store: Store, run: dict) -> str:
    """Content hash of the spec snapshot stored with the run (compares equal
    to the current spec's hash when the spec is unchanged)."""
    from .storage import _content_hash
    return _content_hash(run.get("spec") or {})


def verify_project(project: Project, *, pre_run_id: str | None = None,
                   suggestion: dict | None = None) -> VerifyOutcome:
    """Re-run the suite against the current code on disk and diff against the
    chosen pre-fix run (§8.8). The new run never changes the project baseline
    and is labeled ``verify:<pre_run_id>`` so it is never chosen as a pre-fix
    run itself."""
    from .verify import format_verify_quote, verify_counts, verify_verdict

    pre = _choose_pre_fix_run(project, pre_run_id, suggestion)
    outcome = run_project(project, label=f"verify:{pre['id']}", set_baseline=False)
    diff = regression.diff_runs(pre, project.store.get_run(outcome.run_id))
    counts = verify_counts(diff)
    verdict = verify_verdict(counts)
    spec_changed = _run_spec_hash(project.store, pre) != _content_hash_of(project)
    quote = format_verify_quote(pre["id"], verdict, counts, diff, spec_changed)
    entries = [e for e in diff.entries
               if e.diff_type != "STABLE_PASS"
               and not (e.diff_type == "NEW_TEST" and e.candidate_status == "PASS")]
    return VerifyOutcome(pre_run_id=pre["id"], run_id=outcome.run_id, counts=counts,
                         verdict=verdict, entries=entries, quote=quote,
                         spec_changed=spec_changed)


def _content_hash_of(project: Project) -> str:
    from .storage import _content_hash
    return _content_hash(project.spec.model_dump())


def format_run_quote(outcome: RunOutcome, fail_on: Sequence[str]) -> str:
    """Deterministic quote for run results (§8.6) — pure; the agent must quote
    it verbatim."""
    run = outcome.run
    lines = [
        (f"Run {outcome.run_id} vs baseline {outcome.diff.baseline_run_id if outcome.diff else 'none'}: "
         f"{run['passed']} passed · {run['failed']} failed · {run['errors']} errors · "
         f"{run['canceled']} canceled · {run['total']} total"),
    ]
    if outcome.diff:
        counts = outcome.diff.counts()
        lines.append(
            "Diff: " + " ".join(f"{k}={counts[k]}" for k in
                                ("new_regressions", "fixed", "persistent_fail", "flaky",
                                 "new_tests", "canceled", "stable_pass")))
        if outcome.gate:
            rule_ids = ", ".join(sorted({e.rule_id for e in outcome.gate}))
            lines.append(f"Gate (fail_on: {', '.join(fail_on)}): BLOCKED by "
                         f"{len(outcome.gate)} new regression(s): {rule_ids}")
        else:
            lines.append(f"Gate (fail_on: {', '.join(fail_on)}): PASSED")
    else:
        lines.append(f"Gate (fail_on: {', '.join(fail_on)}): NOT EVALUATED (no baseline)")
    return "\n".join(lines)
