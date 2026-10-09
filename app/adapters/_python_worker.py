"""Private pipe protocol. Each invocation imports and executes one target."""
import asyncio
import contextlib
import inspect
import json
import os
import sys
import importlib.abc
import importlib.machinery
from pathlib import Path

import cloudpickle


class FreshProjectImports(importlib.abc.MetaPathFinder):
    def __init__(self, root):
        self.root = Path(root).resolve()

    def find_spec(self, fullname, path=None, target=None):
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec and spec.origin and spec.origin.endswith('.py') and Path(spec.origin).resolve().is_relative_to(self.root):
            loader = importlib.machinery.SourceFileLoader(fullname, spec.origin)
            loader.get_code = lambda name: compile(loader.get_data(spec.origin), spec.origin, 'exec')
            spec.loader = loader
            return spec
        return None


def main():
    source, sink = sys.stdin.buffer, sys.stdout.buffer
    request = cloudpickle.loads(source.read())
    try:
        # Target stdout is not the protocol, and is never forwarded to logs.
        with open(os.devnull, 'w') as discard, contextlib.redirect_stdout(discard), contextlib.redirect_stderr(discard):
            if 'path' in request:
                from app._python_target import load_target
                base = request.get('base_dir')
                if base:
                    sys.meta_path.insert(0, FreshProjectImports(base))
                fn = load_target(request['path'], base)
            else:
                fn = request['fn']
            if request.get('validate'):
                raw = None
            else:
                sig = inspect.signature(fn)
                accepts_all = any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())
                accepted = {p.name for p in sig.parameters.values() if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)}
                kwargs = {k: v for k, v in request['kwargs'].items() if accepts_all or k in accepted}
                raw = fn(**kwargs)
                if inspect.isawaitable(raw):
                    raw = asyncio.run(raw)
                if hasattr(raw, 'model_dump'):
                    raw = raw.model_dump()
                if isinstance(raw, dict) and raw.get('trace'):
                    raw['trace'] = [e.model_dump() if hasattr(e, 'model_dump') else e for e in raw['trace']]
        reply = {'ok': True, 'value': raw}
    except BaseException as exc:
        from app.agent.sandbox import redact_text
        reply = {'ok': False, 'type': type(exc).__name__, 'message': redact_text(str(exc)),
                 'errors': getattr(exc, 'errors', None)}
    sink.write(json.dumps(reply, ensure_ascii=False, default=str).encode('utf-8'))
    sink.flush()


if __name__ == '__main__':
    main()
