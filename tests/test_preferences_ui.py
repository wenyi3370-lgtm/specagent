"""Real browser preferences and accessibility checks, in a private server."""
import os
import subprocess
import sys

import pytest

from test_browser_ui import ROOT, _has_playwright, _node


@pytest.mark.skipif(_node() is None or not _has_playwright(), reason='browser tooling unavailable')
def test_language_theme_and_accessibility_in_a_real_browser():
    env = dict(os.environ, SPECAGENT_PYTHON=sys.executable)
    result = subprocess.run([_node(), str(ROOT / 'tests/browser/test_preferences_browser.js')],
                            cwd=ROOT, env=env, capture_output=True, encoding='utf-8',
                            errors='replace', timeout=180)
    if result.returncode == 2:
        pytest.skip(result.stderr)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    checks = [line for line in result.stdout.splitlines() if line.startswith(('PASS', 'FAIL'))]
    assert len(checks) >= 37, output
    assert not any(line.startswith('FAIL') for line in checks), output
