"""Unit tests: agent path sandbox + redaction (v1 design §8.3, task 11)."""
import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from app.agent.sandbox import (MAX_OUTPUT_CHARS, ProjectSandbox, SandboxDenied,
                               ensure_state_dir, redact_text)


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "agent.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "specs").mkdir()
    (root / "specs" / "behavior.yaml").write_text("agent: T\nrules: []\n", encoding="utf-8")
    return root


def test_allowed_file_reads_fine(proj):
    sandbox = ProjectSandbox(proj)
    result = sandbox.read_file("agent.py")
    assert result["ok"] is True
    assert result["rewritable"] is True
    assert result["sha256"] == hashlib.sha256((proj / "agent.py").read_bytes()).hexdigest()


def test_lexical_traversal_denied(proj):
    sandbox = ProjectSandbox(proj)
    for path in ("../x", "a/../../x", "..\\x", "specs/../../x", "agent.py:hidden",
                 "C:notdrive/x", ""):
        denied = sandbox.resolve_read(path)
        assert isinstance(denied, SandboxDenied), path
        assert denied.reason == "traversal"


def test_absolute_outside_denied(proj, tmp_path):
    sandbox = ProjectSandbox(proj)
    denied = sandbox.resolve_read(str(tmp_path / "elsewhere.py"))
    assert isinstance(denied, SandboxDenied)


def test_symlink_escape_denied(proj, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    # Deliberately not a deny-listed name: the assertion is about the realpath
    # containment rule, and a name like secret.txt would be rejected by the
    # component deny list anyway (reason "sensitive_name") — passing for the
    # wrong reason.
    (outside / "payload.py").write_text("top secret", encoding="utf-8")
    link = proj / "leak"
    for make in (lambda: link.symlink_to(outside, target_is_directory=True),
                 lambda: _mklink_junction(link, outside)):
        try:
            make()
        except (OSError, subprocess.SubprocessError):
            continue
        # Creating the link is not enough: some Windows hosts accept
        # symlink_to() without the OS actually resolving it (no Developer Mode),
        # and realpath then returns a path inside the root. Only trust the real
        # filesystem when it genuinely reports the escape.
        if _escapes_root(link, proj):
            denied = ProjectSandbox(proj).resolve_read("leak/payload.py")
            assert isinstance(denied, SandboxDenied)
            assert denied.reason == "symlink_escape"
            return
        _rm_link(link)
    # No usable symlink/junction on this host: assert the same containment logic
    # with a monkeypatched realpath — the check itself is never skipped.
    real = os.path.realpath

    def fake_realpath(path):
        if "leak" in str(path):
            return str(outside / "payload.py")
        return real(path)

    monkeypatch.setattr("app.agent.sandbox.os.path.realpath", fake_realpath)
    denied = ProjectSandbox(proj).resolve_read("leak/payload.py")
    assert isinstance(denied, SandboxDenied)
    assert denied.reason == "symlink_escape"


def _mklink_junction(link, target):
    import subprocess
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                   check=True, capture_output=True)


def _escapes_root(link, root):
    """True when the OS really resolves ``link`` to something outside ``root``."""
    root_real = os.path.normcase(os.path.realpath(str(root)))
    link_real = os.path.normcase(os.path.realpath(str(link / "payload.py")))
    return os.path.commonpath([link_real, root_real]) != root_real


def _rm_link(link):
    """Remove a directory symlink or junction.

    ``Path.unlink`` is wrong for those: on Windows a directory link stats as a
    directory, and unlinking it raises PermissionError (WinError 5) — junction
    deletion is ``os.rmdir``. Both are tried so the loop can move on to the
    next link-making strategy regardless of which kind was created.
    """
    for remove in (os.rmdir, os.unlink):
        try:
            remove(str(link))
            return
        except OSError:
            continue


@pytest.mark.parametrize("name", [".env", ".env.local", "id_rsa", "id_ed25519",
                                  "server.pem", "vault.key", "credentials.json",
                                  "my-secret.txt", "passwords.txt", "api_token.txt",
                                  ".netrc", "kubeconfig", "terraform.tfstate",
                                  "service-account.json"])
