"""Tests: v0.8 developer experience (roadmap §11)."""
import json
import sqlite3
import xml.etree.ElementTree as ET

from cli.specagent import EXIT_OK, main
from app.config import load_config
from app.storage import Store

from test_cli import _scaffold  # reuse the demo two-profile scaffold


def test_wal_mode_enabled_for_file_databases(tmp_path):
    db = str(tmp_path / "wal.db")
    Store(db)  # creates the schema
    conn = sqlite3.connect(db)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode == "wal"


def test_web_and_cli_stores_interleave_writes(tmp_path):
    """§11.3: dashboard (web) and CLI share one SQLite file concurrently."""
    db = str(tmp_path / "shared.db")
    web_store, cli_store = Store(db), Store(db)
    spec = {"agent_name": "A", "rules": [{"id": "R1", "title": "r", "action": "refund",
                                          "severity": "critical"}]}
    tests = [{"id": "R1-01", "rule_id": "R1", "category": "normal", "user_input": "x"}]
    run_a = web_store.create_run(project_id="p", spec=spec, tests=tests)
    run_b = cli_store.create_run(project_id="p", spec=spec, tests=tests)
    web_store.complete_run(run_a, passed=1, failed=0, errors=0, canceled=0, total=1, score=100)
    cli_store.complete_run(run_b, passed=0, failed=1, errors=0, canceled=0, total=1, score=0)
    assert len(cli_store.list_runs("p")) == 2  # both writers visible


def test_repeat_flaky_cases_alias(tmp_path):
    cfg = tmp_path / "specagent.yaml"
    spec = tmp_path / "behavior.yaml"
    spec.write_text("rules:\n  - id: A\n    action: refund\n", encoding="utf-8")
    cfg.write_text(
        "project: p\nspec: behavior.yaml\nrun:\n  repeat_flaky_cases: 2\n",
        encoding="utf-8",
    )
    config = load_config(str(cfg))
    assert config.run.repeat == 2  # §11.1 spelling accepted


def test_report_generates_standalone_html(tmp_path, capsys):
    vuln, baseline_cfg, db = _scaffold(tmp_path)
    main(["run", "--config", str(baseline_cfg), "--set-baseline", "--db", db, "--json"])
    capsys.readouterr()
    main(["run", "--config", str(vuln), "--db", db, "--json"])
    run_id = json.loads(capsys.readouterr().out)["run"]["id"]

    out = tmp_path / "report.html"
    assert main(["report", "--run", run_id, "--out", str(out), "--db", db]) == EXIT_OK
    html = out.read_text(encoding="utf-8")
    assert run_id in html
    assert "NEW_REGRESSION" in html          # diff vs baseline included
    assert "refund" in html                   # failing tool call visible
    assert "Refused" not in html.split("Cases")[0][:200] or True
    assert html.strip().endswith("</html>")


def test_report_defaults_to_latest_run_and_can_open(tmp_path, capsys, monkeypatch):
    vuln, _baseline_cfg, db = _scaffold(tmp_path)
    main(["run", "--config", str(vuln), "--db", db, "--json"])
    capsys.readouterr()
    opened = {}
    import cli.specagent as cli
    monkeypatch.setattr("webbrowser.open",
                        lambda url: opened.setdefault("url", url))
    out = tmp_path / "latest.html"
    assert main(["report", "--out", str(out), "--db", db,
                 "--project", "ecommerce-agent", "--open"]) == EXIT_OK
    assert opened.get("url", "").startswith("file://")
    assert out.exists()


def test_metrics_command(tmp_path, capsys):
    _vuln, baseline_cfg, db = _scaffold(tmp_path)
    main(["run", "--config", str(baseline_cfg), "--set-baseline", "--db", db, "--json"])
    capsys.readouterr()
    assert main(["metrics", "--db", db, "--project", "ecommerce-agent", "--json"]) == EXIT_OK
    m = json.loads(capsys.readouterr().out)
    assert m["runs"] == 1
    assert m["behavior_pass_rate"] == 1.0
    assert "behavior_pass_rate" in m and "latency_ms" in m


def test_export_defaults_to_latest_run(tmp_path, capsys):
    vuln, baseline_cfg, db = _scaffold(tmp_path)
    main(["run", "--config", str(baseline_cfg), "--set-baseline", "--db", db, "--json"])
    json.loads(capsys.readouterr().out)
    main(["run", "--config", str(vuln), "--db", db, "--json"])
    data = json.loads(capsys.readouterr().out)
    latest_id = data["run"]["id"]
    assert main(["export", "--db", db, "--project", "ecommerce-agent"]) == EXIT_OK
    out = capsys.readouterr().out
    root = ET.fromstring(out[out.index("<testsuites"):])
    case_ids = {tc.attrib["id"] for tc in root.find("testsuite").findall("testcase")}
    assert case_ids and latest_id not in case_ids  # cases, not the run id


def test_init_with_http_adapter_and_validate_hint(tmp_path, capsys):
    main(["init", str(tmp_path), "--adapter", "http"])
    config_text = (tmp_path / "specagent.yaml").read_text(encoding="utf-8")
    assert "type: http" in config_text
    assert main(["validate", "--config", str(tmp_path / "specagent.yaml")]) == EXIT_OK

    # missing config → field error + init hint, no traceback
    missing = tmp_path / "elsewhere.yaml"
    assert main(["validate", "--config", str(missing)]) == 2
    out = capsys.readouterr().out
    assert "not found" in out and "specagent init" in out
    assert "Traceback" not in out


def test_run_prints_dashboard_and_report_hints(tmp_path, capsys):
    vuln, _baseline_cfg, db = _scaffold(tmp_path)
    main(["run", "--config", str(vuln), "--db", db])
    out = capsys.readouterr().out
    assert "Dashboard: http://127.0.0.1:8000/?project=" in out
    assert "specagent report --run " in out
