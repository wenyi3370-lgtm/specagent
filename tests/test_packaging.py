"""Static packaging checks (v1 design §3.1, task 1).

No TOML parser ships with Python 3.10, so the pyproject assertions are regex
checks on the file text. The offline wheel build is reported separately as
UNVERIFIED (the venv's setuptools predates `[project]` support).
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_pytest_config_present():
    assert re.search(r'^testpaths = \["tests"\]', PYPROJECT, re.M)
    assert re.search(r'^pythonpath = \["\."\]', PYPROJECT, re.M)


def test_package_data_includes_static_files():
    assert re.search(r"\[tool\.setuptools\.package-data\]", PYPROJECT)
    assert re.search(r'^app = \["static/\*"\]', PYPROJECT, re.M)


def test_every_package_with_init_is_listed():
    block = re.search(r"^packages = \[(.*?)\]", PYPROJECT, re.S | re.M)
    assert block, "explicit packages list missing"
    listed = set(re.findall(r'"([a-z_.]+)"', block.group(1)))
    app_dir = ROOT / "app"
    expected = {"cli"} | {
        p.parent.relative_to(ROOT).as_posix().replace("/", ".")
        for p in app_dir.rglob("__init__.py")
    }
    missing = expected - listed
    assert not missing, f"packages missing from pyproject.toml: {sorted(missing)}"


def test_static_dashboard_file_exists():
    assert (ROOT / "app" / "static" / "index.html").is_file()


def test_dev_extra_provides_pytest():
    # tests.yml installs `.[dev]`; the extra must actually contain pytest.
    assert re.search(r"dev = \[\"pytest", PYPROJECT)
