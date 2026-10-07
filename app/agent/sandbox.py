"""Path sandbox and redaction for the agent layer (v1 design §8.3, task 11).

All agent-visible path handling goes through :meth:`ProjectSandbox.resolve_read`:
lexical checks first (traversal, UNC/device paths, alternate data streams),
then a realpath containment check (symlinks and Windows junctions cannot
escape the project root), then a per-component deny list (secrets, VCS,
state, databases). Every denial is a :class:`SandboxDenied` with a stable
``reason``; nothing sensitive is echoed back.

Writes never take an agent-supplied path: the writable locations are computed
by the tool layer (drafts, the spec itself after human confirmation,
``.specagent/suggestions/``, ``.specagent/agent-logs/``).
"""
import fnmatch
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

MAX_READ_BYTES = 200_000
MAX_OUTPUT_CHARS = 20_000

# Deny list (§8.3 rule 3), evaluated on every path component relative to the
# root, lower-cased.
_SENSITIVE_COMPONENTS = {".git", ".ssh", ".aws", ".gnupg"}
_SENSITIVE_PREFIXES = (".env", ".specagent")
_SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".ppk", ".kdbx",
                       ".db", ".sqlite", ".sqlite3")
_SENSITIVE_GLOBS = ("id_rsa*", "id_ed25519*", "id_ecdsa*", "*credential*",
                    "*secret*", "*password*", "*token*", ".netrc", ".npmrc",
                    ".pypirc", ".htpasswd", "service-account*.json", "kubeconfig",
                    "*.tfstate*", "*.tfvars")

# read_file extension allow-list (§8.3 rule 4), plus exact file names.
_ALLOWED_EXTENSIONS = (".py", ".pyi", ".md", ".txt", ".yaml", ".yml", ".json",
                       ".toml", ".cfg", ".ini", ".js", ".ts", ".tsx", ".jsx",
                       ".go", ".java", ".rb", ".rs", ".sh", ".sql", ".html", ".css")
_ALLOWED_NAMES = {"Dockerfile", "Makefile", "LICENSE"}

# redact_text (§8.3 rule 5), applied in this order.
_PEM_BLOCK = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
                        re.DOTALL)
_KEY_VALUE = re.compile(r"\b([\w.-]*(?:api[_-]?key|secret|token|passw(?:or)?d|private[_-]?key)[\w.-]*)"
                        r"(\s*[:=]\s*)(\S+)", re.IGNORECASE)
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_SK_TOKEN = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")
_GH_TOKEN = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")
_AWS_KEY = re.compile(r"\bAKIA[0-9A-Z]{16}\b")


def redact_text(text: str) -> str:
    """Replace secret-looking values with [REDACTED] (§8.3 rule 5). Used for
    transcripts, tool outputs and file reads alike."""
    out = _PEM_BLOCK.sub("[REDACTED]", text)
    out = _KEY_VALUE.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", out)
    out = _BEARER.sub("[REDACTED]", out)
    out = _SK_TOKEN.sub("[REDACTED]", out)
    out = _GH_TOKEN.sub("[REDACTED]", out)
    out = _AWS_KEY.sub("[REDACTED]", out)
    return out


@dataclass(frozen=True)
class SandboxDenied:
    """A path the agent may not read; ``reason`` is a stable identifier."""
    reason: str


def _lexical_deny(path) -> str | None:
    """§8.3 rule 1 — pure lexical checks, platform-independent."""
    if not isinstance(path, str) or not path.strip():
        return "traversal"
    if "\x00" in path:
        return "traversal"
    normalized = path.replace("\\", "/")
    if normalized.startswith("//"):
        return "traversal"  # UNC and \\?\ device paths
    segments = [s for s in normalized.split("/") if s not in ("", ".")]
    for i, segment in enumerate(segments):
        if segment == "..":
            return "traversal"
        if ":" in segment and not (i == 0 and len(segment) == 2 and segment[0].isalpha()):
            return "traversal"  # ADS, drive-relative forms, stray colons
    return None


