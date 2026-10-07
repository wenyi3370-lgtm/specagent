"""Unit tests: agent tool layer, allow-list AST, hash-bound confirmations
(v1 design §8.2, task 11). All registry-based — no LLM."""
import ast
import hashlib
import json
import uuid
from pathlib import Path

import pytest
import yaml

from app.agent import tools as tools_mod
from app.agent.sandbox import ProjectSandbox
from app.agent.tools import (ConfirmationRequired, ConfirmResult, DRAFT_MARKER,
                             REGISTRY, ToolContext, ToolRegistry)
from app.errors import SpecValidationError
from app.project import Project, run_project

REPO_ROOT = Path(__file__).resolve().parents[1]

SPEC = """\
agent: T
rules:
  - id: LARGE_REFUND_APPROVAL
    title: t
    severity: critical
    action: refund
    condition: refund amount > 500
    require_calls: [request_human_approval]
    approval_for: [refund]
"""
CONFIG = """\
project: t
adapter:
  type: demo
  variant: patched
spec: specs/behavior.yaml
"""

# The final tool table (§8.2): 14 tools across tasks 11/12/15.
TASK11_TABLE = {
    "inspect_project": ("auto", False, {"read"}),
    "list_rules": ("auto", False, {"read"}),
    "get_run": ("auto", False, {"read"}),
    "get_diff": ("auto", False, {"read"}),
    "get_trace": ("auto", False, {"read"}),
    "get_metrics": ("auto", False, {"read"}),
    "triage_run": ("auto", False, {"read"}),
    "read_file": ("auto", False, {"read"}),
    "propose_spec": ("auto", False, {"write_draft"}),
    "run_suite": ("confirm", False, {"run_suite"}),
    "verify_fix": ("confirm", False, {"run_suite"}),
    "write_fix_suggestion": ("confirm", False, {"write_suggestion"}),
    "replace_spec": ("confirm", True, {"write_spec"}),
    "set_baseline": ("confirm", True, {"write_baseline"}),
}


def _build_project(tmp_path, spec_text=SPEC, config_text=CONFIG):
    (tmp_path / "specs").mkdir(exist_ok=True)
    (tmp_path / "specs" / "behavior.yaml").write_text(spec_text, encoding="utf-8")
    (tmp_path / "specagent.yaml").write_text(config_text, encoding="utf-8")
    return Project.load(tmp_path / "specagent.yaml", db=str(tmp_path / "t.db"))


def _ctx(project, confirm=None, allow_source=False):
    return ToolContext(
        project=project,
        sandbox=ProjectSandbox(project.root, allow_source),
        allow_source=allow_source,
        confirm=confirm or (lambda req: ConfirmResult("declined", "non_interactive")),
    )


def _approve(_req):
    return ConfirmResult("approved")


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(root).rglob("*")):
        if path.is_dir() or "__pycache__" in path.parts or ".specagent" in path.parts:
            continue
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _auto_approve(req):
    return ConfirmResult("approved", auto=True)


# -- registry table ---------------------------------------------------------------


def test_tool_names_and_tiers_exact():
    registry = ToolRegistry()
    assert set(registry.tools) == set(TASK11_TABLE)
    for name, (risk, human_only, effects) in TASK11_TABLE.items():
        spec = registry.tools[name]
        assert spec.risk == risk, name
        assert spec.human_only is human_only, name
        assert spec.effects == effects, name
        assert spec.effects <= tools_mod.EFFECTS


def test_openai_schemas_use_the_flat_responses_api_shape():
    """The agent loop posts these to ``/v1/responses``, which wants ``name`` and
    ``parameters`` at the top level of each tool.

    The nested ``{"type": "function", "function": {...}}`` shape belongs to Chat
    Completions. OpenAI tolerates it on /v1/responses, but DeepSeek rejects it
    with 422 ``tools[0]: missing field 'name'`` (verified 2026-10-06), so the
    wrong shape passed every fake-client test and only broke against a live
    model. This asserts the wire shape for all 14 tools.
    """
    schemas = ToolRegistry().openai_schemas()
    assert len(schemas) == len(TASK11_TABLE)
    for schema in schemas:
        assert schema["type"] == "function"
        assert "function" not in schema, f"nested Chat Completions shape: {schema['name']}"
        assert schema["name"] in TASK11_TABLE
        assert isinstance(schema["description"], str) and schema["description"]
        params = schema["parameters"]
        assert params["type"] == "object"
        assert params["additionalProperties"] is False
        assert isinstance(params.get("properties"), dict)
        # ``required`` is optional in JSON Schema: tools that take no arguments
        # legitimately omit it.
        assert isinstance(params.get("required", []), list)
    assert {s["name"] for s in schemas} == set(TASK11_TABLE)


