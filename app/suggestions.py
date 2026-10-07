"""Server-rooted fix suggestion views and verification history (no patch application)."""
import datetime
import json
import re
import uuid
from pathlib import Path

ID_PATTERN = r"^fix_[0-9T]+_[0-9a-f]{6}$"
MAX_BYTES = 2_000_000


class SuggestionError(ValueError):
    def __init__(self, detail, status=422):
        super().__init__(detail)
        self.status = status


def valid_id(value):
    return isinstance(value, str) and len(value) <= 64 and re.fullmatch(ID_PATTERN, value) is not None


def _base(root):
    root = Path(root).resolve()
    state, base = root / ".specagent", root / ".specagent" / "suggestions"
    if state.resolve() != state or base.resolve() != base or not base.resolve().is_relative_to(root):
        raise SuggestionError("suggestion_path_denied", 403)
    return base


def suggestion_directory(root, sid):
    if not valid_id(sid):
        raise SuggestionError("invalid_suggestion_id")
    base = _base(root)
    directory = base / sid
    if directory.resolve() != directory or not directory.resolve().is_relative_to(base.resolve()):
        raise SuggestionError("suggestion_path_denied", 403)
    if not directory.is_dir():
        raise SuggestionError("suggestion_not_found", 404)
    return directory


def _bytes(directory, name):
    path = directory / name
    if path.resolve() != path or not path.resolve().is_relative_to(directory.resolve()):
        raise SuggestionError("suggestion_path_denied", 403)
    if not path.is_file():
        raise SuggestionError("suggestion_not_found", 404)
    if path.stat().st_size > MAX_BYTES:
        raise SuggestionError("suggestion_too_large", 413)
    try:
        return path.read_bytes()
    except OSError:
        raise SuggestionError("suggestion_unreadable") from None


def _json(directory, name):
    raw = _bytes(directory, name)
    try:
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (UnicodeError, ValueError):
        raise SuggestionError("suggestion_record_invalid") from None


def load_suggestion(root, sid):
    directory = suggestion_directory(root, sid)
    record = _json(directory, "suggestion.json")
    if record.get("id", sid) != sid:
        raise SuggestionError("suggestion_record_invalid")
    if not isinstance(record.get("files", []), list):
        raise SuggestionError("suggestion_record_invalid")
    for item in record.get("files", []):
        rel = item.get("path", "") if isinstance(item, dict) else ""
        if not isinstance(rel, str) or not rel or rel.startswith(("/", "\\")) or ":" in rel or ".." in rel.replace("\\", "/").split("/"):
            raise SuggestionError("suggestion_record_invalid")
    return record


def diff_bytes(root, sid):
    return _bytes(suggestion_directory(root, sid), "fix.diff")


def verification_history(root, sid):
    directory = suggestion_directory(root, sid)
    history = []
    for path in sorted(directory.glob("verify-*.json"), key=lambda p: (p.stem[7:].split("-")[0], p.stat().st_mtime_ns)):
        if not re.fullmatch(r"verify-[0-9T]+(?:-[0-9a-f]+)?\.json", path.name):
            continue
        report = _json(directory, path.name)
        history.append({"record": path.name, "verified_at": path.name[7:-5], **report})
    return history


def suggestion_view(root, sid, *, include_diff=True):
    record = load_suggestion(root, sid)
    history = verification_history(root, sid)
    view = {"id": sid, "created_at": record.get("created_at"),
            "rule_id": record.get("rule_id"), "files": record.get("files", []),
            "pre_fix_run_id": record.get("pre_fix_run_id"),
            "diagnosis": record.get("diagnosis", ""), "diff_sha256": record.get("diff_sha256"),
            "latest_verdict": history[-1].get("verdict") if history else None,
            "verification_history": history,
            "apply_command": f"git apply .specagent/suggestions/{sid}/fix.diff"}
    if include_diff:
        try:
            view["diff"] = diff_bytes(root, sid).decode("utf-8")
        except UnicodeError:
            raise SuggestionError("suggestion_diff_invalid") from None
    return view


def list_suggestions(root):
    base = _base(root)
    return [suggestion_view(root, path.name, include_diff=False)
            for path in sorted(base.iterdir(), reverse=True) if valid_id(path.name)] if base.exists() else []


def suggestion_hints(root, run_id):
    return [{"id": item["id"], "files": len(item["files"]),
             "line": f"Suggested fixes: {item['id']} ({len(item['files'])} file(s)) — see: specagent suggestions show {item['id']}"}
            for item in list_suggestions(root) if item["pre_fix_run_id"] == run_id]


def save_verification(root, sid, result):
    directory = suggestion_directory(root, sid)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = directory / f"verify-{stamp}.json"
    if path.exists() or path.is_symlink():
        precise = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        path = directory / f"verify-{precise}-{uuid.uuid4().hex[:8]}.json"
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(result, ensure_ascii=False, indent=2))
    except OSError:
        raise SuggestionError("verify_record_write_failed") from None
    return path
