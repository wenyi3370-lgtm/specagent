"""Agent adapters (roadmap v0.4 §7).

An adapter owns exactly one thing: *how to run the agent and harvest its
trace*. It never judges (§7.4) — whatever the framework, the output is the
same normalized AgentExecution, and only app.judge looks at it.

Adapters:
    demo      built-in deterministic agent (vulnerable | patched variants)
    http      TARGET_AGENT_URL webhook contract (P0, universal)
    openai    agents defined as an OpenAIAgentDefinition (P1, Responses API)
    langgraph compiled LangGraph graphs via astream_events (P1)
"""
import importlib
import logging
import os
import sys
from typing import Any

from ..config import AdapterConfig
from ..errors import SpecValidationError
from ..models import AgentExecution, TestCase
from .base import AgentAdapter, ExecutionContext
from .demo_adapter import DemoAdapter
from .http_adapter import HttpAdapter
from .langgraph_adapter import LangGraphAdapter
from .openai_adapter import OpenAIAgentDefinition, OpenAIResponsesAdapter

logger = logging.getLogger("specagent.adapters")

__all__ = [
    "AgentAdapter", "ExecutionContext", "DemoAdapter", "HttpAdapter",
    "OpenAIAgentDefinition", "OpenAIResponsesAdapter", "LangGraphAdapter",
    "load_agent_object", "resolve_adapter",
]


def load_agent_object(path: str, base_dir: str | None = None) -> Any:
    """Load ``module:attribute`` — the user's agent definition — via importlib.

    ``base_dir`` (the specagent.yaml directory) is prepended to sys.path so
    example projects can reference local modules with plain module names.
    """
    module_name, _, attr = (path or "").partition(":")
    if not module_name or not attr:
        raise SpecValidationError([
            f"adapter.agent: expected 'module:attribute' (e.g. 'myagent:AGENT'), got {path!r}"
        ])
    added = False
    if base_dir and base_dir not in sys.path:
        sys.path.insert(0, base_dir)
        added = True
    try:
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise SpecValidationError([f"adapter.agent: cannot import module {module_name!r}: {exc}"]) from None
        try:
            return getattr(module, attr)
        except AttributeError:
            raise SpecValidationError([f"adapter.agent: module {module_name!r} has no attribute {attr!r}"]) from None
    finally:
        if added:
            sys.path.remove(base_dir)


def _openai_adapter_from(config: AdapterConfig, base_dir: str | None) -> OpenAIResponsesAdapter:
    if not config.agent:
        raise SpecValidationError([
            "adapter.agent: 'module:attribute' pointing to an OpenAIAgentDefinition "
            "is required for adapter.type=openai"
        ])
    obj = load_agent_object(config.agent, base_dir)
    if isinstance(obj, dict):
        obj = OpenAIAgentDefinition(**obj)
    if not isinstance(obj, OpenAIAgentDefinition):
        raise SpecValidationError([
            f"adapter.agent: expected an OpenAIAgentDefinition, got {type(obj).__name__}"
        ])
    if config.model:
        obj.model = config.model
    if config.instructions:
        obj.instructions = config.instructions
    return OpenAIResponsesAdapter(obj)


def _langgraph_adapter_from(config: AdapterConfig, base_dir: str | None) -> LangGraphAdapter:
    if not config.agent:
        raise SpecValidationError([
            "adapter.agent: 'module:attribute' pointing to a compiled graph "
            "is required for adapter.type=langgraph"
        ])
    try:
        graph = load_agent_object(config.agent, base_dir)
    except SpecValidationError:
        raise
    try:
        return LangGraphAdapter(graph)
    except TypeError as exc:
        raise SpecValidationError([f"adapter.agent: {exc}"]) from None


def resolve_adapter(
    config: AdapterConfig | None = None,
    *,
    agent: str = "auto",
    agent_variant: str | None = None,
    base_dir: str | None = None,
) -> AgentAdapter:
    """Build an adapter from a specagent.yaml adapter section, or from the
    simple agent selector used by the API ('auto' | 'demo' | 'http')."""
    if config is not None:
        if config.type == "demo":
            return DemoAdapter(config.variant or agent_variant)
        if config.type == "http":
            return HttpAdapter(config.endpoint_env)
        if config.type == "openai":
            return _openai_adapter_from(config, base_dir)
        if config.type == "langgraph":
            return _langgraph_adapter_from(config, base_dir)
        raise SpecValidationError([f"adapter.type: unknown adapter type {config.type!r}"])

    if agent == "http" or (agent == "auto" and os.getenv("TARGET_AGENT_URL")):
        return HttpAdapter()
    if agent in ("demo", "auto", ""):
        return DemoAdapter(agent_variant)
    raise SpecValidationError([
        f"agent={agent!r} needs a specagent.yaml 'adapter:' section with an agent import path"
    ])
