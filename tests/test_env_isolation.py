"""`.env` isolation self-test (v1 design §1.4, task 1).

conftest binds a no-op `dotenv.load_dotenv` and blanks auth/LLM variables, so a
developer's `.env` can never turn auth on or re-enable LLM paths inside tests.
The `.env` used here is the test's own fixture inside `tmp_path` — never a
developer file.
"""
import os


def test_load_dotenv_is_a_noop_and_token_stays_blank(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "SPECAGENT_API_TOKEN=leak\nOPENAI_API_KEY=leak\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    from app import main  # bound name comes from the patched dotenv module

    assert main.load_dotenv() is False  # the no-op, not the real loader
    assert os.environ["SPECAGENT_API_TOKEN"] == ""
    assert os.environ["OPENAI_API_KEY"] == ""
    # `auth.configured_token() is None` is asserted in tests/test_auth.py (task 4
    # introduces the module); the isolation itself is what this file pins.


def test_blank_means_unset_for_every_guarded_variable():
    for var in ("SPECAGENT_API_TOKEN", "TARGET_AGENT_URL", "TARGET_AGENT_TOKEN",
                "SPECAGENT_AGENT_MODEL", "SPECAGENT_PROJECT_CONFIG",
                "SPECAGENT_AGENT_API_INSECURE"):
        assert os.environ.get(var, "") == ""
