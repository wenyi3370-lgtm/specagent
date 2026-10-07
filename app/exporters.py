"""Report serialization shared by CLI exports and authenticated downloads."""
import json
from xml.sax.saxutils import escape


def build_junit(run: dict, diff: dict | None) -> str:
    diff_types = {e["test_case_id"]: e["diff_type"] for e in (diff or {}).get("entries", [])}
    failures = sum(1 for r in run["results"] if r["status"] in ("FAIL", "FLAKY"))
    errors = sum(1 for r in run["results"] if r["status"] == "ERROR")
    lines = [
        f'<testsuites name="specagent" tests="{run["total"]}" failures="{failures}" errors="{errors}">',
        f'  <testsuite name="{escape(run["project_id"])}" tests="{run["total"]}"'
        f' failures="{failures}" errors="{errors}"'
        f' timestamp="{run["started_at"]}">',
    ]
    for r in run["results"]:
        case = next((t for t in run["tests"] if t["id"] == r["test_case_id"]), {})
        secs = (r.get("latency_ms") or 0) / 1000
        lines.append(
            f'    <testcase id="{escape(r["test_case_id"])}" name="{escape(r["test_case_id"])}"'
            f' classname="{escape(r.get("rule_id") or "")}" time="{secs:.3f}">'
        )
        if r["status"] in ("FAIL", "FLAKY"):
            kind = diff_types.get(r["test_case_id"], r["status"])
            message = "; ".join(r.get("violations") or []) or r["status"]
            lines.append(
                f'      <failure message="{escape(message)}" type="{escape(kind)}">'
                f"{escape(case.get('user_input', ''))}</failure>"
            )
        elif r["status"] == "ERROR":
            lines.append(
                f'      <error message="{escape(r.get("response") or "execution error")}">'
                f"{escape(case.get('user_input', ''))}</error>"
            )
        lines.append("    </testcase>")
    lines += ["  </testsuite>", "</testsuites>"]
    return "\n".join(lines)


def build_json(run: dict, diff: dict | None) -> str:
    return json.dumps({"run": run, "diff": diff}, ensure_ascii=False, indent=2)