# -- allow-list AST test (§8.2) ----------------------------------------------------


_FORBIDDEN_MODULES = {"app.judge", "app.llm_judge", "app.constraints", "app.orchestrator",
                      "app.adapters", "app.generator", "app.compiler",
                      "subprocess", "shutil", "ctypes", "importlib", "runpy", "code", "pty"}
_FORBIDDEN_CALLS = {"exec", "eval", "compile", "__import__", "setattr", "delattr",
                    "globals", "vars", "system", "popen"}
_WRITE_CALLS = {"write_text", "write_bytes", "replace", "rename", "remove", "unlink",
                "open", "makedirs", "mkdir"}
_WRITE_ALLOWED_FUNCS = {"_h_propose_spec", "_h_replace_spec", "_h_write_fix_suggestion",
                        "_write", "ensure_state_dir"}
_WRITE_METHODS = {"ensure_project", "create_project", "save_spec", "create_run",
                  "complete_run", "add_execution", "add_review", "set_baseline"}
_READ_PREFIXES = ("get_", "list_", "count_", "latest_")
_HUMAN_BASELINE_FUNCS = {"_h_set_baseline"}


def _agent_sources():
    paths = sorted((REPO_ROOT / "app" / "agent").glob("*.py"))
    api = REPO_ROOT / "app" / "agent_api.py"
    if api.exists():
        paths.append(api)
    return paths


def test_store_methods_are_all_classified():
    from app.storage import Store
    properties = {name for name, value in vars(Store).items()
                  if isinstance(value, property)}
    for name in dir(Store):
        if name.startswith("_") or name in properties:
            continue
        assert name.startswith(_READ_PREFIXES) or name in _WRITE_METHODS, \
            f"unclassified Store method: {name} (add it to the test's table)"


def test_agent_package_allowlist():
    assert (REPO_ROOT / "app" / "agent" / "__init__.py").exists()
    violations = []
    for path in _agent_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in _FORBIDDEN_MODULES or \
                            alias.name in _FORBIDDEN_MODULES:
                        violations.append(f"{path.name}:{node.lineno} import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if f"app.{module.split('.')[0]}" in _FORBIDDEN_MODULES or module in _FORBIDDEN_MODULES:
                    violations.append(f"{path.name}:{node.lineno} from {module} import …")
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in _FORBIDDEN_CALLS:
                    violations.append(f"{path.name}:{node.lineno} call {func.id}()")
                if isinstance(func, ast.Attribute) and func.attr in {"system", "popen"}:
                    violations.append(f"{path.name}:{node.lineno} call .{func.attr}()")
    assert not violations, "\n".join(violations)


def test_agent_package_write_gates_and_keywords():
    """Writes (Path.write_*, os.replace/…, open) only inside the named writer
    functions; Store write-method calls only inside _h_set_baseline; the
    keywords set_baseline/fail_on_override and the attribute _config appear
    nowhere outside the sanctioned spots (§8.2; see open-issues #7 for the
    set_baseline resolution)."""
    problems = []
    for path in _agent_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))

        def walk(node, func_name=None):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    walk(child, child.name)
                    continue
                if isinstance(child, ast.Call):
                    func = child.func
                    name = None
                    is_os_path_write = False
                    if isinstance(func, ast.Attribute):
                        name = func.attr
                        # os.replace/rename/remove/unlink are file writes;
                        # str.replace() and friends are not.
                        is_os_path_write = (func.attr in {"replace", "rename", "remove",
                                                          "unlink", "makedirs"}
                                            and isinstance(func.value, ast.Name)
                                            and func.value.id == "os")
                    elif isinstance(func, ast.Name):
                        name = func.id
                    flagged = name == "open" or is_os_path_write or (
                        isinstance(func, ast.Attribute)
                        and func.attr in {"write_text", "write_bytes", "mkdir"})
                    if flagged and func_name not in _WRITE_ALLOWED_FUNCS:
                        problems.append(f"{path.name}:{child.lineno} write call {name}()")
                    if isinstance(func, ast.Attribute) and func.attr in _WRITE_METHODS and not (
                            func.attr == "set_baseline" and func_name in _HUMAN_BASELINE_FUNCS):
                        problems.append(f"{path.name}:{child.lineno} store write {func.attr}()")
                if isinstance(child, (ast.Name, ast.Attribute)):
                    ident = child.id if isinstance(child, ast.Name) else child.attr
                    if ident == "set_baseline" and func_name not in _HUMAN_BASELINE_FUNCS:
                        problems.append(f"{path.name}:{child.lineno} bare set_baseline")
                    if ident == "fail_on_override":
                        problems.append(f"{path.name}:{child.lineno} fail_on_override")
                    if ident == "_config":
                        problems.append(f"{path.name}:{child.lineno} private _config")
                walk(child, func_name)

        walk(tree)
    assert not problems, "\n".join(problems)


