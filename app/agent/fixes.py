"""Diff builder for fix suggestions (v1 design §8.8, task 15).

``build_unified_diff`` must be applicable by ``git apply`` and ``patch -p1``
for every line-ending style: the dominant EOL of the *old* file wins, so a
model that always emits ``\\n`` cannot silently convert a CRLF file. Lines are
split keeping terminators (``str.splitlines`` would also split on \\x0b \\x0c
\\x1c…). Pure module — file writes for suggestions live in the tool handler
(``_h_write_fix_suggestion``), which is the only allowed writer.
"""
import datetime
import difflib
import hashlib
import re


class DiffError(ValueError):
    """Raised with a stable reason ("not_utf8", "no_change") by the builder."""


def suggestion_id(diff_text: str, when: datetime.datetime | None = None) -> str:
    stamp = (when or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y%m%dT%H%M%S")
    return f"fix_{stamp}_{hashlib.sha1(diff_text.encode('utf-8')).hexdigest()[:6]}"


def build_unified_diff(rel: str, old_bytes: bytes, new_text: str) -> str:
    """Unified diff old→new for ``rel`` (POSIX-style a/ b/ paths), applicable
    by git apply. Raises :class:`DiffError` with reason ``not_utf8`` when the
    old bytes are not strict UTF-8 and ``no_change`` when both sides are
    byte-identical after EOL conversion."""
    try:
        old = old_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise DiffError("not_utf8") from None
    crlf = old.count("\r\n")
    lf = old.count("\n") - crlf
    eol = "\r\n" if crlf > lf else "\n"
    new = new_text.replace("\r\n", "\n").replace("\n", eol)
    if new.encode("utf-8") == old_bytes:
        raise DiffError("no_change")
    rel = rel.replace("\\", "/")
    a_lines = re.findall(r"[^\n]*\n|[^\n]+", old)
    b_lines = re.findall(r"[^\n]*\n|[^\n]+", new)
    out = []
    for line in difflib.unified_diff(a_lines, b_lines, fromfile=f"a/{rel}",
                                     tofile=f"b/{rel}", lineterm="\n"):
        if line.startswith((" ", "-", "+")) and not line.endswith("\n"):
            out.append(line + "\n\\ No newline at end of file\n")
        else:
            out.append(line)
    return "".join(out)
