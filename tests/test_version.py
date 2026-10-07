"""Release metadata consistency (v1 design §9.2, task 18)."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app import __version__
from app.main import app

ROOT = Path(__file__).resolve().parent.parent


def test_pyproject_and_package_versions_are_equal():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    declared = re.search(r'^version = "([^"]+)"', pyproject, re.M)
    assert declared, "pyproject.toml has no version"
    assert declared.group(1) == __version__


def test_release_version_is_0_10_0():
    assert __version__ == "0.10.0"


def test_health_reports_the_release_version():
    assert TestClient(app).get("/api/health").json()["version"] == "0.10.0"


def test_release_documents_exist_and_changelog_lists_the_release():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    for heading in ("## [v0.10]", "## [v0.9]", "## [v0.8.1]"):
        assert heading in changelog
    assert (ROOT / "README.en.md").is_file()
    assert (ROOT / "docs" / "demo-script.md").is_file()
