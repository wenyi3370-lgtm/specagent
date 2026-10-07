"""Tool layer for the SpecAgent agent (v1 design §8.2, task 11).

Everything the agent may do is a :class:`ToolSpec` in :data:`REGISTRY`; the
single gate :meth:`ToolRegistry.call` validates arguments against the JSON
schema, enforces risk tiers and hash-bound confirmations, and redacts output.
The agent loop never invokes a handler directly, and confirm-tier handlers
start with :func:`_require_confirmed` so a direct call fails loudly.

Risk tiers: ``auto`` tools run without prompting; ``confirm`` tools go through
``prepare → confirm → recheck → act``; ``human_only`` confirm tools (replace_spec,
set_baseline) can never be approved by ``--yes`` — the registry treats an
``auto=True`` approval for them as ``declined/human_required`` (defense in depth
against a buggy callback).
"""
import hashlib
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from ..errors import SpecValidationError
from ..metrics import compute_project_metrics
from ..project import (Project, case_count, format_run_quote, run_project,
                       verify_project)
from ..regression import diff_runs
from ..spec_yaml import collect_spec_warnings, parse_spec
from ..trace import _redact
from .sandbox import ProjectSandbox, SandboxDenied, ensure_state_dir, redact_text

logger = logging.getLogger("specagent.agent.tools")

MAX_TOOL_OUTPUT_CHARS = 12_000
DRAFT_MARKER = "# specagent:agent-draft v1"
DRAFT_NAME_PATTERN = r"^[A-Za-z0-9_-]{1,40}$"

EFFECTS = {"read", "write_draft", "write_spec", "write_baseline", "write_suggestion",
           "run_suite"}


class ConfirmationRequired(RuntimeError):
    """Raised when a confirm-tier handler is invoked outside the registry gate."""


def _require_confirmed(ctx: "ToolContext") -> None:
    if not getattr(ctx, "_confirmed_sentinel", False):
        raise ConfirmationRequired("confirm-tier handler invoked outside the registry gate")


@dataclass(frozen=True)
class Prepared:
    summary: str
    preview: str
    bindings: dict[str, str]


@dataclass(frozen=True)
class ConfirmRequest:
    tool: str
    summary: str
    preview: str
    args: dict
    call_id: str
    human_only: bool


@dataclass(frozen=True)
class ConfirmResult:
    decision: str  # "approved" | "declined" | "deferred"
    reason: str = ""
    auto: bool = False


@dataclass
class PendingAction:
    action_id: str
    call_id: str
    tool: str
    args: dict
    prepared: Prepared | None
    human_only: bool


@dataclass
class ToolResult:
    payload: dict
    pending: PendingAction | None = None
    confirm: ConfirmResult | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict
    risk: str                       # "auto" | "confirm"
    human_only: bool                # only meaningful for risk="confirm"
    effects: frozenset[str]         # subset of EFFECTS
    requires: frozenset[str]        # e.g. {"allow_source"}
    prepare: Callable[["ToolContext", dict], "Prepared | dict"] | None = None
    handler: Callable[["ToolContext", dict, "Prepared | None"], dict] | None = None
    recheck: Callable[["ToolContext", dict, "Prepared"], dict | None] | None = None
    log_args: Callable[[dict], dict] | None = None


@dataclass
class ToolContext:
    project: Project
    sandbox: ProjectSandbox
    allow_source: bool = False
    confirm: Callable[[ConfirmRequest], ConfirmResult] | None = None
    transcript: Any = None          # loop.Transcript or None; needs .emit(type, data)
    last_run_id: str | None = None
    read_hashes: dict = field(default_factory=dict)   # rel path -> sha256 of full verbatim reads
    read_notes: dict = field(default_factory=dict)    # rel path -> why a read was NOT rewritable
    quotes: dict = field(default_factory=dict)        # latest deterministic quote per tool
    pending: dict = field(default_factory=dict)       # action_id -> PendingAction
    _confirmed_sentinel: bool = field(default=False, repr=False)


# -- argument schema validation (§8.2 gate step 2) -----------------------------


