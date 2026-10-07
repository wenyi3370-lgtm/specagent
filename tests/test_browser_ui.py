"""Runs the browser-level dashboard test (tests/browser/test_dashboard_browser.js).

Why a pytest wrapper around a Node script instead of a Python test: playwright
is not a dependency of this project. Adding it to requirements.txt would make
every user — including CI images that only need the API — install a browser
driver. The Node script reuses a playwright-core that already exists on the
machine and skips cleanly when there is none.

What this buys over the existing string-grep tests in test_api.py: those assert
markup *contains* certain ids. This drives the real page in a real browser and
clicks the button, which is the only way to catch the class of bug where the
markup and the script drift apart — every Python test still passes and the user
sees a blank page.

Run just this file:
    .venv/Scripts/python -m pytest tests/test_browser_ui.py -v
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tests" / "browser" / "test_dashboard_browser.js"

# Exit code 2 from the harness means "no playwright-core and no browser" —
# a skip, not a failure. See the PW_CANDIDATES list in the script.
SKIP_EXIT = 2

# Same list the Node script probes, plus "node on PATH". Kept here only to
# decide skip-vs-run cheaply; the script is still the source of truth.
PW_MARKERS = [
    ROOT / "promo" / "node_modules" / "playwright-core",
    ROOT / "node_modules" / "playwright-core",
    Path(os.path.expanduser("~")) / ".workbuddy" / "binaries" / "node" / "workspace"
    / "node_modules" / "playwright-core",
]


def _node() -> str | None:
    """Locate a node binary: SPECAGENT_NODE wins, then PATH."""
    explicit = os.getenv("SPECAGENT_NODE")
    if explicit and Path(explicit).exists():
        return explicit
    return shutil.which("node")


def _has_playwright() -> bool:
    return any((p / "package.json").is_file() for p in PW_MARKERS)


@pytest.mark.skipif(not SCRIPT.is_file(), reason="browser test script missing")
@pytest.mark.skipif(_node() is None, reason="node not available")
@pytest.mark.skipif(not _has_playwright(), reason="no playwright-core on this machine")
def test_dashboard_works_in_a_real_browser():
    """Load the dashboard in Chrome, run an audit through the UI, assert it paints.

    Point SPECAGENT_PYTHON at a venv interpreter if `python` on PATH is not the
    project's environment (uvicorn has to be importable by the child process).
    """
    env = dict(os.environ)
    env.setdefault("SPECAGENT_PYTHON", sys.executable)

    proc = subprocess.run(
        [_node(), str(SCRIPT)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        # node prints UTF-8; the default locale codec (GBK on zh-CN Windows)
        # would fail to decode it and leave proc.stdout as None.
        encoding="utf-8",
        errors="replace",
        timeout=420,
    )

    if proc.returncode == SKIP_EXIT:
        pytest.skip(f"playwright/browser unavailable:\n{proc.stdout}\n{proc.stderr}")

    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"browser test failed (exit {proc.returncode}):\n{output}"

    # Assert on the checks, not just the exit code. The harness prints one
    # PASS/FAIL line each; counting them turns "the script ran" into "all 21
    # assertions actually passed", and a truncated run can't pass silently.
    checks = [
        line.split(None, 1)
        for line in proc.stdout.splitlines()
        if line.startswith(("PASS", "FAIL"))
    ]
    assert checks, f"harness produced no check lines:\n{output}"
    failed = [name for mark, rest in checks if mark == "FAIL" for name in [rest.strip()]]
    assert not failed, f"failed checks: {failed}\n{output}"
    # 73 prior checks + 12 fix-suggestion checks
    assert len(checks) >= 85, f"expected the full check set, got only {len(checks)}:\n{output}"

    # Surfaced so `-s` runs show what actually happened in the browser; without
    # this a green line here says nothing about which checks were verified.
    print(f"\n[harness] {len(checks)} browser checks passed\n{proc.stdout}")


def test_browser_script_ships_with_the_repo():
    """The wrapper is useless if the script it shells out to is not committed."""
    assert SCRIPT.is_file(), f"missing {SCRIPT}"
    assert "SPECAGENT_DB" in SCRIPT.read_text(encoding="utf-8"), (
        "the browser test must point SPECAGENT_DB at a temp file so a UI smoke "
        "test can never write to the repo's real specagent.db"
    )


def test_dashboard_ships_a_favicon():
    """A missing /favicon.ico is the one console error a user cannot explain.

    Chrome requests it unconditionally on every page load, so without this the
    browser console always shows a 404 that has nothing to do with the app.
    Serving it as an inline data URI keeps the dashboard single-file.
    """
    html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'rel="icon"' in html, "index.html declares no favicon; browsers will 404"
    assert "data:image/svg+xml" in html, (
        "favicon should be an inline data URI — a separate binary asset would "
        "need packaging and a cache-busting story for no benefit"
    )


def test_browser_ui_test_owns_a_real_server():
    """The harness must spin up its own uvicorn rather than assume one is running.

    A browser test that needs `uvicorn` already listening on some port is not
    reproducible: it passes on a dev machine and fails in CI.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    assert "uvicorn" in src, "harness does not start a server"
    assert "freePort" in src, "harness must bind an ephemeral port, not a fixed one"
    assert json.dumps(True) in src or "SPECAGENT_DB" in src
