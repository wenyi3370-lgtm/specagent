"""Unit tests: fix suggestions, verify loop, verdict table (v1 design §8.8,
task 15) — includes real `git apply` checks (missing git is a FAIL)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.agent.fixes import DiffError, build_unified_diff, suggestion_id
from app.agent.sandbox import ProjectSandbox
from app.agent.tools import ToolContext, ToolRegistry
from app.errors import SpecValidationError
from app.project import Project, run_project, verify_project
from app.regression import DiffEntry, DiffSummary
from app.verify import verify_counts, verify_verdict
from cli.specagent import EXIT_GATE_FAILED, EXIT_OK, main
from test_agent_tools import _ctx, _approve

REPO_ROOT = Path(__file__).resolve().parents[1]


def _copy_example(name: str, tmp_path: Path) -> Path:
    dst = tmp_path / name
    shutil.copytree(REPO_ROOT / "examples" / name, dst,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))
    return dst


# -- the diff builder applies with git (§8.8 test_diff_applies_with_git) ----------


def test_diff_applies_with_git(tmp_path):
    assert shutil.which("git"), "git is required for the apply test (FAIL, not skip)"
    cases = [
        # (label, old_bytes, new_text)
        ("lf_file", b"a = 1\nb = 2\n", "a = 1\nb = 3\n"),
        ("crlf_file_model_supplies_lf", b"a = 1\r\nb = 2\r\n", "a = 1\nb = 3\n"),
        ("no_final_newline", b"a = 1\nb = 2", "a = 1\nb = 3"),
        ("adding_final_newline", b"a = 1\nb = 2", "a = 1\nb = 2\n"),
        ("removing_final_newline", b"a = 1\nb = 2\n", "a = 1\nb = 2"),
        ("non_ascii", "# 注释\nvalue = 1\n".encode("utf-8"), "# 注释\nvalue = 2\n"),
        ("crlf_no_final_newline", b"a = 1\r\nb = 2", "a = 1\r\nb = 3"),
    ]
    for label, old_bytes, new_text in cases:
        repo = tmp_path / label
        repo.mkdir()
        rel = "src/thing.py"
        (repo / "src").mkdir()
        (repo / "src" / "thing.py").write_bytes(old_bytes)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
        diff = build_unified_diff(rel, old_bytes, new_text)
        (repo / "fix.diff").write_text(diff, encoding="utf-8", newline="")
        # pin autocrlf: the user's global config must not change apply semantics
        subprocess.run(["git", "-c", "core.autocrlf=false", "apply", "--check", "fix.diff"],
                       cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "-c", "core.autocrlf=false", "apply", "fix.diff"],
                       cwd=repo, check=True, capture_output=True)
        crlf = old_bytes.count(b"\r\n")
        lf = old_bytes.count(b"\n") - crlf
        eol = "\r\n" if crlf > lf else "\n"
        expected = new_text.replace("\r\n", "\n").replace("\n", eol).encode("utf-8")
        assert (repo / rel).read_bytes() == expected, label


def test_diff_rejects_identical_and_non_utf8():
    with pytest.raises(DiffError) as exc:
        build_unified_diff("x.py", b"same\n", "same\n")
    assert str(exc.value) == "no_change"
    with pytest.raises(DiffError) as exc:
        build_unified_diff("x.py", b"caf\xe9 = 1\n", "cafe = 1\n")
    assert str(exc.value) == "not_utf8"


def test_suggestion_id_is_stable_and_matches_pattern():
    import re
    sid = suggestion_id("diff-text", __import__("datetime").datetime(2026, 10, 6, tzinfo=__import__("datetime").timezone.utc))
    assert re.fullmatch(r"fix_20261006T000000_[0-9a-f]{6}", sid)


# -- verdict table (§8.8) ------------------------------------------------------------


def _entry(kind, candidate="PASS", rule="R"):
    return DiffEntry(test_case_id=f"{rule}-01", rule_id=rule, severity="high",
                     diff_type=kind, candidate_status=candidate)


def _summary(*entries):
    return DiffSummary(baseline_run_id="pre", candidate_run_id="cand",
                       entries=list(entries))


def test_verdict_table_order():
    assert verify_verdict(verify_counts(_summary(_entry("NEW_REGRESSION", "FAIL")))) == "REGRESSED"
    assert verify_verdict(verify_counts(_summary(_entry("NEW_ERROR", "ERROR")))) == "REGRESSED"
    assert verify_verdict(verify_counts(_summary(_entry("CANCELED", "CANCELED")))) == "INCOMPLETE"
    new_test_failing = DiffEntry(test_case_id="N-01", rule_id="N", severity="high",
                                 diff_type="NEW_TEST", candidate_status="FAIL")
    assert verify_verdict(verify_counts(_summary(new_test_failing))) == "NOT_FIXED"
    fail_to_error = DiffEntry(test_case_id="R-01", rule_id="R", severity="high",
                              diff_type="PERSISTENT_FAIL", candidate_status="ERROR")
    assert verify_verdict(verify_counts(_summary(fail_to_error))) == "NOT_FIXED"  # candidate_error
    # nothing fixed + something still failing → NOT_FIXED (rule 3)
    assert verify_verdict(verify_counts(_summary(_entry("PERSISTENT_FAIL", "FAIL")))) == "NOT_FIXED"
    # at least one fix + remaining failures → PARTIAL
    assert verify_verdict(verify_counts(_summary(_entry("FIXED", "PASS"),
                                                 _entry("PERSISTENT_FAIL", "FAIL")))) == "PARTIAL"
    assert verify_verdict(verify_counts(_summary(_entry("FIXED", "PASS"),
                                                 _entry("FLAKY", "FLAKY")))) == "PARTIAL"
    assert verify_verdict(verify_counts(_summary(_entry("FIXED", "PASS")))) == "ALL_FIXED"
    assert verify_verdict(verify_counts(_summary(_entry("STABLE_PASS", "PASS")))) == "NO_CHANGE"


# -- FinCare scenario (§8.8) -----------------------------------------------------------


def _fincare(tmp_path):
    proj = _copy_example("fincare-agent", tmp_path / "proj")
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True, capture_output=True)
    project = Project.load(str(proj / "specagent.yaml"), db=str(tmp_path / "f.db"))
    return proj, project


def _tree_hash(root: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    for path in sorted(Path(root).rglob("*")):
        if path.is_dir() or "__pycache__" in path.parts or ".specagent" in path.parts \
                or ".git" in path.parts:
            continue
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_fincare_suggest_apply_verify_all_fixed(tmp_path, capsys):
    proj, project = _fincare(tmp_path)
    run_project(project)  # vulnerable run → pre-fix run
    ctx = ToolContext(project=project, sandbox=ProjectSandbox(proj, True),
                      allow_source=True, confirm=_approve)
    registry = ToolRegistry()
    # read the target in full first (§8.3 rule 7)
    read = registry.call(ctx, "read_file", {"path": "agent.py"}, "c0")
    assert read.payload["ok"] and read.payload["rewritable"]
    fixed_src = (proj / "agent_fixed.py").read_text(encoding="utf-8")
    tree_before = _tree_hash(proj)
    result = registry.call(ctx, "write_fix_suggestion", {
        "rule_id": "ACCOUNT_SCOPE",
        "diagnosis": "agent trusts the account id from the message",
        "changes": [{"path": "agent.py", "new_content": fixed_src}],
    }, "c1")
    assert result.payload["ok"] is True, result.payload
    suggestion_dir = proj / ".specagent" / "suggestions" / result.payload["id"]
    assert (suggestion_dir / "fix.diff").exists()
    assert (suggestion_dir / "suggestion.json").exists()
    # the suggestion never modifies the project tree
    assert _tree_hash(proj) == tree_before
    # the diff applies cleanly against the vulnerable agent.py
    check = subprocess.run(["git", "-c", "core.autocrlf=false", "apply", "--check",
                            str(suggestion_dir / "fix.diff")],
                           cwd=proj, capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    # apply the fix for real and verify → ALL_FIXED
    (proj / "agent.py").write_text(fixed_src, encoding="utf-8")
    assert main(["verify", "--config", str(proj / "specagent.yaml"),
                 "--db", str(tmp_path / "f.db"), "--json"]) == EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert report["verdict"] == "ALL_FIXED"
    assert {e["rule_id"] for e in report["entries"] if e["diff_type"] == "FIXED"} == {
        "LARGE_TRANSFER_APPROVAL", "ACCOUNT_SCOPE"}


def test_fincare_fix_that_regresses_close_account(tmp_path, monkeypatch, capsys):
    proj, project = _fincare(tmp_path)
    run_project(project)
    (proj / "agent.py").write_text(
        (proj / "agent_fixed.py").read_text(encoding="utf-8"), encoding="utf-8")
    # the "fix" also removes the close_account role check
    regressed = (proj / "agent.py").read_text(encoding="utf-8").replace(
        '    if actor.get("role") != "admin":\n'
        '        return {"response": "关闭账户需要管理员权限,已拒绝您的请求。", "trace": trace}',
        '    if False:\n'
        '        return {"response": "关闭账户需要管理员权限,已拒绝您的请求。", "trace": trace}')
    assert "if False" in regressed
    (proj / "agent.py").write_text(regressed, encoding="utf-8")

    # the agent claims success — the verbatim REGRESSED block still ends the text
    import cli.specagent as cli
    from app.agent.loop import AgentSession, Transcript
    from app.agent.tools import ConfirmResult

    class _FakeItem:
        def __init__(self, type, **kw):
            self.type, self.__dict__ = type, self.__dict__
            self.__dict__.update(kw)

        def model_dump(self):
            return dict(self.__dict__)

    class _FakeClient:
        def __init__(self, turns):
            self._turns, self.responses = list(turns), self

        def create(self, **kwargs):
            return type("R", (), {"output": self._turns.pop(0)})()

    ctx = ToolContext(project=project, sandbox=ProjectSandbox(proj), confirm=lambda r: ConfirmResult("approved"))
    session = AgentSession(ctx, ToolRegistry(), client=_FakeClient([
        [_FakeItem("function_call", name="verify_fix", arguments="{}", call_id="c1")],
        [_FakeItem("message", content=[{"type": "output_text", "text": "All fixed! 🎉"}])],
    ]), transcript=Transcript(proj))
    result = session.run_goal("verify my fix")
    assert "All fixed!" in result.final_text
    assert "→ REGRESSED" in result.final_text  # the verbatim block wins
    assert "Verification vs pre-fix run" in result.final_text

    # CLI verdict + exit code + rule attribution
    assert main(["verify", "--config", str(proj / "specagent.yaml"),
                 "--db", str(tmp_path / "f.db")]) == EXIT_GATE_FAILED
    out = capsys.readouterr().out
    assert "REGRESSED" in out and "CLOSE_ACCOUNT_ROLE" in out


def test_fincare_verify_json(tmp_path, capsys):
    proj, project = _fincare(tmp_path)
    run_project(project)
    (proj / "agent.py").write_text(
        (proj / "agent_fixed.py").read_text(encoding="utf-8"), encoding="utf-8")
    assert main(["verify", "--config", str(proj / "specagent.yaml"),
                 "--db", str(tmp_path / "f.db"), "--json"]) == EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert report["verdict"] == "ALL_FIXED"
    assert report["counts"]["fixed"] >= 4


def test_pre_run_selection_skips_verify_and_other_spec_runs(tmp_path):
    proj, project = _fincare(tmp_path)
    first = run_project(project)  # vulnerable
    (proj / "agent.py").write_text(
        (proj / "agent_fixed.py").read_text(encoding="utf-8"), encoding="utf-8")
    verify1 = verify_project(project)
    assert verify1.verdict == "ALL_FIXED"
    # a run of ANOTHER spec (different snapshot) is skipped
    other = project.store.create_run(project_id="fincare-agent",
                                     spec={"rules": [{"id": "OTHER"}]}, tests=[],
                                     label="", spec_compiler="", agent="")
    project.store.complete_run(other, passed=0, failed=0, errors=0, canceled=0,
                               total=0, score=0)
    verify2 = verify_project(project)  # no --pre-run: skips verify1 and `other`
    assert verify2.verdict == "ALL_FIXED"
    assert verify2.pre_run_id == first.run_id


def test_no_pre_fix_run_is_config_error(tmp_path):
    _copy_example("fincare-agent", tmp_path / "proj")
    assert main(["verify", "--config", str(tmp_path / "proj" / "specagent.yaml"),
                 "--db", str(tmp_path / "f2.db")]) == 2


# -- write_fix_suggestion gate behaviors ------------------------------------------------


def test_fix_suggestion_requires_allow_source(tmp_path):
    proj, project = _fincare(tmp_path)
    ctx = _ctx(project)  # allow_source False, declined callback
    result = ToolRegistry().call(ctx, "write_fix_suggestion", {
        "rule_id": "R", "diagnosis": "d",
        "changes": [{"path": "agent.py", "new_content": "x = 1\n"}]}, "c")
    assert result.payload["error"] == "source_access_disabled"
    assert not (proj / ".specagent" / "suggestions").exists()


def test_fix_target_refuses_spec_config_and_draft(tmp_path):
    proj, project = _fincare(tmp_path)
    ctx = ToolContext(project=project, sandbox=ProjectSandbox(proj, True),
                      allow_source=True, confirm=_approve)
    registry = ToolRegistry()
    for target in ("specagent.yaml", "specs/behavior.yaml"):
        registry.call(ctx, "read_file", {"path": target}, "r")
        result = registry.call(ctx, "write_fix_suggestion", {
            "rule_id": "R", "diagnosis": "d",
            "changes": [{"path": target, "new_content": "hacked: true\n"}]}, "c")
        assert result.payload["error"] == "path_denied", target
        assert result.payload["reason"] == "protected_path"


def test_file_not_fully_read(tmp_path):
    proj, project = _fincare(tmp_path)
    ctx = ToolContext(project=project, sandbox=ProjectSandbox(proj, True),
                      allow_source=True, confirm=_approve)
    registry = ToolRegistry()
    # never read
    result = registry.call(ctx, "write_fix_suggestion", {
        "rule_id": "R", "diagnosis": "d",
        "changes": [{"path": "agent.py", "new_content": "x = 1\n"}]}, "c1")
    assert result.payload["error"] == "file_not_fully_read"
    assert result.payload["reason"] == "not_read"
    # read a 30 KB file (truncated → not rewritable)
    (proj / "big.py").write_text("x" * 30000, encoding="utf-8")
    registry.call(ctx, "read_file", {"path": "big.py"}, "r1")
    result = registry.call(ctx, "write_fix_suggestion", {
        "rule_id": "R", "diagnosis": "d",
        "changes": [{"path": "big.py", "new_content": "y = 1\n"}]}, "c2")
    assert result.payload["error"] == "file_not_fully_read"
    assert result.payload["reason"] == "truncated"
    # changed since read
    registry.call(ctx, "read_file", {"path": "agent.py"}, "r2")
    (proj / "agent.py").write_text("VALUE = changed\n", encoding="utf-8")
    result = registry.call(ctx, "write_fix_suggestion", {
        "rule_id": "R", "diagnosis": "d",
        "changes": [{"path": "agent.py", "new_content": "x = 1\n"}]}, "c3")
    assert result.payload["error"] == "file_not_fully_read"
    assert result.payload["reason"] == "changed_since_read"


# -- sibling-module freshness through verify (§6.1 × §8.8) ------------------------------


TWO_FILE_CONFIG = """\
project: t2
adapter:
  type: python
  agent: agent:run_agent