def _validate_value(key: str, value, schema: dict) -> list[str]:
    problems: list[str] = []
    kind = schema.get("type")
    if kind == "string":
        if not isinstance(value, str):
            return [f"argument '{key}' must be a string"]
        if "enum" in schema and value not in schema["enum"]:
            problems.append(f"argument '{key}' must be one of {schema['enum']}")
        if "pattern" in schema and not re_match(schema["pattern"], value):
            problems.append(f"argument '{key}' must match {schema['pattern']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            problems.append(f"argument '{key}' exceeds maxLength {schema['maxLength']}")
        if "minLength" in schema and len(value) < schema["minLength"]:
            problems.append(f"argument '{key}' is shorter than minLength {schema['minLength']}")
    elif kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return [f"argument '{key}' must be an integer"]
        if "minimum" in schema and value < schema["minimum"]:
            problems.append(f"argument '{key}' must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            problems.append(f"argument '{key}' must be <= {schema['maximum']}")
    elif kind == "boolean":
        if not isinstance(value, bool):
            return [f"argument '{key}' must be a boolean"]
    elif kind == "array":
        if not isinstance(value, list):
            return [f"argument '{key}' must be an array"]
        if "minItems" in schema and len(value) < schema["minItems"]:
            problems.append(f"argument '{key}' needs at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            problems.append(f"argument '{key}' allows at most {schema['maxItems']} items")
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(value[:20]):
                problems.extend(_validate_value(f"{key}[{i}]", item, item_schema))
    return problems


def re_match(pattern: str, value: str) -> bool:
    return re.match(pattern, value) is not None


def _validate_args(args: Any, schema: dict) -> list[str]:
    if not isinstance(args, dict):
        return ["arguments must be an object"]
    problems: list[str] = []
    properties = schema.get("properties", {})
    if schema.get("additionalProperties") is False:
        for key in args:
            if key not in properties:
                problems.append(f"unexpected argument '{key}'")
    for required in schema.get("required", []):
        if required not in args:
            problems.append(f"missing required argument '{required}'")
    for key, value in args.items():
        if key in properties:
            problems.extend(_validate_value(key, value, properties[key]))
    return problems


# -- output post-processing (§8.2 gate step 6) ----------------------------------


def _json_safe(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _postprocess(payload: dict) -> dict:
    """``trace._redact`` (secret-keyed dict values) + ``redact_text`` (every
    string), cap long strings, ensure JSON-serializable."""
    capped = {"value": False}

    def walk(value):
        if isinstance(value, str):
            redacted = redact_text(value)
            if len(redacted) > MAX_TOOL_OUTPUT_CHARS:
                capped["value"] = True
                redacted = redacted[:MAX_TOOL_OUTPUT_CHARS]
            return redacted
        if isinstance(value, dict):
            return {str(k): walk(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [walk(v) for v in value]
        return value

    out = walk(_json_safe(_redact(payload)))
    if capped["value"]:
        out["truncated"] = True
    return out


def _emit(ctx: "ToolContext", event_type: str, data: dict) -> None:
    if ctx.transcript is not None:
        ctx.transcript.emit(event_type, data)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _latest_run(ctx: "ToolContext") -> dict | None:
    runs = ctx.project.store.list_runs(ctx.project.project_id, limit=1)
    return ctx.project.store.get_run(runs[0]["id"]) if runs else None


def _run_summary(run: dict) -> dict:
    return {"id": run["id"], "status": run.get("status"), "passed": run.get("passed"),
            "failed": run.get("failed"), "errors": run.get("errors"),
            "canceled": run.get("canceled"), "total": run.get("total"),
            "is_baseline": run.get("is_baseline"), "label": run.get("label"),
            "agent": run.get("agent"), "started_at": run.get("started_at")}


# -- AUTO tools ------------------------------------------------------------------


def _h_inspect_project(ctx: "ToolContext", args: dict, _prepared) -> dict:
    project = ctx.project
    spec = project.spec
    try:
        raw = Path(project.spec_path).read_text(encoding="utf-8")
        warnings = collect_spec_warnings(yaml.safe_load(raw) or {})
    except OSError:
        warnings = ["spec file could not be read for warnings"]
    baseline = project.store.get_baseline(project.project_id)
    latest = _latest_run(ctx)
    return {
        "ok": True,
        "project_id": project.project_id,
        "adapter_type": project.adapter_type,
        "agent": project.agent_path,
        "spec_path": project.spec_path,
        "rules": len(spec.rules),
        "constraints": sum(len(r.constraints) for r in spec.rules),
        "probes": sum(len(r.probes) for r in spec.rules),
        "gate_fail_on": list(project.gate_fail_on),
        "baseline_run_id": baseline["id"] if baseline else None,
        "latest_run_id": latest["id"] if latest else None,
        "allow_source": ctx.allow_source,
        "db_backend": project.db_backend,
        "validation_warnings": warnings,
    }


def _h_list_rules(ctx: "ToolContext", args: dict, _prepared) -> dict:
    spec = ctx.project.spec
    rules = [{
        "id": r.id, "title": r.title, "severity": r.severity, "action": r.action,
        "condition": r.condition,
        "constraints": [c.type + (f" ({c.tool})" if getattr(c, "tool", None) else "")
                        for c in r.constraints],
        "probes": len(r.probes),
    } for r in spec.rules]
    return {"ok": True, "rules": rules}


def _h_get_run(ctx: "ToolContext", args: dict, _prepared) -> dict:
    run = ctx.project.store.get_run(args["run_id"]) if args.get("run_id") else _latest_run(ctx)
    if run is None:
        return {"ok": False, "error": "run_not_found"}
    failing = [{
        "id": r["test_case_id"], "rule_id": r.get("rule_id", ""), "status": r["status"],
        "violations": r.get("violations", []),
    } for r in run.get("results", []) if r.get("status") in ("FAIL", "FLAKY", "ERROR")][:50]
    return {"ok": True, "run": _run_summary(run), "failing_cases": failing}


def _h_get_diff(ctx: "ToolContext", args: dict, _prepared) -> dict:
    store = ctx.project.store
    candidate = (store.get_run(args["candidate"]) if args.get("candidate")
                 else _latest_run(ctx))
    if candidate is None:
        return {"ok": False, "error": "run_not_found"}
    baseline = (store.get_run(args["baseline"]) if args.get("baseline")
                else store.get_baseline(ctx.project.project_id))
    if baseline and baseline["id"] == candidate["id"]:
        baseline = None
    diff = diff_runs(baseline, candidate)
    entries = [e.model_dump() for e in diff.entries
               if e.diff_type != "STABLE_PASS"]
    if not args.get("include_traces"):
        for entry in entries:
            entry.pop("baseline_trace", None)
            entry.pop("candidate_trace", None)
    return {"ok": True, "counts": diff.counts(), "entries": entries[:50]}


def _h_get_trace(ctx: "ToolContext", args: dict, _prepared) -> dict:
    run = ctx.project.store.get_run(args["run_id"]) if args.get("run_id") else _latest_run(ctx)
    if run is None:
        return {"ok": False, "error": "run_not_found"}
    result = next((r for r in run.get("results", []) if r["test_case_id"] == args["test_case_id"]),
                  None)
    if result is None:
        return {"ok": False, "error": "case_not_found", "run_id": run["id"]}
    return {"ok": True, "run_id": run["id"], "test_case_id": args["test_case_id"],
            "status": result["status"], "violations": result.get("violations", []),
            "trace": result.get("trace", [])}


def _h_get_metrics(ctx: "ToolContext", args: dict, _prepared) -> dict:
    store = ctx.project.store
    runs = store.list_runs(ctx.project.project_id, limit=100)
    latest = store.get_run(runs[0]["id"]) if runs else None
    baseline = store.get_baseline(ctx.project.project_id)
    diff = None
    if baseline and latest and baseline["id"] != latest["id"]:
        diff = diff_runs(baseline, latest)
    return {"ok": True, "metrics": compute_project_metrics(runs, latest, diff)}


def _h_triage_run(ctx: "ToolContext", args: dict, _prepared) -> dict:
    from .triage import triage_run
    store = ctx.project.store
    run = store.get_run(args["run_id"]) if args.get("run_id") else _latest_run(ctx)
    if run is None:
        return {"ok": False, "error": "run_not_found"}
    baseline = store.get_baseline(ctx.project.project_id)
    diff = diff_runs(baseline, run) if baseline and baseline["id"] != run["id"] else None
    return {"ok": True, "report": triage_run(run, diff)}


def _h_read_file(ctx: "ToolContext", args: dict, _prepared) -> dict:
    result = ctx.sandbox.read_file(args["path"])
    if result.get("ok"):
        if result.get("rewritable"):
            ctx.read_hashes[result["path"]] = result["sha256"]
        else:
            ctx.read_notes.setdefault(result["path"],
                                      result.get("not_rewritable_reason") or "redacted")
    return result


def _h_propose_spec(ctx: "ToolContext", args: dict, _prepared) -> dict:
    """AUTO (write_draft): validate, then write `<spec dir>/<name>.draft.yaml`
    whose first line is the agent marker. An existing draft is overwritten only
    if it carries the marker (§8.2)."""
    text = args["yaml"]
    name = args.get("name") or Path(ctx.project.spec_path).stem
    try:
        spec = parse_spec(yaml.safe_load(text))
    except SpecValidationError as exc:
        return {"ok": False, "error": "validation_failed", "problems": exc.errors}
    spec_dir = Path(ctx.project.spec_path).parent
    draft_path = spec_dir / f"{name}.draft.yaml"
    if draft_path.exists():
        try:
            first_line = draft_path.read_text(encoding="utf-8").splitlines()[0].strip()
        except (OSError, IndexError):
            first_line = ""
        if first_line != DRAFT_MARKER:
            return {"ok": False, "error": "draft_exists_not_agent", "draft": draft_path.name}
    body = text if text.lstrip().startswith(DRAFT_MARKER) else f"{DRAFT_MARKER}\n{text}"
    if not body.endswith("\n"):
        body += "\n"
    # atomic temp-file + os.replace (write allowed in this named handler, §8.2);
    # bytes-exact so the returned sha256 matches the file on disk
    tmp = draft_path.with_name(draft_path.name + f".tmp-{uuid.uuid4().hex[:8]}")
    tmp.write_bytes(body.encode("utf-8"))
    os.replace(tmp, draft_path)
    try:
        real = Path(ctx.project.spec_path).read_text(encoding="utf-8")
    except OSError:
        real = ""
    import difflib
    diff_text = "\n".join(difflib.unified_diff(
        real.splitlines(), body.splitlines(),
        fromfile="a/spec", tofile=f"b/{draft_path.name}", lineterm=""))
    return {"ok": True, "draft": draft_path.name, "sha256": _sha256(body.encode("utf-8")),
            "rules": len(spec.rules),
            "constraints": sum(len(r.constraints) for r in spec.rules),
            "probes": sum(len(r.probes) for r in spec.rules),
            "diff": diff_text}


def _log_yaml_args(args: dict) -> dict:
    return {"name": args.get("name"),
            "sha256": _sha256(args.get("yaml", "").encode("utf-8")),
            "bytes": len(args.get("yaml", ""))}


# -- CONFIRM tools ----------------------------------------------------------------


def _bindings_of_project(ctx: "ToolContext") -> dict:
    return {"spec_sha256": _sha256(ctx.project.spec_bytes),
            "config_sha256": _sha256(ctx.project.config_bytes)}


def _prepare_run_suite(ctx: "ToolContext", args: dict) -> "Prepared | dict":
    spec = ctx.project.spec
    return Prepared(
        summary=(f"Run the behavior suite ({case_count(spec)} cases) against "
                 f"adapter '{ctx.project.adapter_type}'"
                 + (f" with label '{args['label']}'" if args.get("label") else "")),
        preview=("Executes the configured agent (code or network) and persists a run. "
                 "Never changes the baseline."),
        bindings=_bindings_of_project(ctx))


def _recheck_project_bindings(ctx: "ToolContext", args: dict, prepared: Prepared) -> dict | None:
    fresh = _bindings_of_project(ctx)
    changed = [k.replace("_sha256", "") for k, v in prepared.bindings.items() if fresh.get(k) != v]
    if changed:
        return {"ok": False, "error": "stale_confirmation", "changed": changed}
    return None


def _h_run_suite(ctx: "ToolContext", args: dict, _prepared) -> dict:
    _require_confirmed(ctx)
    outcome = run_project(ctx.project, label=args.get("label") or "", set_baseline=False)
    ctx.last_run_id = outcome.run_id
    quote = format_run_quote(outcome, ctx.project.gate_fail_on)
    ctx.quotes["run_suite"] = quote
    gate = [{"test_case_id": e.test_case_id, "rule_id": e.rule_id, "severity": e.severity,
             "violations": e.violations} for e in outcome.gate]
    return {"ok": True, "run_id": outcome.run_id, "stats": outcome.stats,
            "diff": outcome.diff.counts() if outcome.diff else None,
            "gate": gate, "quote": quote}


def _prepare_replace_spec(ctx: "ToolContext", args: dict) -> "Prepared | dict":
    spec_dir = Path(ctx.project.spec_path).parent
    draft_path = spec_dir / f"{args['draft_name']}.draft.yaml"
    if not draft_path.is_file():
        return {"ok": False, "error": "draft_not_found", "draft": draft_path.name}
    draft_bytes = draft_path.read_bytes()
    lines = draft_bytes.decode("utf-8", errors="replace").splitlines(keepends=True)
    if not lines or lines[0].strip() != DRAFT_MARKER:
        return {"ok": False, "error": "draft_not_agent", "draft": draft_path.name}
    draft_sha = _sha256(draft_bytes)
    if draft_sha != args["draft_sha256"]:
        return {"ok": False, "error": "stale_draft"}
    try:
        draft_spec = parse_spec(yaml.safe_load(draft_bytes.decode("utf-8")))
    except SpecValidationError as exc:
        return {"ok": False, "error": "validation_failed", "problems": exc.errors}
    real_bytes = ctx.project.spec_bytes
    import difflib
    preview = "\n".join(difflib.unified_diff(
        real_bytes.decode("utf-8", errors="replace").splitlines(),
        "".join(lines[1:]).splitlines(),
        fromfile="a/spec", tofile="b/spec", lineterm=""))
    return Prepared(
        summary=(f"Replace {Path(ctx.project.spec_path).name} with draft "
                 f"{draft_path.name} ({len(draft_spec.rules)} rules)"),
        preview=preview or "(no textual difference)",
        bindings={"draft_sha256": draft_sha, "spec_sha256": _sha256(real_bytes)})


def _recheck_replace_spec(ctx: "ToolContext", args: dict, prepared: Prepared) -> dict | None:
    spec_dir = Path(ctx.project.spec_path).parent
    draft_path = spec_dir / f"{args['draft_name']}.draft.yaml"
    changed = []
    if not draft_path.is_file() or _sha256(draft_path.read_bytes()) != prepared.bindings["draft_sha256"]:
        changed.append("draft")
    if _sha256(ctx.project.spec_bytes) != prepared.bindings["spec_sha256"]:
        changed.append("spec")
    if changed:
        return {"ok": False, "error": "stale_confirmation", "changed": changed}
    return None


def _h_replace_spec(ctx: "ToolContext", args: dict, prepared: Prepared) -> dict:
    _require_confirmed(ctx)
    spec_path = Path(ctx.project.spec_path)
    spec_dir = spec_path.parent
    draft_path = spec_dir / f"{args['draft_name']}.draft.yaml"
    real_bytes = spec_path.read_bytes()
    stamp = _utc_stamp()
    backup = spec_dir / f"{spec_path.name}.bak-{stamp}"
    draft_text = draft_path.read_text(encoding="utf-8")
    body = "\n".join(draft_text.splitlines()[1:]) + "\n"
    backup.write_bytes(real_bytes)
    tmp = spec_path.with_name(spec_path.name + f".tmp-{uuid.uuid4().hex[:8]}")
    tmp.write_bytes(body.encode("utf-8"))
    os.replace(tmp, spec_path)
    try:
        from ..spec_yaml import load_spec_file
        load_spec_file(str(spec_path))
    except SpecValidationError as exc:
        spec_path.write_bytes(real_bytes)  # restore
        return {"ok": False, "error": "tool_error",
                "detail": f"SpecValidationError: reloaded spec failed: {'; '.join(exc.errors)}"}
    return {"ok": True, "spec_path": str(spec_path), "backup": backup.name,
            "sha256": _sha256(body.encode("utf-8"))}


def _prepare_set_baseline(ctx: "ToolContext", args: dict) -> "Prepared | dict":
    run = ctx.project.store.get_run(args["run_id"])
    if run is None:
        return {"ok": False, "error": "run_not_found"}
    if run.get("status") != "completed":
        return {"ok": False, "error": "run_not_completed", "run_id": run["id"]}
    stats = {k: run.get(k) for k in ("status", "spec_id", "passed", "failed", "errors",
                                     "canceled", "total")}
    warning = ""
    if run.get("failed") or run.get("canceled"):
        warning = (f"\nwarning: this run has {run.get('failed')} failure(s) and "
                   f"{run.get('canceled')} canceled case(s)")
    return Prepared(
        summary=f"Set run {run['id']} as the project baseline",
        preview=(f"run {run['id']} · passed={run.get('passed')} failed={run.get('failed')} "
                 f"errors={run.get('errors')} canceled={run.get('canceled')} "
                 f"total={run.get('total')}{warning}"),
        bindings={"run_stats": json.dumps(stats, sort_keys=True)})


def _recheck_set_baseline(ctx: "ToolContext", args: dict, prepared: Prepared) -> dict | None:
    run = ctx.project.store.get_run(args["run_id"])
    if run is None:
        return {"ok": False, "error": "run_not_found"}
    stats = {k: run.get(k) for k in ("status", "spec_id", "passed", "failed", "errors",
                                     "canceled", "total")}
    if json.dumps(stats, sort_keys=True) != prepared.bindings["run_stats"]:
        return {"ok": False, "error": "stale_confirmation", "changed": ["run"]}
    return None


def _h_set_baseline(ctx: "ToolContext", args: dict, _prepared) -> dict:
    _require_confirmed(ctx)
    ctx.project.store.set_baseline(args["run_id"])
    return {"ok": True, "baseline": args["run_id"]}


def _utc_stamp() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")


# -- fix suggestions & verify (v1 design §8.8, task 15) ---------------------------


class _ToolPayloadError(Exception):
    """Carries a ready-made error payload out of a shared helper."""

    def __init__(self, payload: dict):
        super().__init__(payload.get("error", "tool_error"))
        self.payload = payload


def _latest_completed_non_verify_run_id(ctx: "ToolContext") -> str | None:
    store = ctx.project.store
    for row in store.list_runs(ctx.project.project_id, limit=50):
        if row.get("label", "").startswith("verify:"):
            continue
        if row.get("status") == "completed":
            return row["id"]
    return None


def _compute_fix_diffs(ctx: "ToolContext", changes: list) -> list[dict]:
    """Sandbox rule 7 (§8.3): every target must resolve, must not be the
    config/spec/a draft, and must have been fully read (sha256 unchanged)."""
    from . import fixes
    out = []
    protected = {os.path.normcase(os.path.abspath(ctx.project.spec_path)),
                 os.path.normcase(os.path.abspath(str(ctx.project.config_path)))}
    for change in changes:
        resolved = ctx.sandbox.resolve_read(change["path"])
        if isinstance(resolved, SandboxDenied):
            raise _ToolPayloadError({"ok": False, "error": "path_denied",
                                     "reason": resolved.reason})
        if os.path.normcase(str(resolved)) in protected or resolved.name.endswith(".draft.yaml"):
            raise _ToolPayloadError({
                "ok": False, "error": "path_denied", "reason": "protected_path",
                "hint": "fixes may never change the gate, the spec or agent drafts"})
        rel = os.path.relpath(str(resolved), os.path.realpath(str(ctx.project.root))).replace("\\", "/")
        current = resolved.read_bytes()
        sha_before = _sha256(current)
        if ctx.read_hashes.get(rel) != sha_before:
            reason = ctx.read_notes.get(rel) or (
                "changed_since_read" if rel in ctx.read_hashes else "not_read")
            raise _ToolPayloadError({
                "ok": False, "error": "file_not_fully_read", "reason": reason,
                "hint": "files over 20,000 chars or containing secret-looking "
                        "values cannot be rewritten by the agent"})
        try:
            diff_text = fixes.build_unified_diff(rel, current, change["new_content"])
        except Exception as exc:  # DiffError from the builder
            raise _ToolPayloadError({"ok": False, "error": str(exc)}) from None
        out.append({"rel": rel, "diff": diff_text, "sha_before": sha_before,
                    "sha_after": _sha256(change["new_content"].encode("utf-8"))})
    return out


def _prepare_write_fix_suggestion(ctx: "ToolContext", args: dict) -> "Prepared | dict":
    try:
        diffs = _compute_fix_diffs(ctx, args["changes"])
    except _ToolPayloadError as exc:
        return exc.payload
    pre_run_id = args.get("pre_fix_run_id") or ctx.last_run_id or \
        _latest_completed_non_verify_run_id(ctx)
    if pre_run_id is None:
        return {"ok": False, "error": "run_not_found",
                "detail": "no completed run to diff against; run the suite first"}
    diff_text = "\n".join(d["diff"] for d in diffs)
    return Prepared(
        summary=(f"Write fix suggestion for {args['rule_id']} touching "
                 f"{len(diffs)} file(s) — proposal only, no file is changed"),
        preview=diff_text[:20000],
        bindings={"diff_sha256": _sha256(diff_text.encode("utf-8"))})


def _recheck_write_fix_suggestion(ctx: "ToolContext", args: dict,
                                  prepared: Prepared) -> dict | None:
    try:
        diffs = _compute_fix_diffs(ctx, args["changes"])
    except _ToolPayloadError as exc:
        return exc.payload
    diff_text = "\n".join(d["diff"] for d in diffs)
    if _sha256(diff_text.encode("utf-8")) != prepared.bindings["diff_sha256"]:
        return {"ok": False, "error": "stale_confirmation", "changed": ["files"]}
    return None


def _h_write_fix_suggestion(ctx: "ToolContext", args: dict, _prepared) -> dict:
    _require_confirmed(ctx)
    from . import fixes
    import datetime
    diffs = _compute_fix_diffs(ctx, args["changes"])
    diff_text = "\n".join(d["diff"] for d in diffs)
    now = datetime.datetime.now(datetime.timezone.utc)
    record = {
        "id": fixes.suggestion_id(diff_text, now),
        "created_at": now.isoformat(),
        "pre_fix_run_id": args.get("pre_fix_run_id") or ctx.last_run_id
                          or _latest_completed_non_verify_run_id(ctx),
        "rule_id": args["rule_id"],
        "diagnosis": args["diagnosis"],
        "files": [{"path": d["rel"], "sha256_before": d["sha_before"],
                   "sha256_after": d["sha_after"]} for d in diffs],
        "diff_sha256": _sha256(diff_text.encode("utf-8")),
    }
    if not record["pre_fix_run_id"]:
        return {"ok": False, "error": "run_not_found"}
    # Writes are allowed only in this named handler (§8.2); nothing in the
    # project tree changes — suggestions live under .specagent/ only.
    state = ensure_state_dir(ctx.project.root)
    directory = state / "suggestions" / record["id"]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "suggestion.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    # newline="" keeps the diff byte-exact so git apply sees the same EOLs
    (directory / "fix.diff").write_text(diff_text, encoding="utf-8", newline="")
    return {"ok": True, "id": record["id"], "files": record["files"],
            "diff_sha256": record["diff_sha256"],
            "pre_fix_run_id": record["pre_fix_run_id"]}


def _load_suggestion_record(ctx: "ToolContext", suggestion_id: str) -> dict:
    path = (ctx.project.root / ".specagent" / "suggestions" / suggestion_id
            / "suggestion.json")
    if not path.is_file():
        raise _ToolPayloadError({"ok": False, "error": "suggestion_not_found",
                                 "detail": str(path)})
    return json.loads(path.read_text(encoding="utf-8"))


def _prepare_verify_fix(ctx: "ToolContext", args: dict) -> "Prepared | dict":
    from ..project import _choose_pre_fix_run
    suggestion = None
    if args.get("suggestion_id"):
        try:
            suggestion = _load_suggestion_record(ctx, args["suggestion_id"])
        except _ToolPayloadError as exc:
            return exc.payload
    try:
        pre = _choose_pre_fix_run(ctx.project, args.get("pre_run_id"), suggestion)
    except SpecValidationError as exc:
        return {"ok": False, "error": "no_pre_fix_run", "detail": "; ".join(exc.errors)}
    return Prepared(
        summary=(f"Re-run the suite against the current code and verify against "
                 f"pre-fix run {pre['id']}"),
        preview=("The current code on disk runs again; the pre-fix run stays the "
                 "diff baseline. The project baseline is never changed."),
        bindings=_bindings_of_project(ctx))


def _h_verify_fix(ctx: "ToolContext", args: dict, _prepared) -> dict:
    _require_confirmed(ctx)
    suggestion = None
    if args.get("suggestion_id"):
        suggestion = _load_suggestion_record(ctx, args["suggestion_id"])
    outcome = verify_project(ctx.project, pre_run_id=args.get("pre_run_id"),
                             suggestion=suggestion)
    ctx.quotes["verify_fix"] = outcome.quote
    ctx.last_run_id = outcome.run_id
    return {"ok": True, "pre_run_id": outcome.pre_run_id, "run_id": outcome.run_id,
            "verdict": outcome.verdict, "counts": outcome.counts,
            "spec_changed": outcome.spec_changed, "quote": outcome.quote}


# -- registry ----------------------------------------------------------------------


def _spec(name, description, parameters, risk, human_only, effects, requires=(),
          prepare=None, handler=None, recheck=None, log_args=None) -> ToolSpec:
    return ToolSpec(name=name, description=description, parameters=parameters,
                    risk=risk, human_only=human_only, effects=frozenset(effects),
                    requires=frozenset(requires), prepare=prepare, handler=handler,
                    recheck=recheck, log_args=log_args)


_EMPTY_OBJECT = {"type": "object", "properties": {}, "additionalProperties": False}

REGISTRY: dict[str, ToolSpec] = {
    "inspect_project": _spec(
        "inspect_project",
        "Project overview: id, adapter, spec path, rule/constraint/probe counts, "
        "gate severities, run ids, validation warnings, DB backend name.",
        _EMPTY_OBJECT, "auto", False, {"read"},
        handler=_h_inspect_project),
    "list_rules": _spec(
        "list_rules", "Rules with severity, constraints and probe counts.",
        _EMPTY_OBJECT, "auto", False, {"read"},
        handler=_h_list_rules),
    "get_run": _spec(
        "get_run", "Status counts and failing cases (≤ 50) for a run (default: latest).",
        {"type": "object", "additionalProperties": False, "properties": {
            "run_id": {"type": "string"}},
         }, "auto", False, {"read"},
        handler=_h_get_run),
    "get_diff": _spec(
        "get_diff", "Diff counts + non-stable entries between two runs "
        "(defaults: latest candidate vs project baseline).",
        {"type": "object", "additionalProperties": False, "properties": {
            "candidate": {"type": "string"},
            "baseline": {"type": "string"},
            "include_traces": {"type": "boolean"}},
         }, "auto", False, {"read"},
        handler=_h_get_diff),
    "get_trace": _spec(
        "get_trace", "Stored (already redacted) trace of one test case.",
        {"type": "object", "additionalProperties": False, "required": ["test_case_id"],
         "properties": {"test_case_id": {"type": "string"},
                        "run_id": {"type": "string"}}},
        "auto", False, {"read"},
        handler=_h_get_trace),
    "get_metrics": _spec(
        "get_metrics", "Project observability metrics.", _EMPTY_OBJECT,
        "auto", False, {"read"},
        handler=_h_get_metrics),
    "triage_run": _spec(
        "triage_run", "Deterministic triage of a run (default: latest): merged "
        "findings by category/tool/arg with hints. No LLM.",
        {"type": "object", "additionalProperties": False, "properties": {
            "run_id": {"type": "string"}}},
        "auto", False, {"read"},
        handler=_h_triage_run),
    "read_file": _spec(
        "read_file", "Read one project source file (requires --allow-source).",
        {"type": "object", "additionalProperties": False, "required": ["path"],
         "properties": {"path": {"type": "string", "maxLength": 500}}},
        "auto", False, {"read"}, requires={"allow_source"},
        handler=_h_read_file),
    "propose_spec": _spec(
        "propose_spec",
        "Validate a behavior spec YAML and write it as an agent draft "
        "(<spec dir>/<name>.draft.yaml). Nothing loads a draft automatically.",
        {"type": "object", "additionalProperties": False, "required": ["yaml"],
         "properties": {
             "yaml": {"type": "string", "maxLength": 100_000},
             "name": {"type": "string", "pattern": DRAFT_NAME_PATTERN}}},
        "auto", False, {"write_draft"},
        handler=_h_propose_spec, log_args=_log_yaml_args),
    "run_suite": _spec(
        "run_suite",
        "Execute the user's agent and persist a run (never sets a baseline); "
        "returns stats, diff and the gate verdict.",
        {"type": "object", "additionalProperties": False, "properties": {
            "label": {"type": "string", "maxLength": 100}}},
        "confirm", False, {"run_suite"},
        prepare=_prepare_run_suite, handler=_h_run_suite,
        recheck=_recheck_project_bindings),
    "replace_spec": _spec(
        "replace_spec",
        "Replace the project's behavior spec with an agent draft "
        "(hash-bound; needs a human).",
        {"type": "object", "additionalProperties": False,
         "required": ["draft_name", "draft_sha256"],
         "properties": {"draft_name": {"type": "string", "pattern": DRAFT_NAME_PATTERN},
                        "draft_sha256": {"type": "string", "pattern": r"^[0-9a-f]{64}$"}}},
        "confirm", True, {"write_spec"},
        prepare=_prepare_replace_spec, handler=_h_replace_spec,
        recheck=_recheck_replace_spec),
    "set_baseline": _spec(
        "set_baseline",
        "Set an existing completed run as the project baseline (needs a human).",
        {"type": "object", "additionalProperties": False, "required": ["run_id"],
         "properties": {"run_id": {"type": "string"}}},
        "confirm", True, {"write_baseline"},
        prepare=_prepare_set_baseline, handler=_h_set_baseline,
        recheck=_recheck_set_baseline),
    "write_fix_suggestion": _spec(
        "write_fix_suggestion",
        "Write a fix proposal (diff + diagnosis) under .specagent/suggestions/; "
        "never modifies any project file. Requires --allow-source.",
        {"type": "object", "additionalProperties": False,
         "required": ["rule_id", "diagnosis", "changes"],
         "properties": {
             "rule_id": {"type": "string", "maxLength": 100},
             "diagnosis": {"type": "string", "maxLength": 4000},
             "pre_fix_run_id": {"type": "string"},
             "changes": {"type": "array", "minItems": 1, "maxItems": 3,
                         "items": {"type": "object",
                                   "additionalProperties": False,
                                   "required": ["path", "new_content"],
                                   "properties": {
                                       "path": {"type": "string", "maxLength": 500},
                                       "new_content": {"type": "string",
                                                       "maxLength": 200_000}}}}}},
        "confirm", False, {"write_suggestion"}, requires={"allow_source"},
        prepare=_prepare_write_fix_suggestion, handler=_h_write_fix_suggestion,
        recheck=_recheck_write_fix_suggestion),
    "verify_fix": _spec(
        "verify_fix",
        "Re-run the suite against the current code and verify against the "
        "pre-fix run; returns the six-way verdict and the quote.",
        {"type": "object", "additionalProperties": False, "properties": {
            "pre_run_id": {"type": "string"},
            "suggestion_id": {"type": "string", "pattern": r"^fix_[0-9T]+_[0-9a-f]{6}$"}}},
        "confirm", False, {"run_suite"},
        prepare=_prepare_verify_fix, handler=_h_verify_fix,
        recheck=_recheck_project_bindings),
}


class ToolRegistry:
    """The single gate (§8.2): nothing else may invoke a handler."""

    def __init__(self, tools: dict[str, ToolSpec] | None = None):
        self._tools = dict(REGISTRY if tools is None else tools)

    @property
    def tools(self) -> dict[str, ToolSpec]:
        return dict(self._tools)

    def openai_schemas(self) -> list[dict]:
        """Flat Responses-API shape: ``name``/``parameters`` sit at the top level
        of the tool, not nested under a ``function`` key.

        The nested ``{"type": "function", "function": {...}}`` form belongs to
        Chat Completions. OpenAI tolerates it on /v1/responses, but DeepSeek's
        Responses endpoint rejects it with 422 "tools[0]: missing field `name`"
        (verified against deepseek-flash, 2026-10-06). The flat form is what the
        Responses API reference specifies and both providers accept it.
        """
        return [{"type": "function", "name": t.name, "description": t.description,
                 "parameters": t.parameters}
                for t in self._tools.values()]

    def call(self, ctx: ToolContext, name: str, args: dict, call_id: str) -> ToolResult:
        spec = self._tools.get(name)
        if spec is None:
            return ToolResult({"ok": False, "error": "unknown_tool"})
        _emit(ctx, "tool_call", {"tool": name, "call_id": call_id,
                                 "args": spec.log_args(args) if (spec.log_args and isinstance(args, dict)) else args})
        problems = _validate_args(args, spec.parameters)
        if problems:
            return self._finish(ctx, name, call_id,
                                {"ok": False, "error": "invalid_arguments", "problems": problems})
        if spec.requires and "allow_source" in spec.requires and not ctx.allow_source:
            return self._finish(ctx, name, call_id,
                                {"ok": False, "error": "source_access_disabled",
                                 "hint": "start with --allow-source or set agent.allow_source: true"})
        prepared = None
        if spec.risk == "confirm":
            prepared = spec.prepare(ctx, args) if spec.prepare else Prepared(
                summary=f"Confirm {name}", preview="", bindings={})
            if isinstance(prepared, dict):  # error payload, no prompt
                return self._finish(ctx, name, call_id, prepared)
            decision = ctx.confirm(ConfirmRequest(
                tool=name, summary=prepared.summary, preview=prepared.preview,
                args=spec.log_args(args) if (spec.log_args and isinstance(args, dict)) else args,
                call_id=call_id, human_only=spec.human_only))
            if spec.human_only and decision.auto:
                decision = ConfirmResult("declined", "human_required")
            _emit(ctx, "confirm", {"tool": name, "call_id": call_id,
                                   "decision": decision.decision,
                                   "reason": decision.reason, "auto": decision.auto})
            if decision.decision == "declined":
                return self._finish(ctx, name, call_id,
                                    {"ok": False, "error": "confirmation_declined",
                                     "reason": decision.reason})
            if decision.decision == "deferred":
                action_id = f"act-{uuid.uuid4().hex[:12]}"
                pending = PendingAction(action_id=action_id, call_id=call_id, tool=name,
                                        args=args, prepared=prepared,
                                        human_only=spec.human_only)
                ctx.pending[action_id] = pending
                return ToolResult({}, pending=pending)
        return self._execute(ctx, spec, args, prepared, call_id=call_id, approved_by="")

    def _finish(self, ctx, name, call_id, payload) -> ToolResult:
        result = _postprocess(payload)
        _emit(ctx, "tool_result", {"tool": name, "call_id": call_id,
                                   "ok": result.get("ok"), "payload": result})
        return ToolResult(result)

    def _execute(self, ctx: ToolContext, spec: ToolSpec, args: dict,
                 prepared: Prepared | None, call_id: str, approved_by: str) -> ToolResult:
        """Steps 5–6: recheck bindings, run the handler, post-process output."""
        if spec.risk == "confirm" and spec.recheck is not None and prepared is not None:
            problem = spec.recheck(ctx, args, prepared)
            if problem is not None:
                return self._finish(ctx, spec.name, call_id, problem)
        ctx._confirmed_sentinel = True
        try:
            payload = spec.handler(ctx, args, prepared)
        except ConfirmationRequired:
            raise
        except Exception as exc:  # noqa: BLE001 — a tool bug is a normal tool result
            logger.warning("tool %s failed: %s", spec.name, exc)
            logger.debug("tool %s traceback", spec.name, exc_info=True)
            payload = {"ok": False, "error": "tool_error",
                       "detail": f"{type(exc).__name__}: {exc}"}
        finally:
            ctx._confirmed_sentinel = False
        if approved_by:
            payload["approved_by"] = approved_by
        return self._finish(ctx, spec.name, call_id, payload)

    def execute_approved(self, ctx: ToolContext, action_id: str,
                         approved_by: str = "human") -> ToolResult:
        """Run a parked action through steps 5–6 after rechecking (§8.2)."""
        pending = ctx.pending.pop(action_id, None)
        if pending is None:
            return ToolResult({"ok": False, "error": "unknown_action"})
        spec = self._tools[pending.tool]
        return self._execute(ctx, spec, pending.args, pending.prepared,
                             call_id=pending.call_id, approved_by=approved_by)
