"""Check real Action outputs against the offline FinCare candidate payload."""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ci import load_result


def check(kind, payload, gate_exit, outcome, cache_key="", comment_status="", require_cache=False):
    if gate_exit not in {"0", "1"}:
        raise ValueError("missing gate output or configuration error")
    expected_outcome = "failure" if gate_exit == "1" else "success"
    if outcome != expected_outcome:
        raise ValueError("Action outcome does not match its CLI gate output")
    gate = payload.get("gate") or {}
    diff = payload.get("diff")
    if kind == "cache" and not cache_key:
        if require_cache:
            raise ValueError("the base branch contains the integration workflow but no baseline cache was restored")
        if gate_exit != "0" or diff is not None or gate.get("violations"):
            raise ValueError("cache miss must be an unevaluated first candidate")
        return {"status": "pending-main-baseline", "gate_exit": gate_exit,
                "cache_key": "", "reason": "No earlier baseline cache was restored"}
    if gate_exit != "1" or not diff or diff.get("new_regressions") != 4:
        raise ValueError("restored/seeded FinCare baseline must expose four new regressions")
    if len(gate.get("violations") or []) != 4:
        raise ValueError("gate violations do not match the FinCare regression fixture")
    if kind == "readonly" and comment_status != "unavailable":
        raise ValueError("read-only comment attempt did not report unavailable")
    return {"status": "verified", "gate_exit": gate_exit,
            "new_regressions": 4, "cache_key": cache_key,
            "comment_status": comment_status}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("cache", "readonly"))
    parser.add_argument("--result", default="result.json")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        evidence = check(args.kind, load_result(Path(args.result).read_text(encoding="utf-8")),
                         os.getenv("SA_GATE_EXIT", ""), os.getenv("SA_ACTION_OUTCOME", ""),
                         os.getenv("SA_CACHE_KEY", ""), os.getenv("SA_COMMENT_STATUS", ""),
                         os.getenv("SA_REQUIRE_CACHE", "") == "true")
    except (OSError, ValueError) as exc:
        print(f"Action integration evidence rejected: {exc}", file=sys.stderr)
        return 1
    evidence["event"] = os.getenv("GITHUB_EVENT_NAME", "local")
    evidence["ref"] = os.getenv("GITHUB_REF", "")
    evidence["source_sha"] = os.getenv("GITHUB_SHA", "")
    text = json.dumps(evidence, indent=2) + "\n"
    Path(args.out).write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
