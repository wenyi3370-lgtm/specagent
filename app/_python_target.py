"""Lightweight Python target loading and signature validation for workers."""
import importlib
import inspect
import sys

from .errors import SpecValidationError


def validate_signature(fn, path):
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError) as exc:
        raise SpecValidationError([f'adapter.agent: cannot inspect signature of {path!r}: {exc}']) from None
    params = sig.parameters.values()
    has_var_kwargs = any(p.kind == p.VAR_KEYWORD for p in params)
    names = {p.name for p in params if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)}
    if 'message' not in names and not has_var_kwargs:
        raise SpecValidationError([f"adapter.agent: {path} must accept a 'message' parameter (or **kwargs); got signature {sig}"])


def load_target(path, base):
    module_name, _, attr = (path or '').partition(':')
    if not module_name or not attr:
        raise SpecValidationError([f"adapter.agent: expected 'module:attribute' (e.g. 'myagent:AGENT'), got {path!r}"])
    if base:
        sys.path.insert(0, base)
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise SpecValidationError([f'adapter.agent: cannot import module {module_name!r}: {exc}']) from None
    try:
        fn = getattr(module, attr)
    except AttributeError:
        raise SpecValidationError([f'adapter.agent: module {module_name!r} has no attribute {attr!r}']) from None
    if not callable(fn):
        raise TypeError(f'expected a callable, got {type(fn).__name__}')
    validate_signature(fn, path)
    return fn