# -- read-only Project surface ------------------------------------------------------


def test_project_config_is_private_and_immutable(tmp_path):
    project = _build_project(tmp_path)
    with pytest.raises(AttributeError):
        _ = project.config
    assert isinstance(project.gate_fail_on, tuple)
    spec_copy = project.spec
    spec_copy.rules.append(spec_copy.rules[0].model_copy(deep=True))
    assert len(project.spec.rules) == 1  # deep copy per access
    settings = project.agent_settings
    settings.max_steps = 99
    assert project.agent_settings.max_steps == 12


def test_run_project_and_run_suite_agree(tmp_path):
    project = _build_project(tmp_path)
    outcome = run_project(project)
    ctx = _ctx(project, confirm=_approve)
    result = ToolRegistry().call(ctx, "run_suite", {}, "call-1")
    assert result.payload["ok"] is True
    baseline_statuses = {r["test_case_id"]: r["status"] for r in outcome.run["results"]}
    tool_statuses = {r["test_case_id"]: r["status"]
                     for r in project.store.get_run(result.payload["run_id"])["results"]}
    assert tool_statuses == baseline_statuses
    assert "quote" in result.payload and "Run " in result.payload["quote"]
    assert ctx.last_run_id == result.payload["run_id"]
    assert ctx.quotes["run_suite"] == result.payload["quote"]


# -- the confirm gate ----------------------------------------------------------------


def test_confirm_tier_never_runs_unconfirmed(tmp_path):
    project = _build_project(tmp_path)
    before_hash, before_runs = _tree_hash(project.root), len(project.store.list_runs("t"))
    for confirm in (lambda req: ConfirmResult("declined", "non_interactive"),
                    lambda req: ConfirmResult("deferred")):
        ctx = _ctx(project, confirm=confirm)
        for tool in ("run_suite", "replace_spec", "set_baseline"):
            args = {"run_id": "whatever", "draft_name": "x",
                    "draft_sha256": "0" * 64}
            result = ToolRegistry().call(ctx, tool, args, "call-x")
            if result.pending is not None:  # deferred — parked, not executed
                ctx.pending.clear()
            assert result.payload.get("ok") is False
    assert _tree_hash(project.root) == before_hash
    assert len(project.store.list_runs("t")) == before_runs


def test_confirm_tier_handler_direct_call_fails(tmp_path):
    project = _build_project(tmp_path)
    ctx = _ctx(project, confirm=_approve)
    with pytest.raises(ConfirmationRequired):
        tools_mod._h_run_suite(ctx, {}, None)
    with pytest.raises(ConfirmationRequired):
        tools_mod._h_set_baseline(ctx, {"run_id": "x"}, None)


def test_declined_confirmation_reports_reason(tmp_path):
    project = _build_project(tmp_path)
    ctx = _ctx(project, confirm=lambda req: ConfirmResult("declined", "user_declined"))
    result = ToolRegistry().call(ctx, "run_suite", {}, "call-1")
    assert result.payload == {"ok": False, "error": "confirmation_declined",
                              "reason": "user_declined"}


def test_human_only_tools_ignore_auto_approval(tmp_path):
    project = _build_project(tmp_path)
    spec_before = (tmp_path / "specs" / "behavior.yaml").read_bytes()
    outcome = run_project(project)  # a real completed run for set_baseline
    path, sha = _write_agent_draft(tmp_path)
    ctx = _ctx(project, confirm=_auto_approve)
    for tool, args in (("replace_spec", {"draft_name": "d1", "draft_sha256": sha}),
                       ("set_baseline", {"run_id": outcome.run_id})):
        result = ToolRegistry().call(ctx, tool, args, "call-h")
        assert result.payload["error"] == "confirmation_declined", tool
        assert result.payload["reason"] == "human_required", tool
    assert (tmp_path / "specs" / "behavior.yaml").read_bytes() == spec_before
    assert project.store.get_baseline("t") is None
    assert project.store.get_run(outcome.run_id)["is_baseline"] is False