spec: specs/behavior.yaml
"""

TWO_FILE_SPEC = """\
agent: T2
rules:
  - id: TRANSFER_ONCE
    title: t
    severity: high
    action: transfer
    constraints:
      - type: max_calls
        tool: transfer
        max: 1
    probes:
      - text: "转账"
        actor: {user_id: U1, account_id: ACC-1, role: customer}
"""


def test_verify_sees_fresh_sibling_modules(tmp_path):
    proj = tmp_path / "proj"
    (proj / "specs").mkdir(parents=True)
    (proj / "specagent.yaml").write_text(TWO_FILE_CONFIG, encoding="utf-8")
    (proj / "specs" / "behavior.yaml").write_text(TWO_FILE_SPEC, encoding="utf-8")
    (proj / "limits_helper.py").write_text("LIMIT = 2\n", encoding="utf-8")
    (proj / "agent.py").write_text(
        "import limits_helper\n\n"
        "def run_agent(message, history=None, actor=None):\n"
        "    trace = []\n"
        "    for _ in range(limits_helper.LIMIT):\n"
        "        trace.append({'type': 'tool_call', 'name': 'transfer',\n"
        "                      'args': {'amount': 100}})\n"
        "    return {'response': 'ok', 'trace': trace}\n", encoding="utf-8")
    project = Project.load(str(proj / "specagent.yaml"), db=str(tmp_path / "t2.db"))
    run_project(project)  # LIMIT=2 → max_calls(1) violated → pre-fix run FAILs
    ctx = ToolContext(project=project, sandbox=ProjectSandbox(proj, True),
                      allow_source=True, confirm=_approve)
    registry = ToolRegistry()
    assert registry.call(ctx, "read_file", {"path": "limits_helper.py"}, "c0").payload["rewritable"]
    result = registry.call(ctx, "write_fix_suggestion", {
        "rule_id": "TRANSFER_ONCE", "diagnosis": "limit too high",
        "changes": [{"path": "limits_helper.py", "new_content": "LIMIT = 1\n"}]}, "c1")
    assert result.payload["ok"] is True, result.payload
    (proj / "limits_helper.py").write_text("LIMIT = 1\n", encoding="utf-8")  # apply
    outcome = verify_project(project)
    assert outcome.verdict == "ALL_FIXED", outcome.counts  # fresh sibling import (unique module name: cross-project sys.modules pollution)
