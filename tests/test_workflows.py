"""Structural tests for the GitHub gate workflow (v1 design §6.3, task 10):
the matrix must cover both example projects, every config path it implies
must exist, and the candidate step must check for exactly exit code 1 (a
config error — exit 2 — must not count as a successful block)."""
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _workflow():
    text = (ROOT / ".github" / "workflows" / "specagent-gate.yml").read_text(encoding="utf-8")
    wf = yaml.safe_load(text)
    # PyYAML turns the key `on` into Python True.
    trigger = wf.get("on", wf.get(True))
    assert trigger, "workflow has no triggers"
    return wf, trigger


def test_gate_matrix_covers_both_examples():
    wf, _trigger = _workflow()
    job = wf["jobs"]["gate"]
    assert job["strategy"]["fail-fast"] is False
    assert job["strategy"]["matrix"]["example"] == ["ecommerce-agent", "fincare-agent"]


def test_matrix_implies_existing_config_paths():
    wf, _trigger = _workflow()
    for example in wf["jobs"]["gate"]["strategy"]["matrix"]["example"]:
        base = ROOT / "examples" / example
        assert (base / "specagent.yaml").exists(), example
        assert (base / "specagent.baseline.yaml").exists(), example
        assert (base / "specs" / "behavior.yaml").exists(), example


def test_candidate_step_checks_exactly_exit_1():
    wf, _trigger = _workflow()
    run_blocks = [s.get("run", "") for s in wf["jobs"]["gate"]["steps"] if isinstance(s, dict)]
    text = "\n".join(run_blocks)
    assert "-ne 1" in text  # exact-exit-1 check, stricter than "any failure"
    assert "--set-baseline" in text  # baseline step records the project baseline
    assert "upload-artifact" in str(wf["jobs"]["gate"]["steps"])