def test_unknown_tool_and_invalid_arguments(tmp_path):
    project = _build_project(tmp_path)
    ctx = _ctx(project)
    assert ToolRegistry().call(ctx, "nope", {}, "c").payload["error"] == "unknown_tool"
    result = ToolRegistry().call(ctx, "get_trace", {}, "c")
    assert result.payload["error"] == "invalid_arguments"
    assert any("test_case_id" in p for p in result.payload["problems"])
    result = ToolRegistry().call(ctx, "propose_spec", {"yaml": "x", "evil": 1}, "c")
    assert any("evil" in p for p in result.payload["problems"])
    result = ToolRegistry().call(ctx, "read_file", {"path": "agent.py"}, "c")
    assert result.payload["error"] == "source_access_disabled"
    assert "--allow-source" in result.payload["hint"]


def test_tool_output_is_redacted_and_capped(tmp_path):
    project = _build_project(tmp_path)
    ctx = _ctx(project)
    result = ToolRegistry().call(
        ctx, "propose_spec", {"yaml": "rules:\n  - id: A\n    title: API_KEY=sk-abcdefghijklmnop1234\n"}, "c")
    assert "[REDACTED]" in json.dumps(result.payload)
    huge = "rules:\n  - id: A\n    title: " + "x" * 20000 + "\n"
    result = ToolRegistry().call(ctx, "propose_spec", {"yaml": huge}, "c2")
    assert result.payload.get("truncated") is True


# -- propose_spec -------------------------------------------------------------------


def test_propose_spec_writes_marker_draft(tmp_path):
    project = _build_project(tmp_path)
    ctx = _ctx(project)
    draft = SPEC.replace("agent: T", "agent: Drafted").replace(
        "    approval_for: [refund]",
        "    approval_for: [refund]\n"
        "    constraints:\n"
        "      - type: max_calls\n"
        "        tool: refund\n"
        "        max: 1")
    result = ToolRegistry().call(ctx, "propose_spec", {"yaml": draft, "name": "d1"}, "c")
    assert result.payload["ok"] is True
    path = tmp_path / "specs" / "d1.draft.yaml"
    assert path.exists()
    assert path.read_text(encoding="utf-8").splitlines()[0] == DRAFT_MARKER
    assert len(result.payload["sha256"]) == 64
    # overwriting an agent draft is allowed; a user file is not
    result = ToolRegistry().call(ctx, "propose_spec", {"yaml": draft, "name": "d1"}, "c2")
    assert result.payload["ok"] is True
    (tmp_path / "specs" / "user.draft.yaml").write_text("agent: mine\n", encoding="utf-8")
    result = ToolRegistry().call(ctx, "propose_spec", {"yaml": draft, "name": "user"}, "c3")
    assert result.payload["error"] == "draft_exists_not_agent"
    assert (tmp_path / "specs" / "user.draft.yaml").read_text(encoding="utf-8") == "agent: mine\n"


def test_propose_spec_invalid_writes_nothing(tmp_path):
    project = _build_project(tmp_path)
    ctx = _ctx(project)
    result = ToolRegistry().call(ctx, "propose_spec", {"yaml": "rules:\n  - id: A\n    severity: ultra\n"}, "c")
    assert result.payload["error"] == "validation_failed"
    assert any("severity" in p for p in result.payload["problems"])
    assert list((tmp_path / "specs").glob("*.draft.yaml")) == []


# -- replace_spec hash binding (§8.2 test_replace_spec_is_hash_bound) ----------------


def _write_agent_draft(tmp_path, name="d1"):
    draft = DRAFT_MARKER + "\n" + SPEC.replace("agent: T", "agent: Drafted")
    path = tmp_path / "specs" / f"{name}.draft.yaml"
    path.write_text(draft, encoding="utf-8")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _park_replace(ctx, tmp_path):
    """Park a replace_spec action via a deferred confirmation; returns the action id."""
    path, sha = _write_agent_draft(tmp_path)
    decisions = iter([ConfirmResult("deferred")])
    ctx.confirm = lambda req: next(decisions)
    result = ToolRegistry().call(ctx, "replace_spec",
                                 {"draft_name": "d1", "draft_sha256": sha}, "call-1")
    assert result.pending is not None
    return result.pending.action_id


