"""Standalone HTML report (roadmap §11.2 `specagent report --open`).

Renders a run + its baseline diff + metrics into one self-contained HTML file
(dark theme, no JavaScript, no server needed) so a failure can be shared or
attached to a PR discussion. Every failure shows Expected vs Actual with the
exact trace events — the §9.3 questions answered on a single page.
"""
import html
import json

from . import __version__
from .models import DiffSummary
from .trace_diff import new_call_indices


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


_CSS = """
body{margin:0;background:#0b0e14;color:#eef3ff;font:14px/1.55 Inter,ui-sans-serif,system-ui,"Segoe UI",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:36px 24px 80px}
h1{font-size:24px;letter-spacing:-.5px;margin:0 0 4px}
h2{font-size:17px;margin:26px 0 8px}
.muted{color:#8d98ad}.mono{font-family:ui-monospace,Menlo,monospace;font-size:12px}
.pill{border:1px solid #252d3d;border-radius:999px;padding:4px 10px;font-size:12px;color:#8d98ad;display:inline-block}
.pass{color:#55d187}.fail{color:#ff6b78}.warn{color:#f5c261}
.tiles{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin:18px 0}
.tile{background:#121722;border:1px solid #252d3d;border-radius:12px;padding:12px}
.tile b{display:block;font-size:20px}.tile span{color:#8d98ad;font-size:12px}
.card{background:#121722;border:1px solid #252d3d;border-radius:14px;padding:14px;margin-top:10px}
.card.newreg{border-color:#5b2833}
.row{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}
.tag{font-size:11px;padding:3px 7px;border-radius:7px;background:#171d2a;border:1px solid #252d3d;color:#8d98ad;white-space:nowrap}
.tag.critical{color:#ff6b78}.tag.high{color:#f5c261}
.trace{margin-top:8px;padding:8px 10px;background:#0d1119;border:1px solid #252d3d;border-radius:8px;font-family:ui-monospace,Menlo,monospace;font-size:12px;color:#cbd5e8}
.newcall{color:#ff6b78;font-weight:700}
.violation{margin-top:6px;color:#ff6b78}
.compare{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:8px}
.compare h4{margin:0 0 4px;font-size:12px;color:#8d98ad}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:#8d98ad;padding:6px 8px;border-bottom:1px solid #252d3d}
td{padding:7px 8px;border-bottom:1px solid #1c2330}
@media(max-width:900px){.tiles,.compare{grid-template-columns:1fr 1fr}}
"""

_STATUS_STYLE = {"PASS": "pass", "FAIL": "fail", "ERROR": "warn", "FLAKY": "warn", "CANCELED": "warn"}


def _trace_lines(events, highlight=None) -> str:
    normalized = []
    for e in events or []:
        normalized.append(e.model_dump() if hasattr(e, "model_dump") else e)
    calls = [e for e in normalized if e.get("type") == "tool_call"]
    if not calls:
        return '<div class="trace">→ no tool call</div>'
    lines = []
    for i, e in enumerate(calls):
        args = ", ".join(f"{esc(k)}={esc(v)}" for k, v in (e.get("args") or {}).items())
        label = f"→ {e.get('name')}({args})"
        if highlight and i in highlight:
            lines.append(f'<div class="newcall">{esc(label)} ◀ new</div>')
        else:
            lines.append(f"<div>{esc(label)}</div>")
    return f'<div class="trace">{"".join(lines)}</div>'


def _diff_section(diff: DiffSummary | None) -> str:
    if diff is None:
        return ("<h2>Regression diff</h2>"
                '<p class="muted">No baseline for this project yet — record one with '
                "<code>specagent run --set-baseline</code>.</p>")
    counters = "".join(
        f'<div class="tile"><b>{getattr(diff, key)}</b><span>{label}</span></div>'
        for key, label in [
            ("new_regressions", "New regressions"), ("fixed", "Fixed"),
            ("persistent_fail", "Persistent"), ("stable_pass", "Stable"),
            ("flaky", "Flaky"), ("canceled", "Canceled"),
        ])
    entries = []
    for e in diff.entries:
        if e.diff_type == "STABLE_PASS":
            continue
        is_new = e.diff_type == "NEW_REGRESSION"
        added = new_call_indices(e.baseline_trace, e.candidate_trace)
        violations = "".join(f'<div class="violation">⚠ {esc(v)}</div>' for v in e.violations)
        entries.append(
            f'<div class="card{" newreg" if is_new else ""}">'
            f'<div class="row"><b>{esc(e.test_case_id)}</b>'
            f'<span><span class="tag {esc(e.severity)}">{esc(e.severity)}</span>'
            f'<span class="tag">{esc(e.diff_type)}</span></span></div>'
            f'<div style="margin-top:6px">{esc(e.input)}</div>{violations}'
            f'<div class="compare"><div><h4>Baseline ({esc(e.baseline_status or "—")})</h4>'
            f'{_trace_lines(e.baseline_trace)}'
            f'<div><h4 style="margin-top:8px">Candidate ({esc(e.candidate_status)})</h4>'
            f'{_trace_lines(e.candidate_trace, added)}</div></div></div>'
        )
    return (f'<h2>Regression diff <span class="muted">vs {esc(diff.baseline_run_id)}</span></h2>'
            f'<div class="tiles" style="grid-template-columns:repeat(6,1fr)">{counters}</div>'
            + "".join(entries))


