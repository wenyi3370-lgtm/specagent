"""Test environment isolation: deterministic, no LLM compiler, no external agent.

Set env BEFORE any test module imports app.main (which builds the Store).
"""
import os
import tempfile

_DB = os.path.join(tempfile.mkdtemp(prefix="specagent-tests-"), "test.db")
for suffix in ("", "-wal", "-shm"):
    try:
        os.remove(_DB + suffix)
    except FileNotFoundError:
        pass
os.environ["SPECAGENT_DB"] = _DB
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("TARGET_AGENT_URL", None)
os.environ.pop("TARGET_AGENT_TOKEN", None)
