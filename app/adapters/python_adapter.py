"""Run each Python callable in a fresh subprocess with a JSON result pipe.

The worker filters message/history/actor by signature. Timeouts and cancellation
terminate the worker and its process tree. Configured project modules are imported
only in the worker, including lazy sibling imports. Exceptions become per-case
ERROR results without retries. Target globals never mutate the host process.
"""
import asyncio
import builtins
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import cloudpickle

from ..errors import SpecValidationError
from .._python_target import validate_signature
from ..models import AgentExecution, TestCase, TraceEvent
from ..trace import normalize_trace_with_dropped
from .base import AgentAdapter, ExecutionContext


class PythonAdapter(AgentAdapter):
    name = "python"

    def __init__(self, fn, name: str, *, base_dir: str | None = None):
        self.fn = fn
        self.import_path = name if fn is None else None
        self.base_dir = str(Path(base_dir).resolve()) if base_dir else None
        self.name = f"python:{name}"  # label stored in runs.agent (§6.1)

    @staticmethod
    def validate_signature(fn, path: str) -> None:
        """Factory-side check (§6.1): the callable must accept ``message``
        (a parameter with that name, or ``**kwargs``); ``history``/``actor``
        are optional."""
        validate_signature(fn, path)

    async def execute(self, case: TestCase, context: ExecutionContext) -> AgentExecution:
        payload = self._request()
        payload['kwargs'] = {'message': case.user_input, 'history': list(case.history), 'actor': dict(case.actor)}
        request = cloudpickle.dumps(payload)
        process = self._start()
        communication = asyncio.create_task(asyncio.to_thread(process.communicate, request))
        try:
            stdout, _ = await asyncio.wait_for(asyncio.shield(communication), context.timeout_seconds)
            raw = self._reply(stdout, process.returncode)
        finally:
            await asyncio.to_thread(self._terminate, process)
            await asyncio.shield(communication)
        if isinstance(raw, AgentExecution):
            raw = raw.model_dump()
        if not isinstance(raw, dict):
            raise TypeError(f"python agent must return AgentExecution or dict, "
                            f"got {type(raw).__name__}")
        trace = [e.model_dump() if isinstance(e, TraceEvent) else e
                 for e in (raw.get("trace") or [])]
        events, dropped = normalize_trace_with_dropped(trace)
        return AgentExecution(
            response=raw.get("response", ""),
            trace=events,
            dropped_events=dropped,
            latency_ms=int(raw.get("latency_ms") or 0),
            raw=raw,
        )

    def _request(self):
        return ({'path': self.import_path, 'base_dir': self.base_dir} if self.import_path
                else {'fn': self.fn})

    @staticmethod
    def _start():
        env = dict(os.environ, SPECAGENT_SKIP_DOTENV='1')
        # The worker must import this installation even outside the checkout.
        env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2]) + os.pathsep + env.get('PYTHONPATH', '')
        process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('_python_worker.py'))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
            start_new_session=os.name != 'nt',
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW) if os.name == 'nt' else 0)
        if os.name == 'nt':
            from ._windows_job import attach
            try:
                process._close_job = attach(process)
            except BaseException:
                process.kill()
                process.wait()
                raise
        return process

    @staticmethod
    def _terminate(process):
        if os.name == 'nt':
            close = getattr(process, '_close_job', None)
            if close:
                process._close_job = None
                close()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)

    @staticmethod
    def _reply(stdout, returncode):
        if returncode or not stdout:
            raise RuntimeError('python worker exited without an execution result')
        reply = json.loads(stdout.decode('utf-8'))
        if not reply['ok']:
            if reply['type'] == 'SpecValidationError':
                raise SpecValidationError(reply['errors'])
            kind = getattr(builtins, reply['type'], RuntimeError)
            if not isinstance(kind, type) or not issubclass(kind, Exception):
                kind = RuntimeError
            raise kind(reply['message'])
        return reply['value']

    def validate_import(self):
        request = cloudpickle.dumps({**self._request(), 'validate': True})
        process = self._start()
        try:
            stdout, _ = process.communicate(request, timeout=30)
            self._reply(stdout, process.returncode)
        except subprocess.TimeoutExpired:
            raise SpecValidationError(['adapter.agent: import timed out after 30 seconds']) from None
        except TypeError as exc:
            raise SpecValidationError([f'adapter.agent: {exc}']) from None
        finally:
            self._terminate(process)
