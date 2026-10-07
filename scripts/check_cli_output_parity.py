"""Compare pre-refactor CLI stdout bytes against current CLI on the same records.

Run with --reference <saved cli/specagent.py> --out <evidence.json>.
No output normalization, network or repository example writes. Runtime run ids
and timings are held fixed by replaying persisted outcomes, not stripping text.
"""
import argparse
import contextlib
import gc
import io
import json
import os
import sys
import tempfile
import types
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check(reference):
    for key in ("OPENAI_API_KEY", "TARGET_AGENT_URL", "SPECAGENT_API_TOKEN", "SPECAGENT_PROJECT_CONFIG"):
        os.environ[key] = ""
    from cli import specagent as current
    from app.project import Project, run_project, verify_project
    previous = types.ModuleType("parity_cli_before")
    previous.__file__ = str(ROOT / "cli" / "specagent.py")
    exec(compile(reference.read_text(encoding="utf-8"), previous.__file__, "exec"), previous.__dict__)
    cases = []

    def capture(module, args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = module.main(args)
        return code, output.getvalue().encode("utf-8")

    def compare(name, args):
        before, after = capture(previous, args), capture(current, args)
        assert before == after, name + " differed"
        cases.append({"command": name, "exit_code": before[0], "stdout_bytes": len(before[1]), "equal": True})

    with tempfile.TemporaryDirectory(prefix="specagent-byte-parity-") as directory:
        root = Path(directory)
        (root / "specs").mkdir()
        source = ROOT / "examples" / "fincare-agent"
        (root / "specs" / "behavior.yaml").write_bytes((source / "specs" / "behavior.yaml").read_bytes())
        configs = {}
        for kind in ("fixed", "broken"):
            name = "byte_" + kind + "_" + uuid.uuid4().hex
            (root / (name + ".py")).write_bytes((source / ("agent_fixed.py" if kind == "fixed" else "agent.py")).read_bytes())
            cfg = root / ("specagent." + kind + ".yaml")
            cfg.write_text(f"project: byte-parity\nadapter:\n  type: python\n  agent: {name}:run_agent\n"
                           "spec: specs/behavior.yaml\ngate:\n  fail_on: [critical, high]\n", encoding="utf-8")
            configs[kind] = cfg
        db = str(root / "demo.db")
        fixed_project = Project.load(configs["fixed"], db)
        broken_project = Project.load(configs["broken"], db)
        clean = run_project(fixed_project, set_baseline=True)
        broken = run_project(broken_project)
        fixed = verify_project(fixed_project, pre_run_id=broken.run_id)
        original = current.run_project
        try:
            for kind, outcome, set_baseline in (("fixed", clean, True), ("broken", broken, False)):
                def replay(_project, announce=None, **_kwargs):
                    if announce:
                        announce(1, f"Running 43 behavior tests against {outcome.adapter_name} …")
                    return outcome
                previous.run_project = current.run_project = replay
                for machine in (False, True):
                    compare("run " + kind + (" --json" if machine else ""),
                            ["run", "--config", str(configs[kind]), "--db", db]
                            + (["--set-baseline"] if set_baseline else []) + (["--json"] if machine else []))
        finally:
            current.run_project = original
        original = current.verify_project
        try:
            previous.verify_project = current.verify_project = lambda *_a, **_k: fixed
            for machine in (False, True):
                compare("verify" + (" --json" if machine else ""),
                        ["verify", "--config", str(configs["fixed"]), "--db", db, "--pre-run", broken.run_id]
                        + (["--json"] if machine else []))
        finally:
            current.verify_project = original
        compare("validate", ["validate", "--config", str(configs["fixed"])])
        compare("validate missing", ["validate", "--config", str(root / "missing.yaml")])
        compare("draft", ["draft", "退款超过500元需要人工审批。修改地址之前必须确认。"])
        compare("baseline", ["baseline", clean.run_id, "--db", db])
        for machine in (False, True):
            for name, args in (
                ("triage", ["triage", "--run", broken.run_id, "--db", db]),
                ("diff", ["diff", "--baseline", clean.run_id, "--candidate", broken.run_id, "--db", db]),
                ("metrics", ["metrics", "--project", "byte-parity", "--db", db]),
            ):
                compare(name + (" --json" if machine else ""), args + (["--json"] if machine else []))
        for fmt in ("junit", "json"):
            compare("export " + fmt, ["export", "--run", broken.run_id, "--format", fmt, "--db", db])
        compare("report", ["report", "--run", broken.run_id, "--out", str(root / "report.html"), "--db", db])
        # CLI commands create short-lived Stores; SQLite pools may keep files
        # open on Windows even after the command's local Store goes out of scope.
        from sqlalchemy.engine import Engine
        for engine in gc.get_objects():
            if isinstance(engine, Engine) and engine.url.drivername == "sqlite":
                engine.dispose()
    return {"comparisons": len(cases), "cases": cases,
            "method": "Original and current CLI on identical persisted FinCare records, replaying run/verify outcomes and progress to hold ids and timings fixed. UTF-8 stdout bytes and exit codes compared without normalization."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    evidence = check(args.reference)
    args.out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{evidence['comparisons']} comparisons: identical stdout bytes and exit codes")
