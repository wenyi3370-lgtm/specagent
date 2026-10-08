"""Evidence-based failure categories, with no speculative model diagnosis."""
from .models import TestResult
from .violations import parse_violation


def attribute_failure(result: TestResult) -> list[dict]:
    if result.status == "ERROR":
        return [{"category": "execution", "kind": "execution_error", "tool": None,
                 "arg": None, "evidence_event_ids": []}]
    out = []
    events = result.execution.trace
    ids = {e.id or f"evt_{i + 1}" for i, e in enumerate(events)}
    for text in result.violations:
        parsed = parse_violation(text)
        category = ("input_context" if parsed.kind == "context_missing" else
                    "evaluator" if parsed.kind == "evaluation_error" else
                    "unknown" if parsed.kind == "other" else "policy")
        evidence = [eid for eid in parsed.evidence if eid in ids]
        if not parsed.evidence:
            evidence = [e.id or f"evt_{i + 1}" for i, e in enumerate(events)
                        if e.type == "tool_call" and e.name == parsed.tool]
        out.append({"category": category, "kind": parsed.kind, "tool": parsed.tool,
                    "arg": parsed.arg, "evidence_event_ids": evidence[:5]})
    return out