def _component_denied(part: str) -> str | None:
    low = part.lower()
    if low in _SENSITIVE_COMPONENTS or low.startswith(_SENSITIVE_PREFIXES):
        return "sensitive_name"
    if low.endswith(_SENSITIVE_SUFFIXES):
        return "sensitive_extension"
    if any(fnmatch.fnmatch(low, glob) for glob in _SENSITIVE_GLOBS):
        return "sensitive_name"
    return None


class ProjectSandbox:
    """Read gate rooted at the project directory (the specagent.yaml dir)."""

    def __init__(self, root: Path, allow_source: bool = False):
        self.root = Path(root)
        self.root_real = os.path.normcase(os.path.realpath(str(self.root)))
        self.allow_source = allow_source

    def resolve_read(self, path) -> Path | SandboxDenied:
        """Rules 1–3: lexical checks, realpath containment, deny list.
        Returns the resolved real path or a SandboxDenied."""
        reason = _lexical_deny(path)
        if reason:
            return SandboxDenied(reason)
        candidate = Path(path) if Path(path).is_absolute() else self.root / path
        real = os.path.realpath(str(candidate))
        real_norm = os.path.normcase(real)
        try:
            inside = os.path.commonpath([real_norm, self.root_real]) == self.root_real
        except ValueError:  # different drives on Windows
            inside = False
        if not inside:
            # lexical ".." is already rejected, so a realpath escape means the
            # path traverses a symlink or junction pointing outside the root
            return SandboxDenied("symlink_escape")
        rel = os.path.relpath(real_norm, self.root_real)
        if rel != ".":
            for part in rel.split(os.sep):
                denied = _component_denied(part)
                if denied:
                    return SandboxDenied(denied)
        return Path(real)

    def read_file(self, path) -> dict:
        """§8.3 rule 4: read one source file with extension/binary/size rules.
        Returns ``{ok, path, content, truncated, bytes, sha256, rewritable}``
        or ``{ok: false, error: "path_denied", reason}``. ``sha256`` is the
        hash of the raw bytes; ``rewritable`` marks a *fully* returned,
        un-redacted, strict-UTF-8 read — only those may be rewritten later."""
        resolved = self.resolve_read(path)
        if isinstance(resolved, SandboxDenied):
            return {"ok": False, "error": "path_denied", "reason": resolved.reason}
        real = resolved
        if not real.is_file():
            return {"ok": False, "error": "path_denied", "reason": "not_a_file"}
        if real.suffix.lower() not in _ALLOWED_EXTENSIONS and real.name not in _ALLOWED_NAMES:
            return {"ok": False, "error": "path_denied", "reason": "extension_not_allowed"}
        raw = real.read_bytes()
        size_ok = len(raw) <= MAX_READ_BYTES
        data = raw[:MAX_READ_BYTES]
        if b"\x00" in data[:4096]:
            return {"ok": False, "error": "path_denied", "reason": "binary"}
        try:
            data.decode("utf-8")
            strict_ok = True
        except UnicodeDecodeError:
            strict_ok = False
        text = data.decode("utf-8", errors="replace")
        truncated = len(text) > MAX_OUTPUT_CHARS
        if truncated:
            text = text[:MAX_OUTPUT_CHARS]
        redacted = redact_text(text)
        note = ""
        if not size_ok or truncated:
            note = "truncated"
        elif not strict_ok:
            note = "not_utf8"
        elif redacted != text:
            note = "redacted"
        rel = os.path.relpath(str(real), os.path.realpath(str(self.root))).replace("\\", "/")
        return {
            "ok": True, "path": rel, "content": redacted,
            "truncated": truncated, "bytes": len(data),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "rewritable": not note,
            "not_rewritable_reason": note,
        }


def ensure_state_dir(root: Path) -> Path:
    """§8.3 rule 6: create ``<root>/.specagent/`` and, before its first use,
    a ``.gitignore`` containing ``*`` so transcripts and suggestions are never
    committed regardless of the user's own ignore rules."""
    state = Path(root) / ".specagent"
    state.mkdir(parents=True, exist_ok=True)
    marker = state / ".gitignore"
    if not marker.exists():
        marker.write_text("*\n", encoding="utf-8")
    return state
