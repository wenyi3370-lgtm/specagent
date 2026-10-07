"""Deterministic triage (v1 design §8.5, task 12) — pure, no LLM, no I/O.

The only app import is ``app.violations.parse_violation`` (owned by task 6):
the agent layer parses violation *text* without touching the evaluator or the
judge. Findings are merged per rule by ``(category, tool, arg)``; hints come
from a constant template table.
"""
from app.violations import parse_violation

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

# Fixed hint templates (§8.5); no model involved.
HINTS = {
    "missing_approval": "`{tool}` ran without the required human approval call before it. "
                        "Request approval first and wait for an explicit result.",
    "approval_denied": "`{tool}` ran after the approval channel answered approved:false. "
                       "Check the approval result before executing and treat a denial as terminal.",
    "scope_violation": "`{tool}` received `{arg}` that does not match the acting user's identity. "
                       "Take the account from the authenticated actor, never from the message.",
    "call_limit_exceeded": "`{tool}` was called more often than the rule allows. "
                           "Enforce the per-message call budget before executing.",
    "role_not_allowed": "`{tool}` requires a role the acting user does not hold. "
                        "Check actor.role before executing.",
    "arg_out_of_range": "`{tool}` received `{arg}` outside the allowed range. "
                        "Validate numeric bounds before executing.",
    "arg_not_allowed": "`{tool}` received `{arg}` with a value outside the allowed set. "
                       "Validate against the whitelist before executing.",
    "forbidden_call": "`{tool}` must never be called by this rule. Refuse the request instead.",
    "missing_required_call": "The expected `{tool}` call never happened. "
                             "Make the required step part of the flow.",
    "missing_prerequisite": "`{tool}` ran before its required prerequisite. Enforce the ordering.",
    "evaluation_error": "The deterministic evaluator failed on `{tool}` — this is a SpecAgent "
                        "bug, not an agent failure; report it.",
    "other": "Unrecognized violation text mentioning `{tool}` — read the example and judge manually.",
}


def _hint(category: str, tool: str | None, arg: str | None) -> str:
    template = HINTS.get(category, HINTS["other"])
    return template.format(tool=tool or "the tool", arg=arg or "the value")


def _evidence_for(parsed, trace: list[dict]) -> list[str]:
    """Tagged violations carry their evidence ids; for legacy/plain strings use
    the ids of the stored trace's tool_call events named `tool` (max 3)."""
    if parsed.evidence:
        return list(parsed.evidence)[:5]
    calls = [e for e in trace or [] if e.get("type") == "tool_call"]
    return [(e.get("id") or f"evt_{i + 1}") for i, e in enumerate(calls)
            if parsed.tool and e.get("name") == parsed.tool][:3]


def triage_run(run: dict, diff=None) -> dict:
    """Triage one stored run (as returned by ``Store.get_run``)."""
    tests = {t["id"]: t for t in run.get("tests", [])}
    counts = {"pass": 0, "fail": 0, "flaky": 0, "error": 0, "canceled": 0}
    rule_stats: dict[str, dict] = {}
    merged: dict[tuple, dict] = {}
    errors: list[dict] = []

    for result in run.get("results", []):
        status = result.get("status")
        case_id = result["test_case_id"]
        rule_id = result.get("rule_id") or tests.get(case_id, {}).get("rule_id", "")
        counts_key = {"PASS": "pass", "FAIL": "fail", "FLAKY": "flaky",
                      "ERROR": "error", "CANCELED": "canceled"}.get(status)
        if counts_key:
            counts[counts_key] += 1
        stats = rule_stats.setdefault(rule_id, {"failing": [], "total": 0})
        stats["total"] += 1
        if status in ("FAIL", "FLAKY"):
            stats["failing"].append(case_id)
            for violation in result.get("violations", []):
                parsed = parse_violation(violation)  # never raises
                key = (rule_id, parsed.kind, parsed.tool, parsed.arg)
                entry = merged.setdefault(key, {"cases": [], "evidence": [],
                                                "example": violation})
                if case_id not in entry["cases"]:
                    entry["cases"].append(case_id)
                if not entry["evidence"]:
                    entry["evidence"] = _evidence_for(parsed, result.get("trace", []))
        elif status == "ERROR":
            # ERROR is not a behavior violation — listed separately, never merged
            errors.append({"case": case_id, "rule_id": rule_id,
                           "message": result.get("response") or "execution error"})

    diff_by_rule: dict[str, dict] = {}
    if diff is not None:
        for entry in diff.entries:
            if entry.diff_type == "STABLE_PASS":
                continue
            per_rule = diff_by_rule.setdefault(entry.rule_id, {})
            per_rule[entry.diff_type] = per_rule.get(entry.diff_type, 0) + 1

    spec_rules = {r.get("id"): r for r in run.get("spec", {}).get("rules", [])}
    rules_out = []
    for rule_id, stats in rule_stats.items():
        if not stats["failing"]:
            continue
        findings = []
        for (rid, category, tool, arg), entry in merged.items():
            if rid != rule_id:
                continue
            findings.append({
                "category": category, "tool": tool, "arg": arg,
                "count": len(entry["cases"]), "cases": entry["cases"][:5],
                "evidence_event_ids": entry["evidence"][:5],
                "example_violation": entry["example"],
                "hint": _hint(category, tool, arg),
            })
        spec_rule = spec_rules.get(rule_id, {})
        rules_out.append({
            "rule_id": rule_id,
            "title": spec_rule.get("title", ""),
            "severity": spec_rule.get("severity", "medium"),
            "failing": len(stats["failing"]),
            "total": stats["total"],
            "diff": diff_by_rule.get(rule_id),
            "findings": findings,
        })
    rules_out.sort(key=lambda r: (_SEVERITY_ORDER.get(r["severity"], 9),
                                  -r["failing"], r["rule_id"]))

    total = len(run.get("results", []))
    failing_cases = counts["fail"] + counts["flaky"]
    if rules_out:
        severity_counts: dict[str, int] = {}
        for rule in rules_out:
            severity_counts[rule["severity"]] = severity_counts.get(rule["severity"], 0) + 1
        parts = [f"{severity} ×{n}" for severity in
                 sorted(severity_counts, key=lambda s: _SEVERITY_ORDER.get(s, 9))
                 for n in [severity_counts[severity]] if n]
        summary = (f"{len(rules_out)} rules failing ({', '.join(parts)}) — "
                   f"{failing_cases} of {total} cases")
    else:
        summary = f"All {total} cases passed; nothing to triage."

    return {
        "run_id": run.get("id"),
        "project_id": run.get("project_id"),
        "counts": counts,
        "summary": summary,
        "rules": rules_out,
        "errors": errors,
    }
