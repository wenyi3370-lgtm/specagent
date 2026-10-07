"""Validated spec previews shared by the CLI and dashboard; no file writes."""
import yaml

from .compiler import compile_spec
from .llm_client import make_client, resolve_model
from .spec_yaml import dump_spec_yaml, parse_spec

FALLBACK_WARNING = ("no OPENAI_API_KEY (or the LLM failed): used the deterministic "
                    "compiler — limited patterns, no constraints/probes")


def draft_spec(text: str, *, client=None, model=None) -> dict:
    model = model or resolve_model()
    spec = compile_spec(text, client=client, model=model)
    preview = dump_spec_yaml(spec)
    parse_spec(yaml.safe_load(preview))
    llm = spec.compiler.startswith("openai")
    return {"yaml": preview, "compiler": spec.compiler,
            "warnings": [] if llm else [FALLBACK_WARNING],
            "compiler_message": f"compiler: LLM ({model})" if llm else FALLBACK_WARNING}


def server_draft(text: str) -> dict:
    return draft_spec(text, client=make_client())
