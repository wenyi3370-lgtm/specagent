"""CLI output must survive a non-UTF-8 redirected stdout (zh-CN Windows / GBK).

Regression: printing a status glyph raised UnicodeEncodeError, and the
traceback's exit code 1 is indistinguishable from "gate failed" in CI.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(args, env):
    return subprocess.run(
        [sys.executable, "-m", "cli.specagent", *args],
        cwd=ROOT, env=env, capture_output=True, timeout=240,
    )


def test_gate_output_survives_gbk_stdout(tmp_path):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "gbk"  # strict stdout codec, as on zh-CN Windows
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    db = str(tmp_path / "gate.db")

    base = _run(["run", "--config", "examples/fincare-agent/specagent.baseline.yaml",
                 "--set-baseline", "--db", db, "--label", "baseline"], env)
    assert base.returncode == 0, base.stderr.decode("utf-8", "replace")

    cand = _run(["run", "--config", "examples/fincare-agent/specagent.yaml",
                 "--db", db, "--label", "candidate"], env)
    out = cand.stdout.decode("gbk", "replace")
    err = cand.stderr.decode("utf-8", "replace")
    assert "Traceback" not in err, err
    assert cand.returncode == 1  # the gate blocked it, not a crash
    assert "New regressions:" in out
