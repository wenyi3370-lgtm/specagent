"""Model/client resolution for the agent layer (v1 design §8.6, task 13).

Lives outside ``app/agent/`` so ``app.compiler`` callers can share it. The
OpenAI import is lazy and only happens when a key is configured (the SDK is
not installed in the test environment).
"""
import os

# The literal the repo already uses as its fallback model (app/compiler.py,
# app/llm_judge.py, .env.example). No new model names are invented; users must
# set a valid SPECAGENT_AGENT_MODEL / OPENAI_MODEL for their provider.
DEFAULT_MODEL = "gpt-5.5"


def resolve_model() -> str:
    return (os.getenv("SPECAGENT_AGENT_MODEL") or os.getenv("OPENAI_MODEL")
            or DEFAULT_MODEL)


def make_client():
    """None when OPENAI_API_KEY is unset/blank; otherwise a sync OpenAI client
    (the SDK reads OPENAI_API_KEY and OPENAI_BASE_URL natively)."""
    if not os.getenv("OPENAI_API_KEY"):
        return None
    from openai import OpenAI
    return OpenAI()
