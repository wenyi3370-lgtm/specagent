"""Export the declared public contract in an isolated, offline subprocess.

--write explicitly replaces the reviewable snapshot; --check only compares.
No .env, project configuration, existing database, agent or model is opened.
"""
import argparse
import inspect
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "docs/contracts/v1-candidate.json"


def cli_contract(parser):
    actions = []
    commands = {}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            commands = {name: cli_contract(child) for name, child in sorted(action.choices.items())}
            actions.append({"kind": "subcommands", "dest": action.dest, "required": action.required})
            continue
        actions.append({"kind": type(action).__name__, "dest": action.dest,
            "flags": action.option_strings, "required": action.required, "nargs": action.nargs,
            "default": action.default, "const": action.const,
            "type": getattr(action.type, "__name__", None),
            "choices": list(action.choices) if action.choices is not None else None})
    groups = [{"required": g.required, "members": [a.dest for a in g._group_actions]}
              for g in parser._mutually_exclusive_groups]
    return {"actions": actions, "commands": commands, "exclusive_groups": groups}


def capture():
    # Executed only by the isolated child. Network access is forbidden here.
    sys.path.insert(0, str(ROOT))
    import socket
    def offline(*args, **kwargs):
        raise RuntimeError("Contract export must remain offline")
    socket.socket.connect = offline
    socket.socket.connect_ex = offline
    socket.getaddrinfo = offline
    import dotenv
    dotenv.load_dotenv = lambda *a, **k: False
    from app import models
    from app.config import SpecAgentConfig
    from app.adapters.base import AgentAdapter, ExecutionContext
    from app.main import app, store
    from app.spec_yaml import CONSTRAINT_TYPE_NAMES
    from app.verify import VERDICT_ORDER, VERDICT_EXIT_OK
    from cli.specagent import (build_parser, EXIT_OK, EXIT_GATE_FAILED,
                              EXIT_CONFIG_ERROR, EXIT_AGENT_STOPPED)
    import yaml
    try:
        openapi = app.openapi()
        openapi["info"]["version"] = "PACKAGE_VERSION"
        model_names = ("BehaviorSpec", "TestCase", "TraceEvent", "AgentExecution", "TestResult", "DiffSummary")
        schemas = {name: getattr(models, name).model_json_schema() for name in model_names}
        schemas["SpecAgentConfig"] = SpecAgentConfig.model_json_schema()
        signature = inspect.signature(AgentAdapter.execute)
        adapter = {"async": inspect.iscoroutinefunction(AgentAdapter.execute),
            "parameters": [{"name": p.name, "kind": p.kind.name,
                            "annotation": getattr(p.annotation, "__name__", str(p.annotation))}
                           for p in signature.parameters.values()],
            "return": signature.return_annotation.__name__,
            "context_fields": list(ExecutionContext.__dataclass_fields__)}
        action = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
        return {"contract_version": "1.0-candidate", "based_on_release": "v0.11",
            "openapi": openapi, "schemas": schemas, "cli": cli_contract(build_parser()),
            "adapter": adapter, "constraint_types": list(CONSTRAINT_TYPE_NAMES),
            "action": {k: action[k] for k in ("inputs", "outputs")},
            "exit_codes": {"ok": EXIT_OK, "gate_failed": EXIT_GATE_FAILED,
                           "configuration_error": EXIT_CONFIG_ERROR, "agent_stopped": EXIT_AGENT_STOPPED},
            "verify": {"ordered_verdicts": list(VERDICT_ORDER), "exit_zero": sorted(VERDICT_EXIT_OK)}}
    finally:
        store._engine.dispose()


def isolated_capture():
    with tempfile.TemporaryDirectory(prefix="specagent-contract-") as temporary:
        # A fresh process prevents imported globals from leaking user state.
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("SPECAGENT_", "OPENAI_", "DEEPSEEK_", "TARGET_AGENT_"))}
        env.update(SPECAGENT_DB=str(Path(temporary) / "contract.db"),
                   SPECAGENT_SKIP_DOTENV="1", SPECAGENT_AUTH_MODE="shared",
                   PYTHONIOENCODING="utf-8")
        process = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker"],
            cwd=temporary, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
        if process.returncode:
            raise RuntimeError("Isolated contract export failed")
        return json.loads(process.stdout)


def changed_sections(expected, actual):
    return [key for key in sorted(expected.keys() | actual.keys())
            if expected.get(key) != actual.get(key)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(capture(), ensure_ascii=False, sort_keys=True))
        return 0
    actual = isolated_capture()
    if args.write:
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps(actual, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        print("Wrote docs/contracts/v1-candidate.json; review the diff before committing")
        return 0
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    changed = changed_sections(expected, actual)
    if changed:
        print("Contract drift in: " + ", ".join(changed), file=sys.stderr)
        return 1
    print("Declared v1 candidate contract matches")
    return 0


if __name__ == "__main__":
    sys.exit(main())
