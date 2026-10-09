"""CI helpers for the reusable GitHub Action (v1 design §9.1, task 17).

The composite `action.yml` stays thin: the decisions live here so they can be
unit-tested without GitHub.

    python -m app.ci mode --event push --ref refs/heads/main --default-branch main
    python -m app.ci comment --result result.json --out comment.md
    python -m app.ci run-id --result result.json

Deterministic only: no LLM, no network, no agent layer.
"""
import argparse
import json
import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger("specagent.ci")

MODES = ("baseline", "candidate")
OVERRIDES = ("auto",) + MODES
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
MAX_COMMENT_ENTRIES = 5
MAX_VIOLATIONS_PER_ENTRY = 2


def decide_mode(event: str, ref: str, default_branch: str, override: str = "auto") -> str:
    """`baseline` for a push to the default branch, otherwise `candidate`.

    An explicit override (`baseline` / `candidate`) always wins, which keeps the
    action's self-test independent of the triggering event.
    """
    if override not in OVERRIDES:
        raise ValueError(f"override must be one of {', '.join(OVERRIDES)}; got {override!r}")
    if override != "auto":
        return override
    if event == "push" and ref == f"refs/heads/{default_branch}":
        return "baseline"
    return "candidate"


def load_result(text: str) -> dict:
    """Parse `specagent run --json` output.

    The CLI prints a few warning lines before the JSON document in some
    situations, so parsing starts at the first line that is exactly `{`.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    offset = 0
    for line in text.splitlines(keepends=True):
        if line.strip() == "{":
            try:
                payload, _ = json.JSONDecoder().raw_decode(text[offset:])
                return payload
            except json.JSONDecodeError:
                break
        offset += len(line)
    raise ValueError("no JSON document found in run output")


def _md(value: object, limit: int = 160) -> str:
    """Make agent-produced text safe for a PR comment: one line, no code-span
    breakers, no HTML, no @-mentions."""
    text = re.sub(r"\s+", " ", str(value)).strip()
    text = text.replace("`", "'").replace("<", "‹").replace(">", "›").replace("@", "＠")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_comment(payload: dict) -> str:
    """PR comment (counts, gate verdict, top NEW_REGRESSION entries)."""
    run = payload.get("run") or {}
    diff = payload.get("diff")
    gate = payload.get("gate") or {}
    violations = gate.get("violations") or []
    fail_on = gate.get("fail_on") or []
    lines = ["## SpecAgent Behavior Check", ""]
    if not diff and not violations:
        lines.append("First run for this project: no baseline to diff against yet.")
        return "\n".join(lines) + "\n"
    blocked = bool(violations) or gate.get("exit_code") == 1
    if blocked:
        if any(e.get('candidate_status') in {'ERROR', 'FLAKY'} for e in violations):
            errors = sum(e.get('candidate_status') == 'ERROR' for e in violations)
            flaky = sum(e.get('candidate_status') == 'FLAKY' for e in violations)
            lines.append(f'**Gate: FAILED** · {len(violations) - errors - flaky} behavior regression(s), '
                         f'{errors} execution error(s), {flaky} flaky case(s)')
        else:
            lines.append(f"**Gate: FAILED** · {len(violations)} new regression(s) at or above "
                         f"`{_md(', '.join(fail_on))}`")
    else:
        lines.append("**Gate: PASSED**")
    if not diff:
        lines.append('First run: quality checks apply even without a baseline.')
        return '\n'.join(lines) + '\n'
    lines.append("")
    lines.append("| New regressions | Fixed | Persistent fail | Flaky | Stable pass |")
    lines.append("|---:|---:|---:|---:|---:|")
    lines.append(f"| {diff.get('new_regressions', 0)} | {diff.get('fixed', 0)} | "
                 f"{diff.get('persistent_fail', 0)} | {diff.get('flaky', 0)} | "
                 f"{diff.get('stable_pass', 0)} |")
    regressions = sorted(
        (e for e in diff.get("entries") or [] if e.get("diff_type") == "NEW_REGRESSION"),
        key=lambda e: (_SEVERITY_ORDER.get(e.get("severity"), 9), e.get("test_case_id", "")),
    )
    if regressions:
        lines += ["", "### Top new regressions", ""]
        for entry in regressions[:MAX_COMMENT_ENTRIES]:
            lines.append(f"- **{_md(entry.get('severity', '?'))}** `{_md(entry.get('test_case_id', ''))}`"
                         + (f" (rule `{_md(entry['rule_id'])}`)" if entry.get("rule_id") else ""))
            for text in (entry.get("violations") or [])[:MAX_VIOLATIONS_PER_ENTRY]:
                lines.append(f"  - {_md(text)}")
        if len(regressions) > MAX_COMMENT_ENTRIES:
            lines.append(f"- … and {len(regressions) - MAX_COMMENT_ENTRIES} more (see the uploaded report)")
    if run.get("id"):
        lines += ["", f"Run `{_md(run['id'])}`"]
    return "\n".join(lines) + "\n"


def extract_run_id(text: str) -> str | None:
    """Run id from either `run --json` output or the plain-text `Run id: …` line."""
    try:
        run_id = (load_result(text).get("run") or {}).get("id")
        if run_id:
            return str(run_id)
    except ValueError:
        pass
    match = re.search(r"^Run id: (\S+)\s*$", text, re.M)
    return match.group(1) if match else None


def _cmd_run_id(args) -> int:
    try:
        run_id = extract_run_id(Path(args.result).read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"cannot read run output: {exc}", file=sys.stderr)
        return 2
    if not run_id:
        print("no run id found in run output", file=sys.stderr)
        return 2
    print(run_id)
    return 0


def _cmd_mode(args) -> int:
    try:
        print(decide_mode(args.event, args.ref, args.default_branch, args.override))
    except ValueError as exc:
        print(f"Invalid configuration:\n  x {exc}", file=sys.stderr)
        return 2
    return 0


def _cmd_comment(args) -> int:
    try:
        payload = load_result(Path(args.result).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"cannot render comment: {exc}", file=sys.stderr)
        return 2
    Path(args.out).write_text(render_comment(payload), encoding="utf-8")
    return 0


def _cmd_quality_blocked(args) -> int:
    try:
        payload = load_result(Path(args.result).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        print(f'cannot inspect gate result: {exc}', file=sys.stderr)
        return 2
    blocked = any(e.get('candidate_status') in {'ERROR', 'FLAKY'}
                  for e in (payload.get('gate') or {}).get('violations', []))
    print('true' if blocked else 'false')
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.ci", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("mode", help="print baseline|candidate for this CI event")
    p.add_argument("--event", required=True)
    p.add_argument("--ref", default="")
    p.add_argument("--default-branch", default="main")
    p.add_argument("--override", default="auto")
    p.set_defaults(func=_cmd_mode)
    p = sub.add_parser("run-id", help="print the run id found in `specagent run` output")
    p.add_argument("--result", required=True)
    p.set_defaults(func=_cmd_run_id)
    p = sub.add_parser("comment", help="render the PR comment from `run --json` output")
    p.add_argument("--result", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=_cmd_comment)
    p = sub.add_parser('quality-blocked', help='print whether ERROR/FLAKY blocks this run')
    p.add_argument('--result', required=True)
    p.set_defaults(func=_cmd_quality_blocked)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
