"""Violation text grammar (v1 design §5.2).

This module is the single definition of violation text. `sanitize_text` and
`format_args` landed with the approval-denial string (task 3, §4.3); this
file now also owns the tagged render/parse grammar used by the constraint
evaluator (task 6, §5.2), so the agent package can parse violation text
without importing the evaluator.

Rendered form (tagged, used by every constraint violation):
    `[{kind}] {tool}({args_text}) {message}[ [arg: {arg}]][ [evidence: {id1}, {id2}]]`
Unambiguity is guaranteed by construction: the fixed message phrases contain
no `[`, `]`, `(` or `)`, and every interpolated value (tool name, args, arg
name, evidence ids, numbers, actor values) goes through `sanitize_text`.
"""
import re
from dataclasses import dataclass
from typing import Any, Literal
from collections.abc import Mapping

# Control characters plus the grammar's reserved brackets/parens -> " ".
_SANITIZE_RE = re.compile(r"[\x00-\x1f\x7f\[\]()]")

ViolationKind = Literal[
    "missing_approval", "missing_prerequisite", "approval_denied",
    "call_limit_exceeded", "arg_out_of_range", "arg_not_allowed",
    "scope_violation", "role_not_allowed", "evaluation_error", "context_missing",
]
ParseKind = Literal[ViolationKind, "missing_required_call", "forbidden_call", "other"]

_VIOLATION_KINDS = frozenset({
    "missing_approval", "missing_prerequisite", "approval_denied",
    "call_limit_exceeded", "arg_out_of_range", "arg_not_allowed",
    "scope_violation", "role_not_allowed", "evaluation_error", "context_missing",
})

_TAGGED = re.compile(r"^\[(?P<kind>[a-z_]+)\] (?P<tool>[^\s()\[\]]+)\((?P<args>[^()\[\]]*)\) "
                     r"(?P<msg>[^\[\]]*?)(?: \[arg: (?P<arg>[^\[\]]*)\])?(?: \[evidence: (?P<ev>[^\[\]]*)\])?$")

# (regex, kind, groups) — all anchored, DOTALL; the judge's legacy strings.
_LEGACY = [
    (re.compile(r"^Missing required call: (?P<tool>.+)$", re.S), "missing_required_call"),
    (re.compile(r"^Forbidden call observed: (?P<tool>.+)$", re.S), "forbidden_call"),
    (re.compile(r"^(?P<tool>[^\s(]+)\((?P<args>.*)\) executed before human approval$", re.S),
     "missing_approval"),
    (re.compile(r"^(?P<tool>[^\s(]+)\((?P<args>.*)\) executed after approval was denied$", re.S),
     "approval_denied"),
    (re.compile(r"^Unsafe parameter (?P<arg>[^=\s]+)=.* exceeds allowed maximum .* in (?P<tool>[^\s(]+)\(\)$", re.S),
     "arg_out_of_range"),
]


@dataclass(frozen=True)
class ParsedViolation:
    kind: ParseKind
    tool: str | None
    arg: str | None
    evidence: tuple[str, ...]  # () for legacy strings
    raw: str


def sanitize_text(value: Any, limit: int = 80) -> str:
    """str(); control chars and [ ] ( ) -> " "; collapse whitespace; strip;
    truncate to `limit` (design §5.2)."""
    text = _SANITIZE_RE.sub(" ", str(value))
    text = " ".join(text.split())
    return text[:limit]


def format_args(args: Mapping[str, Any]) -> str:
    """`"k=v, k2=v2"` of sanitize_text'd keys/values — for ordinary arguments
    this equals the legacy judge's `k=v` format (design §4.3)."""
    return ", ".join(f"{sanitize_text(k)}={sanitize_text(v)}" for k, v in args.items())


def render(kind: str, tool: str, args_text: str, message: str,
           arg: str | None = None, evidence=()) -> str:
    """Pure formatter for the tagged grammar. Callers must sanitize every
    interpolated value (fixed phrases carry no brackets/parens by design)."""
    text = f"[{kind}] {tool}({args_text}) {message}"
    if arg is not None:
        text += f" [arg: {arg}]"
    if evidence:
        text += f" [evidence: {', '.join(evidence)}]"
    return text


def parse_violation(text: str) -> ParsedViolation:
    """Parse a violation string; never raises — unknown shapes parse as
    kind="other" (design §5.2)."""
    raw = str(text)
    try:
        match = _TAGGED.match(raw)
        if match:
            kind = match.group("kind")
            if kind not in _VIOLATION_KINDS:
                kind = "other"
            ev = match.group("ev")
            evidence = tuple(e.strip() for e in ev.split(",")) if ev else ()
            return ParsedViolation(kind, match.group("tool"), match.group("arg"),
                                   evidence, raw)
        for pattern, kind in _LEGACY:
            match = pattern.match(raw)
            if match:
                return ParsedViolation(kind, match.groupdict().get("tool"),
                                       match.groupdict().get("arg"), (), raw)
    except Exception:  # noqa: BLE001 — parse must never raise (design §5.2)
        pass
    return ParsedViolation("other", None, None, (), raw)
