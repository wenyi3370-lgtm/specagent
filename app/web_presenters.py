"""Transport-only redaction: CLI retains paths; web never reveals server secrets."""
import os
import re
from pathlib import Path

from fastapi.encoders import jsonable_encoder

from .project import project_config_path
from .config import load_config


def web_payload(value):
    """Redact data recursively, including exception messages and stored traces.
    This does not change verdicts, counts, classifications or gate policy."""
    config = Path(project_config_path()).resolve()
    replacements = {str(config): config.name, str(config.parent): "[project]"}
    for name, text in os.environ.items():
        if text and (re.search(r"TOKEN|KEY|SECRET|PASSWORD|URL|DSN", name, re.I)
                     or name in ("SPECAGENT_DB", "SPECAGENT_PROJECT_CONFIG", "SPECAGENT_ALLOWED_HOSTS")):
            replacements[text] = "[redacted]"
    for host in os.getenv("SPECAGENT_ALLOWED_HOSTS", "").split(","):
        if host.strip():
            replacements[host.strip()] = "[redacted]"
    try:
        endpoint = os.getenv(load_config(str(config)).adapter.endpoint_env)
        if endpoint:
            replacements[endpoint] = "[redacted]"
    except Exception:
        pass  # Validation errors are themselves being sanitized.
    replacements[str(config)] = config.name
    replacements[str(config.parent)] = "[project]"

    def clean(item):
        if isinstance(item, str):
            for source in sorted(replacements, key=len, reverse=True):
                if len(source) < 8:
                    item = re.sub(r"(?<!\w)" + re.escape(source) + r"(?!\w)",
                                  lambda _: replacements[source], item)
                else:
                    item = item.replace(source, replacements[source])
                    item = item.replace(source.replace("\\", "/"), replacements[source])
            # Credentials in arbitrary URLs, absolute Windows/UNC and POSIX paths.
            item = re.sub(r"(https?://)[^\s/@]+@", r"\1[redacted]@", item)
            item = re.sub(r"(?i)(?:[a-z]:[\\/]|\\\\)[^\s\"<>]*", "[path]", item)
            item = re.sub(r"(?<![\w:/])/(?:[^\s/\"<>]+/)+[^\s\"<>]*", "[path]", item)
            return item
        if isinstance(item, list):
            return [clean(x) for x in item]
        if isinstance(item, dict):
            return {k: clean(v) for k, v in item.items()}
        return item

    return clean(jsonable_encoder(value))