def test_replace_spec_is_hash_bound(tmp_path):
    project = _build_project(tmp_path)
    spec_path = tmp_path / "specs" / "behavior.yaml"
    original = spec_path.read_bytes()
    ctx = _ctx(project, confirm=_approve)
    registry = ToolRegistry()

    # (a) stale hash argument → stale_draft, no prompt ever shown
    prompts = []
    ctx.confirm = lambda req: prompts.append(req) or ConfirmResult("approved")
    result = registry.call(ctx, "replace_spec",
                           {"draft_name": "missing", "draft_sha256": "0" * 64}, "c")
    assert result.payload["error"] == "stale_draft" or result.payload["error"] == "draft_not_found"
    path, sha = _write_agent_draft(tmp_path)
    (path).write_text(path.read_text(encoding="utf-8").replace("Drafted", "Drafted2"),
                      encoding="utf-8")
    result = registry.call(ctx, "replace_spec",
                           {"draft_name": "d1", "draft_sha256": sha}, "c")
    assert result.payload["error"] == "stale_draft"
    assert prompts == []

    # (b) park, overwrite the draft, approve → stale_confirmation
    ctx.confirm = lambda req: ConfirmResult("approved")
    action = _park_replace(ctx, tmp_path)
    path.write_text(DRAFT_MARKER + "\n" + SPEC.replace("agent: T", "agent: Drafted3"),
                    encoding="utf-8")
    result = registry.execute_approved(ctx, action)
    assert result.payload["error"] == "stale_confirmation"
    assert result.payload["changed"] == ["draft"]
    assert spec_path.read_bytes() == original

    # (c) park, edit the real spec, approve → stale_confirmation
    action = _park_replace(ctx, tmp_path)
    spec_path.write_text(original.decode("utf-8") + "\n", encoding="utf-8")
    result = registry.execute_approved(ctx, action)
    assert result.payload["error"] == "stale_confirmation"
    assert result.payload["changed"] == ["spec"]

    # (d) unchanged → applied, backup present, spec reloaded fine
    spec_path.write_bytes(original)
    action = _park_replace(ctx, tmp_path)
    result = registry.execute_approved(ctx, action)
    assert result.payload["ok"] is True
    assert "bak-" in result.payload["backup"]
    assert "Drafted" in spec_path.read_text(encoding="utf-8")
    assert spec_path.read_text(encoding="utf-8").splitlines()[0] != DRAFT_MARKER
    assert load_spec_ok(spec_path)


def load_spec_ok(path):
    from app.spec_yaml import load_spec_file
    spec = load_spec_file(str(path))
    return spec.agent_name == "Drafted"


# -- set_baseline hash binding --------------------------------------------------------


def test_set_baseline_hash_bound(tmp_path):
    project = _build_project(tmp_path)
    outcome = run_project(project)  # a real completed run
    run_id = outcome.run_id
    ctx = _ctx(project, confirm=_approve)
    registry = ToolRegistry()
    result = registry.call(ctx, "set_baseline", {"run_id": run_id}, "c")
    assert result.payload["ok"] is True
    assert project.store.get_baseline("t")["id"] == run_id

    # run_not_completed refusal
    runs = project.store.list_runs("t")
    project.store.create_run(project_id="t", spec={"rules": []}, tests=[])
    incomplete = project.store.list_runs("t")[0]["id"]
    assert incomplete != run_id
    result = registry.call(ctx, "set_baseline", {"run_id": incomplete}, "c2")
    assert result.payload["error"] == "run_not_completed"

    # parked, then the run's stats change → stale_confirmation
    ctx2 = _ctx(project)
    decisions = iter([ConfirmResult("deferred")])
    ctx2.confirm = lambda req: next(decisions)
    result = registry.call(ctx2, "set_baseline", {"run_id": run_id}, "c3")
    action = result.pending.action_id
    project.store.complete_run(run_id, passed=0, failed=1, errors=0, canceled=0, total=1, score=0)
    result = registry.execute_approved(ctx2, action)
    assert result.payload["error"] == "stale_confirmation"
    assert result.payload["changed"] == ["run"]


# -- inspect_project surface -----------------------------------------------------------


def test_inspect_project_reports_backend_and_warnings(tmp_path):
    project = _build_project(tmp_path, config_text=CONFIG + "\n")
    ctx = _ctx(project)
    payload = ToolRegistry().call(ctx, "inspect_project", {}, "c").payload
    assert payload["ok"] is True
    assert payload["db_backend"] == "sqlite"
    assert "/" not in payload["db_backend"] and ":" not in payload["db_backend"]
    assert payload["gate_fail_on"] == ["critical", "high"]
    assert payload["latest_run_id"] is None
    spec_yaml_text = SPEC + "\nfuture_key: 1\n"
    (tmp_path / "specs" / "behavior.yaml").write_text(spec_yaml_text, encoding="utf-8")
    payload = ToolRegistry().call(ctx, "inspect_project", {}, "c2").payload
    assert any("future_key" in w for w in payload["validation_warnings"])
