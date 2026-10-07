"""Agent adapters (roadmap v0.4 §7; python adapter in v1 design §6.1).

An adapter owns exactly one thing: *how to run the agent and harvest its
trace*. It never judges (§7.4) — whatever the framework, the output is the
same normalized AgentExecution, and only app.judge looks at it.

Adapters:
    demo      built-in deterministic agent (vulnerable | patched variants)
    http      TARGET_AGENT_URL webhook contract (P0, universal)
    openai    agents defined as an OpenAIAgentDefinition (P1, Responses API)
    langgraph compiled LangGraph graphs via astream_events (P1)
    python    plain `module:function` callables (v1 design §6.1)
"""
import importlib
import importlib.util
import logging
import os
import sys
import threading
from typing import Any

from ..config import AdapterConfig
from ..errors import SpecValidationError
from ..models import AgentExecution, TestCase
from .base import AgentAdapter, ExecutionContext
from .demo_adapter import DemoAdapter
from .http_adapter import HttpAdapter
from .langgraph_adapter import LangGraphAdapter
from .openai_adapter import OpenAIAgentDefinition, OpenAIResponsesAdapter
from .python_adapter import PythonAdapter

logger = logging.getLogger("specagent.adapters")

__all__ = [
    "AgentAdapter", "ExecutionContext", "DemoAdapter", "HttpAdapter",
    "OpenAIAgentDefinition", "OpenAIResponsesAdapter", "LangGraphAdapter",
    "PythonAdapter", "load_agent_object", "resolve_adapter",
]

# Module freshness (§6.1): dashboard sessions may import agents concurrently —
# every sys.path mutation and the reload purge happen under this lock.
_IMPORT_LOCK = threading.RLock()


def _purge_stale_modules(base_dir: str, module_name: str) -> None:
    """Step 1 of the module-freshness flow (§6.1): drop cached modules whose
    code lives under `base_dir`, unlinking their cached bytecode best-effort
    (.pyc files are validated by source mtime in whole seconds plus size, so a
    same-size edit within the same second would otherwise import stale
    bytecode). Also evicts the target module name itself when it is cached
    from a *different* directory — two example projects both define an
    `agent.py`, and the first import would otherwise shadow the second (§6.2
    module-collision test)."""
    root = os.path.normcase(os.path.realpath(base_dir))
    prefix = root + os.sep
    evict: list[str] = []
    for name, module in list(sys.modules.items()):
        files = [f for f in (getattr(module, "__file__", None),) if f]
        files += [p for p in (getattr(module, "__path__", ()) or ()) if p]
        under = False
        for f in files:
            resolved = os.path.normcase(os.path.realpath(f))
            if resolved == root or resolved.startswith(prefix):
                under = True
                break
        if not under:
            continue
        evict.append(name)
        for f in files:  # best-effort bytecode unlink
            try:
                cache = importlib.util.cache_from_source(f)
                if cache:
                    os.unlink(cache)
            except OSError:
                pass
    top = module_name.split(".", 1)[0]
    if top and top in sys.modules and top not in evict:
        evict.append(top)  # cached from elsewhere — a plain-name collision
    for name in evict:
        sys.modules.pop(name, None)


def load_agent_object(path: str, base_dir: str | None = None,
                      reload: bool = False) -> Any:
    """Load ``module:attribute`` — the user's agent definition — via importlib.

    ``base_dir`` (the specagent.yaml directory) is prepended to sys.path so
    example projects can reference local modules with plain module names, and
    removed again afterwards.

    With ``reload=True`` (used by the python, openai and langgraph adapters,
    §6.1) previously imported modules whose files live under ``base_dir`` are
    evicted from ``sys.modules`` first — agent-verify flows rewrite project
    files in place, a plain ``importlib.import_module`` would return the stale
    entry (its sibling modules too, since base_dir no longer stays on
    sys.path), and two projects defining the same top-level module name (e.g.
    both examples ship an ``agent.py``) would shadow each other in-process.
    The whole sequence runs under :data:`_IMPORT_LOCK`.
    """
    module_name, _, attr = (path or "").partition(":")
    if not module_name or not attr:
        raise SpecValidationError([
            f"adapter.agent: expected 'module:attribute' (e.g. 'myagent:AGENT'), got {path!r}"
        ])
    with _IMPORT_LOCK:
        if reload and base_dir:
            _purge_stale_modules(base_dir, module_name)
            importlib.invalidate_caches()
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
    # reload=True: another project's same-named module (each example ships an
    # agent.py) must never shadow this one — same freshness rule as python.
    obj = load_agent_object(config.agent, base_dir, reload=True)
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
        graph = load_agent_object(config.agent, base_dir, reload=True)
    except SpecValidationError:
        raise
    try:
        return LangGraphAdapter(graph)
    except TypeError as exc:
        raise SpecValidationError([f"adapter.agent: {exc}"]) from None


def _python_adapter_from(config: AdapterConfig, base_dir: str | None) -> "PythonAdapter":
    """Build the python adapter (§6.1): a plain ``module:function`` callable.
    The import uses reload=True so agent-verify flows that rewrite project
    files in place always see fresh code."""
    if not config.agent:
        raise SpecValidationError([
            "adapter.agent: 'module:function' is required for adapter.type=python"
        ])
    obj = load_agent_object(config.agent, base_dir, reload=True)
    if not callable(obj):
        raise SpecValidationError([
            f"adapter.agent: expected a callable, got {type(obj).__name__}"
        ])
    PythonAdapter.validate_signature(obj, config.agent)
    return PythonAdapter(obj, config.agent)


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
        if config.type == "python":
            return _python_adapter_from(config, base_dir)
        raise SpecValidationError([f"adapter.type: unknown adapter type {config.type!r}"])

    if agent == "http" or (agent == "auto" and os.getenv("TARGET_AGENT_URL")):
        return HttpAdapter()
    if agent in ("demo", "auto", ""):
        return DemoAdapter(agent_variant)
    raise SpecValidationError([
        f"agent={agent!r} needs a specagent.yaml 'adapter:' section with an agent import path"
    ])