def test_sensitive_names_denied(proj, name):
    (proj / name).write_text("x", encoding="utf-8")
    sandbox = ProjectSandbox(proj)
    denied = sandbox.resolve_read(name)
    assert isinstance(denied, SandboxDenied), name
    assert denied.reason in ("sensitive_name", "sensitive_extension")


def test_nested_sensitive_components_denied(proj):
    (proj / ".git").mkdir()
    (proj / ".git" / "config").write_text("x", encoding="utf-8")
    (proj / ".specagent").mkdir()
    (proj / ".specagent" / "agent-logs").mkdir()
    (proj / ".specagent" / "agent-logs" / "x.jsonl").write_text("{}", encoding="utf-8")
    (proj / ".specagent-ci").mkdir()
    (proj / ".specagent-ci" / "x.db").write_text("x", encoding="utf-8")
    sandbox = ProjectSandbox(proj)
    for path in (".git/config", ".specagent/agent-logs/x.jsonl", ".specagent-ci/x.db"):
        denied = sandbox.resolve_read(path)
        assert isinstance(denied, SandboxDenied), path


def test_db_files_denied_by_extension(proj):
    (proj / "gate.db").write_text("x", encoding="utf-8")
    sandbox = ProjectSandbox(proj)
    denied = sandbox.resolve_read("gate.db")
    assert isinstance(denied, SandboxDenied)
    assert denied.reason == "sensitive_extension"


def test_binary_and_unknown_extension_denied(proj):
    (proj / "blob.bin").write_bytes(b"\x00\x01\x02" + b"a" * 100)  # .bin → extension first
    (proj / "data.py").write_bytes(b"\x00\x01\x02" + b"a" * 100)   # allowed ext, NUL → binary
    (proj / "notes.xyz").write_text("text", encoding="utf-8")
    sandbox = ProjectSandbox(proj)
    assert sandbox.read_file("blob.bin")["reason"] == "extension_not_allowed"
    assert sandbox.read_file("data.py")["reason"] == "binary"
    assert sandbox.read_file("notes.xyz")["reason"] == "extension_not_allowed"


def test_oversize_read_is_truncated_and_not_rewritable(proj):
    big = "x" * (MAX_OUTPUT_CHARS + 5000)
    (proj / "big.py").write_text(big, encoding="utf-8")
    result = ProjectSandbox(proj).read_file("big.py")
    assert result["ok"] is True
    assert result["truncated"] is True
    assert result["rewritable"] is False
    assert "sha256" in result  # hash of raw bytes, but not rewritable


def test_redacted_or_non_utf8_files_are_not_rewritable(proj):
    (proj / "values.py").write_text("API_KEY=abcdef123456\n", encoding="utf-8")
    (proj / "latin.py").write_bytes(b"caf\xe9 = 1\n")
    sandbox = ProjectSandbox(proj)
    secret = sandbox.read_file("values.py")
    assert secret["rewritable"] is False  # redact_text changed the content
    latin = sandbox.read_file("latin.py")
    assert latin["rewritable"] is False   # not strict UTF-8


def test_redact_text_table():
    assert redact_text("API_KEY=sk-abcdef123456ghij") == "API_KEY=[REDACTED]"
    assert redact_text("x: Bearer abcdefgh12345678") == "x: [REDACTED]"
    assert redact_text("token = hunter2") == "token = [REDACTED]"
    assert redact_text("connect sk-abcdefghijklmnop1234 now") == "connect [REDACTED] now"
    assert redact_text("git push ghp_abcdefghijklmnopqrst") == "git push [REDACTED]"
    assert redact_text("key AKIAIOSFODNN7EXAMPLE end") == "key [REDACTED] end"
    pem = "-----BEGIN RSA PRIVATE KEY-----\nabc\ndef\n-----END RSA PRIVATE KEY-----\n"
    assert redact_text(pem) == "[REDACTED]\n"
    assert redact_text("clean text with numbers 12345") == "clean text with numbers 12345"


def test_ensure_state_dir_creates_gitignore(proj):
    state = ensure_state_dir(proj)
    assert (state / ".gitignore").read_text(encoding="utf-8") == "*\n"
    # idempotent and never clobbers
    (state / ".gitignore").write_text("agent-logs/\n", encoding="utf-8")
    ensure_state_dir(proj)
    assert (state / ".gitignore").read_text(encoding="utf-8") == "agent-logs/\n"
