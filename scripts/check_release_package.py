"""Build tracked sources offline and check installed wheels outside the checkout.

Dependencies must already be installed. Never loads .env or an external agent.
The optional previous ref tests an actual wheel-to-wheel SQLite upgrade.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_FILES = {"pyproject.toml", "README.md", "LICENSE"}
SMOKE = r'''
import contextlib, importlib.metadata, io, json, os, pathlib, sys
import dotenv
dotenv.load_dotenv = lambda *args, **kwargs: False
import app
import cli.specagent
installed = pathlib.Path(os.environ["SA_INSTALLED"])
assert pathlib.Path(app.__file__).is_relative_to(installed)
assert pathlib.Path(cli.specagent.__file__).is_relative_to(installed)
distribution = importlib.metadata.distribution("specagent")
assert distribution.version == app.__version__
assert any(e.name == "specagent" and e.value == "cli.specagent:main"
           for e in distribution.entry_points)
static = pathlib.Path(app.__file__).parent / "static"
for name in json.loads(os.environ["SA_STATIC_FILES"]):
    assert (static / name).is_file(), name
output = io.StringIO()
with contextlib.redirect_stdout(output):
    try:
        cli.specagent.main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
assert app.__version__ in output.getvalue()
from fastapi.testclient import TestClient
from app.main import app as api
with TestClient(api) as client:
    assert client.get("/api/health").json()["version"] == app.__version__
    assert client.get("/").status_code == 200
print(json.dumps({"version": app.__version__, "installed_import": True,
                  "cli_version": True, "health_version": True,
                  "static_files": json.loads(os.environ["SA_STATIC_FILES"])}))
'''
CLI = '''
import dotenv
dotenv.load_dotenv = lambda *args, **kwargs: False
from cli.specagent import main
raise SystemExit(main(__import__("sys").argv[1:]))
'''
BASELINE = '''
import dotenv
dotenv.load_dotenv = lambda *args, **kwargs: False
from app.storage import Store
import hashlib, json, os
store = Store(os.environ["SPECAGENT_DB"])
baseline = store.get_baseline("fincare-agent")
assert baseline and baseline["passed"] == 43
snapshot = {k: baseline[k] for k in ("spec", "tests", "results")}
print(json.dumps({"baseline_id": baseline["id"], "baseline_passed": baseline["passed"],
                  "snapshot_sha256": hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()}))
'''


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def build(root, label, ref=None):
    source = root / label / "source"
    source.mkdir(parents=True)
    names = (git("ls-tree", "-rz", "--name-only", ref) if ref else
             git("ls-files", "-z")).decode().split("\0")
    selected = [n for n in names if n in PACKAGE_FILES or n.startswith(("app/", "cli/"))]
    for name in selected:
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if ref:
            target.write_bytes(git("show", f"{ref}:{name}"))
        else:
            shutil.copyfile(ROOT / name, target)
    subprocess.run([sys.executable, "-c",
                    'import setuptools.build_meta as b; b.build_wheel("dist"); b.build_sdist("dist")'],
                   cwd=source, check=True, stdout=subprocess.DEVNULL)
    wheel = next((source / "dist").glob("*.whl"))
    installed = root / label / "installed"
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-index", "--no-deps",
                    "--target", str(installed), str(wheel)], cwd=root,
                   check=True, stdout=subprocess.DEVNULL)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("SPECAGENT_", "OPENAI_", "TARGET_AGENT_", "SA_"))}
    env.update(PYTHONPATH=str(installed), PYTHONIOENCODING="utf-8",
               SPECAGENT_DB=str(root / "upgrade.db"), SPECAGENT_AUTH_MODE="shared",
               SA_INSTALLED=str(installed), SA_STATIC_FILES=json.dumps(
                   [n[len("app/static/"):] for n in selected if n.startswith("app/static/")]))
    return source, wheel, env


def child(code, env, cwd, args=(), exit_code=0):
    result = subprocess.run([sys.executable, "-c", code, *args], cwd=cwd, env=env,
                            capture_output=True, text=True, encoding="utf-8")
    if result.returncode != exit_code:
        raise RuntimeError(f"package check exit {result.returncode}, expected {exit_code}: "
                           f"{result.stderr}")
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-ref", help="Published Git ref to build and upgrade from")
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="specagent-release-") as directory:
        root = Path(directory)
        source, _, env = build(root, "current")
        smoke_env = dict(env, SPECAGENT_DB=str(root / "smoke-current.db"))
        evidence = child(SMOKE, smoke_env, root)
        if args.previous_ref:
            _, _, previous_env = build(root, "previous", args.previous_ref)
            previous = child(SMOKE, dict(previous_env, SPECAGENT_DB=str(root / "smoke-previous.db")), root)
            fixture = root / "fincare-agent"
            for name in git("ls-files", "-z", "examples/fincare-agent").decode().split("\0"):
                if name:
                    target = fixture / Path(name).relative_to("examples/fincare-agent")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(ROOT / name, target)
            before = child(CLI, previous_env, root, ["run", "--config",
                           str(fixture / "specagent.baseline.yaml"), "--set-baseline", "--json"])
            baseline = child(BASELINE, previous_env, root)
            after = child(CLI, env, root, ["run", "--config", str(fixture / "specagent.yaml"),
                          "--json"], exit_code=1)
            preserved = child(BASELINE, env, root)
            assert baseline == preserved
            assert before["run"]["passed"] == 43
            assert after["diff"]["new_regressions"] == 4
            assert len(after["gate"]["violations"]) == 4
            evidence["upgrade"] = {"from_version": previous["version"],
                                   "to_version": evidence["version"],
                                   "baseline_preserved": True, "baseline_passed": 43,
                                   "new_regressions": 4, "gate_exit": 1}
        args.out.mkdir(parents=True, exist_ok=True)
        artifacts = []
        for artifact in sorted((source / "dist").iterdir()):
            target = args.out / artifact.name
            shutil.copyfile(artifact, target)
            artifacts.append({"name": target.name, "bytes": target.stat().st_size,
                              "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
        evidence["artifacts"] = artifacts
        evidence["dependencies"] = "reused installed dependencies; offline no-deps install"
        (args.out / "release-evidence.json").write_text(
            json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