def _cases_section(results, tests) -> str:
    by_id = {t.get("id"): t for t in tests}
    failing = [r for r in results if r.get("status") != "PASS"]
    passing = [r for r in results if r.get("status") == "PASS"]
    cards = []
    for r in failing + passing:
        case = by_id.get(r.get("test_case_id"), {})
        status = r.get("status", "?")
        style = _STATUS_STYLE.get(status, "fail")
        violations = "".join(f'<div class="violation">⚠ {esc(v)}</div>' for v in r.get("violations", []))
        llm = r.get("llm_verdict")
        llm_html = ""
        if llm:
            if llm.get("skipped"):
                llm_html = f'<div class="muted" style="margin-top:6px">🤖 LLM judge: skipped — {esc(llm.get("skip_reason"))}</div>'
            else:
                icon = {"pass": "✅", "fail": "❌"}.get(llm.get("verdict"), "❓")
                llm_html = (f'<div style="margin-top:6px">🤖 LLM judge: {icon} <b>{esc(llm.get("verdict"))}</b> '
                            f'({int(round((llm.get("confidence") or 0) * 100))}%) {esc(llm.get("reason"))} '
                            f'<span class="mono">[{esc(", ".join(llm.get("evidence_event_ids") or []))}]</span></div>')
        review = r.get("review")
        review_html = f'<div style="margin-top:6px">👤 人工复核: <b>{esc(review.get("verdict"))}</b> · {esc(review.get("reviewer"))}</div>' if review else ""
        error_html = f'<div class="violation">⚠ {esc(r.get("error"))}</div>' if r.get("error") else ""
        cards.append(
            f'<div class="card"><div class="row"><b>{esc(r.get("test_case_id"))}</b>'
            f'<span><span class="tag">{esc(case.get("category", ""))}</span>'
            f'<b class="{style}">{esc(status)}</b></span></div>'
            f'<div style="margin-top:6px">{esc(case.get("user_input", ""))}</div>'
            f'{_trace_lines(r.get("trace"))}{violations}{error_html}{llm_html}{review_html}</div>'
        )
    return f'<h2>Cases ({len(passing)} passed / {len(results)})</h2>' + "".join(cards)


def build_html_report(run: dict, diff: DiffSummary | None, metrics: dict) -> str:
    tiles = "".join(
        f'<div class="tile"><b>{value}</b><span>{label}</span></div>' for label, value in [
            ("Behavior score", f'{run.get("score", 0)}%'),
            ("Passed", run.get("passed", 0)),
            ("Failed", run.get("failed", 0)),
            ("Errors", run.get("errors", 0)),
            ("Canceled", run.get("canceled", 0)),
            ("New regressions", metrics.get("new_regression_count", 0) if diff else "—"),
        ])
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>SpecAgent report · {esc(run.get('id'))}</title><style>{_CSS}</style></head>
<body><div class="wrap">
<h1>SpecAgent Behavior Report</h1>
<div class="muted">run <span class="mono">{esc(run.get('id'))}</span> · project <span class="mono">{esc(run.get('project_id'))}</span>
 · agent <span class="mono">{esc(run.get('agent'))}</span> · started {esc(run.get('started_at'))}</div>
<div style="margin-top:8px"><span class="pill">{esc(run.get('status'))}</span>
 <span class="pill">{esc(run.get('spec_compiler'))}</span>
 {f'<span class="pill">{esc(run.get("label"))}</span>' if run.get('label') else ''}</div>
<div class="tiles">{tiles}</div>
{_diff_section(diff)}
{_cases_section(run.get('results', []), run.get('tests', []))}
<p class="muted mono" style="margin-top:30px">generated by SpecAgent {esc(__version__)}</p>
</div></body></html>"""
