"""specagent.yaml project configuration (roadmap §11.1)."""
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from .errors import SpecValidationError
from .models import Severity


class AdapterConfig(BaseModel):
    type: Literal["demo", "http", "openai", "langgraph"] = "demo"
    endpoint_env: str = "TARGET_AGENT_URL"
    variant: str | None = None   # demo agent variant (vulnerable | patched)
    # 'module:attribute' pointing at an OpenAIAgentDefinition (type=openai)
    # or a compiled LangGraph graph (type=langgraph). Resolved relative to the
    # specagent.yaml directory for local modules.
    agent: str | None = None
    model: str | None = None          # optional override for openai definitions
    instructions: str | None = None   # optional override for openai definitions
    # v0.7 SSRF allowlist: endpoint hostnames the HTTP adapter may contact
    # (merged with env SPECAGENT_ALLOWED_HOSTS). Empty = no extra restriction.
    allowed_hosts: list[str] = []


class RunOptions(BaseModel):
    concurrency: int = Field(default=4, ge=1, le=32)
    timeout_seconds: int = Field(default=30, ge=1, le=600)
    repeat: int = Field(default=1, ge=1, le=10)
    llm_expand: bool = False  # v0.5: LLM expansion of test cases (needs OPENAI_API_KEY)
    # v0.7 stability (roadmap §10.2): retries apply only to transport errors
    # and upstream 5xx — behavior FAILs are never retried.
    retries: int = Field(default=1, ge=0, le=5)
    max_trace_events: int = Field(default=200, ge=10, le=100000)
    max_response_chars: int = Field(default=20000, ge=1000, le=10000000)


class GateConfig(BaseModel):
    fail_on: list[Severity] = ["critical", "high"]


class SpecAgentConfig(BaseModel):
    project: str = "default"
    adapter: AdapterConfig = Field(default_factory=AdapterConfig)
    spec: str
    run: RunOptions = Field(default_factory=RunOptions)
    gate: GateConfig = Field(default_factory=GateConfig)
    db: str | None = None

    model_config = {"extra": "forbid"}


def load_config(path: str) -> SpecAgentConfig:
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except FileNotFoundError:
        raise SpecValidationError([f"config file not found: {path}"]) from None
    except yaml.YAMLError as exc:
        raise SpecValidationError([f"{path}: invalid YAML: {exc}"]) from None
    if not isinstance(data, dict):
        raise SpecValidationError([f"{path}: expected a mapping at the top level"])
    if not data.get("spec"):
        raise SpecValidationError([f"{path}.spec: path to the behavior spec YAML is required"])
    try:
        config = SpecAgentConfig.model_validate(data)
    except ValidationError as exc:
        from .spec_yaml import _format_validation_error
        raise SpecValidationError(_format_validation_error(exc, path)) from exc
    # Resolve a relative spec path against the config file's directory so the
    # CLI behaves the same from any working directory (CI included).
    spec_path = Path(config.spec)
    if not spec_path.is_absolute():
        config.spec = str(Path(path).resolve().parent / spec_path)
    return config
