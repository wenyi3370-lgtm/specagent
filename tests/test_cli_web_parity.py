"""CLI/web contracts on private copies of FinCare, with no external clients."""
import argparse
import json
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import drafts, main, project as core
from app.models import DiffSummary
from app.presenters import run_summary
from app.project import Project, RunOutcome, release_run, try_acquire_run
from app.spec_yaml import load_spec_file
from app.storage import Store
from cli import specagent as cli

# Each parser command must have an explicit web counterpart or a reason.
PARITY = {
    "init": {"cli_only": "Writes server configuration and spec files; browser is read-only."},
    "validate": {"web": "POST /api/project/validate"},
    "run": {"web": "POST /api/project/runs"},
    "baseline": {"web": "POST /api/runs/{run_id}/baseline"},
    "diff": {"web": "GET /api/diff"},
    "triage": {"web": "GET /api/runs/{run_id}/triage"},
    "verify": {"web": "POST /api/project/verify"},
    "export": {"web": "GET /api/runs/{run_id}/export"},
    "report": {"web": "GET /api/runs/{run_id}/report.html"},
    "metrics": {"web": "GET /api/metrics"},
    "draft": {"web": "POST /api/project/draft"},
    "agent": {"web": "POST /api/agent/sessions/{session_id}/messages"},
}


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "fincare"
    (root / "specs").mkdir(parents=True)
    source = main.BASE.parent / "examples" / "fincare-agent"
    (root / "specs" / "behavior.yaml").write_bytes((source / "specs" / "behavior.yaml").read_bytes())
    names = {kind: "parity_" + kind + "_" + uuid.uuid4().hex for kind in ("fixed", "broken")}
    for kind, name in names.items():
        (root / (name + ".py")).write_bytes((source / ("agent_fixed.py" if kind == "fixed" else "agent.py")).read_bytes())
    cfg = root / "specagent.yaml"
    pid = "parity-" + uuid.uuid4().hex

    def switch(kind):
        cfg.write_text(f"project: {pid}\nadapter:\n  type: python\n  agent: {names[kind]}:run_agent\n"
                       "spec: specs/behavior.yaml\ngate:\n  fail_on: [critical, high]\n", encoding="utf-8")

    switch("fixed")
    db = str(tmp_path / "parity.db")
    store = Store(db)
    monkeypatch.setenv("SPECAGENT_PROJECT_CONFIG", str(cfg))
    monkeypatch.setattr(main, "store", store)
    client = TestClient(main.app, client=("127.0.0.1", 50000), headers={"Host": "127.0.0.1:8765"})
    return SimpleNamespace(root=root, cfg=cfg, pid=pid, db=db, store=store, client=client, switch=switch)


def post(project, operation="runs", **body):
    response = project.client.post("/api/project/" + operation, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def command(capsys, project, verb, *options):
    args = [verb, *options]
    if verb in ("run", "verify", "validate"):
        args += ["--config", str(project.cfg)]
    if verb not in ("validate", "draft"):
        args += ["--db", project.db]
    capsys.readouterr()
    code = cli.main(args)
    return code, capsys.readouterr().out


def test_parser_commands_have_real_web_routes():
    commands = next(a for a in cli.build_parser()._actions if isinstance(a, argparse._SubParsersAction)).choices
    assert set(commands) == set(PARITY)
    routes = {(method, route.path) for route in main.app.routes for method in getattr(route, "methods", [])}
    for name, entry in PARITY.items():
        if "web" in entry:
            method, path = entry["web"].split(" ", 1)
            assert (method, path) in routes, name
        else:
            assert name == "init" and entry["cli_only"]


def test_same_run_summary_diff_gate_and_json_output(project, capsys, monkeypatch):
    baseline = post(project, set_baseline=True)
    project.switch("broken")
    body = post(project, baseline=baseline["run"]["id"], label="parity")
    run = project.store.get_run(body["run"]["id"])
    diff = DiffSummary.model_validate(body["diff"])
    outcome = RunOutcome(run["id"], run, diff, core.regression.gate_violations(diff, ["critical", "high"]), {}, [], run["agent"])
    # Replay this very record through CLI rendering, so ids/timings cannot obscure parity.
    monkeypatch.setattr(cli, "run_project", lambda *_a, **_k: outcome)
    code, output = command(capsys, project, "run", "--json")
    assert code == 1
    data = json.loads(output)
    assert data["diff"] == body["diff"]
    assert data["gate"]["violations"] == body["gate"]["violations"]
    assert data["gate"]["fail_on"] == body["gate"]["fail_on"]
    assert bool(data["gate"]["exit_code"]) == body["gate"]["failed"]
    assert run_summary(project.pid, data["run"], data["diff"], data["gate"]) == body["summary"]
    code, text = command(capsys, project, "run")
    assert code == 1
    assert "\n".join(body["summary"]["lines"]) in text
    assert body["summary"]["result"] in text
    assert body["diff"]["new_regressions"] == 4


def test_independent_cli_and_web_runs_have_same_findings(project, capsys):
    code, output = command(capsys, project, "run", "--json", "--set-baseline")
    assert code == 0
    baseline = json.loads(output)["run"]["id"]
    project.switch("broken")
    code, output = command(capsys, project, "run", "--json", "--baseline", baseline)
    body = post(project, baseline=baseline)
    data = json.loads(output)
    assert code == 1
    for key in ("total", "passed", "failed", "errors"):
        assert body["run"][key] == data["run"][key]
    fields = ("test_case_id", "diff_type", "severity", "violations")
    select = lambda d: [{k: e[k] for k in fields} for e in d["entries"]]
    assert select(body["diff"]) == select(data["diff"])


def test_triage_and_metrics_are_identical(project, capsys):
    post(project, set_baseline=True)
    project.switch("broken")
    body = post(project)
    rid = body["run"]["id"]
    _, text = command(capsys, project, "triage", "--run", rid, "--json")
    assert project.client.get(f"/api/runs/{rid}/triage").json() == json.loads(text)
    _, text = command(capsys, project, "metrics", "--project", project.pid, "--json")
    assert project.client.get("/api/metrics", params={"project_id": project.pid}).json() == json.loads(text)


@pytest.mark.parametrize("verdict,pre_kind,post_kind", [("ALL_FIXED", "broken", "fixed"), ("REGRESSED", "fixed", "broken")])
def test_verify_conclusions_and_counts(project, capsys, monkeypatch, verdict, pre_kind, post_kind):
    from app import trace
    monkeypatch.setattr(trace, "_now_iso", lambda: "2026-10-07T00:00:00.000+00:00")
    project.switch(pre_kind)
    pre = post(project)["run"]["id"]
    project.switch(post_kind)
    code, text = command(capsys, project, "verify", "--pre-run", pre, "--json")
    web = post(project, "verify", pre_run_id=pre)
    data = json.loads(text)
    assert code == (0 if verdict == "ALL_FIXED" else 1)
    for key in ("pre_run_id", "verdict", "counts", "spec_changed", "quote", "entries"):
        assert web[key] == data[key]
    assert web["verdict"] == verdict
    assert project.store.get_baseline(project.pid) is None


@pytest.mark.parametrize("format", ["junit", "json", "html"])
def test_downloads_equal_cli_file_bytes(project, capsys, tmp_path, format):
    post(project, set_baseline=True)
    project.switch("broken")
    rid = post(project)["run"]["id"]
    out = tmp_path / ("report." + format)
    if format == "html":
        code, _ = command(capsys, project, "report", "--run", rid, "--out", str(out))
        url = f"/api/runs/{rid}/report.html"
    else:
        code, _ = command(capsys, project, "export", "--run", rid, "--format", format, "--out", str(out))
        url = f"/api/runs/{rid}/export?format={format}"
    response = project.client.get(url)
    assert code == 0 and response.status_code == 200
    assert response.content == out.read_bytes()
    assert response.headers["content-disposition"].startswith('attachment; filename="specagent-run_')
    assert project.client.get(f"/api/runs/{rid}/report").json().keys() == {"run", "diff"}


def test_validate_corresponds_to_cli_and_hides_paths(project, capsys):
    code, text = command(capsys, project, "validate")
    report = post(project, "validate")
    assert code == 0 and report["ok"]
    assert report["project"] == project.pid
    assert report["constraints"] == 6 and report["probes"] == 7
    assert [r["cases"] for r in report["rules"]] == [10, 12, 5, 10, 6]
    # Relative labels are the sole intentional transport difference.
    rendered = text.replace(str(project.cfg), project.cfg.name).replace(str(project.root), "[project]").rstrip("\n")
    assert "\n".join(report["lines"]) == rendered
    project.cfg.write_text("project: broken\n", encoding="utf-8")
    report = post(project, "validate")
    assert not report["ok"] and any(".spec:" in e for e in report["errors"])
    assert str(project.root) not in json.dumps(report)


def test_draft_offline_parity_roundtrip_and_no_files(project, capsys, tmp_path):
    text = "退款超过500元需要人工审批。修改地址之前必须获得用户确认。"
    before = {str(p.relative_to(project.root)): p.read_bytes() for p in project.root.rglob("*") if p.is_file()}
    web = post(project, "draft", text=text)
    code, output = command(capsys, project, "draft", text)
    assert code == 0 and output == web["yaml"] + "\n" + web["warnings"][0] + "\n"
    assert web["compiler"] == "deterministic-demo"
    assert before == {str(p.relative_to(project.root)): p.read_bytes() for p in project.root.rglob("*") if p.is_file()}
    out = tmp_path / "roundtrip.yaml"
    out.write_text(web["yaml"], encoding="utf-8")
    assert load_spec_file(str(out)).rules[0].id == "ADDRESS_CONFIRM"


def test_draft_fake_llm_and_fallback(project, capsys, monkeypatch):
    from app.compiler import compile_demo
    payload = compile_demo("退款超过500元需要人工审批").model_dump()
    sent = []

    def create(**kwargs):
        sent.append(kwargs["input"])
        return SimpleNamespace(output_text=json.dumps(payload))

    fake = SimpleNamespace(responses=SimpleNamespace(create=create))
    monkeypatch.setattr(drafts, "make_client", lambda: fake)
    monkeypatch.setattr(cli, "_client_factory", lambda: fake)
    web = post(project, "draft", text="LLM preview")
    _, output = command(capsys, project, "draft", "LLM preview")
    assert web["compiler"].startswith("openai:") and web["warnings"] == []
    assert output.startswith(web["yaml"] + "\n")
    assert sent == ["LLM preview", "LLM preview"]
    fake.responses.create = lambda **_: (_ for _ in ()).throw(RuntimeError("offline fake failure"))
    assert post(project, "draft", text="fallback")["warnings"]


def test_run_options_last_baseline_and_expansion_warning(project, capsys):
    first = post(project, set_baseline=True)
    project.switch("broken")
    second = post(project, baseline="last", set_baseline=True, llm_expand=True)
    assert second["run"]["is_baseline"]
    assert second["diff"]["baseline_run_id"] == first["run"]["id"]
    assert second["gate"]["failed"]
    assert project.store.get_baseline(project.pid)["id"] == second["run"]["id"]
    _, output = command(capsys, project, "run", "--llm-expand")
    assert second["warnings"] == [output.splitlines()[0]]


def test_configured_expansion_and_fake_client(project, monkeypatch):
    calls = []

    async def expand(spec, tests):
        calls.append(len(tests))
        return tests

    monkeypatch.setenv("OPENAI_API_KEY", "fake-never-sent")
    monkeypatch.setattr(core, "expand_tests", expand)
    project.cfg.write_text(project.cfg.read_text(encoding="utf-8") + "run:\n  llm_expand: true\n", encoding="utf-8")
    assert post(project)["warnings"] == []
    assert calls == [43]


@pytest.mark.parametrize("operation,body", [("runs", {}), ("validate", {}), ("verify", {}), ("draft", {"text": "preview"})])
def test_post_guards_strict_inputs_and_lock(project, monkeypatch, operation, body):
    url = "/api/project/" + operation
    bad_host = TestClient(main.app, client=("127.0.0.1", 50000), headers={"Host": "attacker.example"})
    assert bad_host.post(url, json=body).status_code == 403
    assert project.client.post(url, content=json.dumps(body), headers={"Content-Type": "text/plain"}).status_code == 422
    assert project.client.post(url).status_code == 422
    for field in ("adapter", "agent", "endpoint", "spec", "config", "project_id", "fail_on", "allow_source"):
        assert project.client.post(url, json={**body, field: "untrusted"}).status_code == 422
    lock = try_acquire_run(project.cfg)
    try:
        assert project.client.post(url, json=body).status_code == 409
    finally:
        release_run(lock)
    monkeypatch.setenv("SPECAGENT_API_TOKEN", "test-parity-auth")
    assert project.client.post(url, json=body).status_code == 401
    assert project.client.post(url, json={**body, "adapter": "evil"}, headers={"Authorization": "Bearer test-parity-auth"}).status_code == 422


def test_verify_bad_sources_and_missing_records(project):
    c = project.client
    assert c.post("/api/project/verify", json={"pre_run_id": "x", "suggestion": "fix_20261007T000000_abcdef"}).status_code == 422
    assert c.post("/api/project/verify", json={"suggestion": "../../outside"}).status_code == 422
    assert c.post("/api/project/verify", json={"pre_run_id": "missing"}).status_code == 422
    assert c.post("/api/project/verify", json={}).status_code == 422
    assert c.post("/api/project/runs", json={"baseline": "last"}).status_code == 422
    assert c.post("/api/project/runs", json={"baseline": "missing"}).status_code == 422
    assert c.get("/api/diff?baseline=missing&candidate=missing").status_code == 404


def test_verify_suggestion_uses_stored_prefix(project):
    project.switch("broken")
    rid = post(project)["run"]["id"]
    suggestion = project.root / ".specagent" / "suggestions" / "fix_20261007T000000_abcdef"
    suggestion.mkdir(parents=True)
    (suggestion / "suggestion.json").write_text(json.dumps({"pre_fix_run_id": rid}), encoding="utf-8")
    project.switch("fixed")
    assert post(project, "verify", suggestion=suggestion.name)["verdict"] == "ALL_FIXED"


@pytest.mark.parametrize("suffix", ["triage", "export?format=junit", "export?format=json", "report.html"])
def test_read_guards_and_missing_run(project, monkeypatch, suffix):
    assert project.client.get("/api/runs/missing/" + suffix).status_code == 404
    rid = post(project)["run"]["id"]
    monkeypatch.setenv("SPECAGENT_API_TOKEN", "download-test-token")
    assert project.client.get(f"/api/runs/{rid}/" + suffix).status_code == 401
    assert project.client.get(f"/api/runs/{rid}/" + suffix, headers={"X-API-Key": "download-test-token"}).status_code == 200


def test_no_server_secrets_in_import_errors_or_downloads(project, monkeypatch):
    secret = "sentinel-credential-do-not-return"
    endpoint = "https://user:password@private.example/internal"
    monkeypatch.setenv("PRIVATE_AGENT_KEY", secret)
    monkeypatch.setenv("CUSTOM_AGENT_ENDPOINT", endpoint)
    original = project.cfg.read_text(encoding="utf-8")
    project.cfg.write_text(original.replace("  type: python", "  type: python\n  endpoint_env: CUSTOM_AGENT_ENDPOINT"), encoding="utf-8")
    # A configured module may raise with a path and env data; they must stay server-side.
    monkeypatch.setattr(Project, "make_adapter", lambda _: (_ for _ in ()).throw(RuntimeError(f"{project.root} {secret} {endpoint}")))
    response = project.client.post("/api/project/runs", json={})
    assert response.status_code == 422
    for needle in (str(project.root), secret, endpoint, "user:password"):
        assert needle not in response.text


def test_actual_concurrent_operation_is_rejected(project, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = main.validate_report

    def slow(path):
        entered.set()
        assert release.wait(10)
        return original(path)

    monkeypatch.setattr(main, "validate_report", slow)
    results = []
    worker = threading.Thread(target=lambda: results.append(project.client.post("/api/project/validate", json={})))
    worker.start()
    try:
        assert entered.wait(10)
        assert project.client.post("/api/project/draft", json={"text": "preview"}).status_code == 409
    finally:
        release.set()
        worker.join(10)
    assert results[0].status_code == 200


def test_downloads_and_triage_redact_stored_secrets(project, monkeypatch):
    rid = post(project)["run"]["id"]
    secret = "fake-stored-secret-to-redact"
    monkeypatch.setenv("PRIVATE_AGENT_TOKEN", secret)
    original = project.store.get_run

    def unsafe_record(run_id):
        run = original(run_id)
        if run and run_id == rid:
            row = run["results"][0]
            row["status"] = "ERROR"
            row["response"] = f"{secret} {project.root} https://user:password@private.example/path"
        return run

    monkeypatch.setattr(project.store, "get_run", unsafe_record)
    for suffix in ("triage", "export?format=json", "export?format=junit", "report.html"):
        response = project.client.get(f"/api/runs/{rid}/{suffix}")
        assert response.status_code == 200
        for needle in (secret, str(project.root), "user:password"):
            assert needle not in response.text


def test_download_filename_and_draft_limits(project):
    response = main._download("data", 'run_"\r\n<unsafe>', "html", "text/html")
    filename = response.headers["content-disposition"]
    assert "\r" not in filename and "\n" not in filename and "<" not in filename
    assert project.client.post("/api/project/draft", json={"text": ""}).status_code == 422
    assert project.client.post("/api/project/draft", json={"text": "x" * 20001}).status_code == 422
    rid = post(project)["run"]["id"]
    assert project.client.get(f"/api/runs/{rid}/export?format=other").status_code == 422


def test_bad_input_diagnostics_do_not_echo_secrets(project, monkeypatch):
    secret = "fake-schema-secret"
    monkeypatch.setenv("PRIVATE_AGENT_SECRET", secret)
    response = project.client.post("/api/project/verify", json={"suggestion": secret})
    assert response.status_code == 422 and secret not in response.text


def test_new_call_metadata_handles_arguments_and_repeated_calls():
    from app.trace_diff import new_call_indices
    from app.report import _trace_lines
    call = lambda args: {"type": "tool_call", "name": "transfer", "args": args}
    before = [call({"amount": 1, "account": "A"})]
    after = [call({"account": "A", "amount": 1}), call({"amount": 2, "account": "A"}), call({"amount": 1, "account": "A"})]
    indices = new_call_indices(before, after)
    assert indices == [1, 2]
    assert _trace_lines(after, indices).count("◀ new") == 2


def test_authenticated_project_tools_and_short_token(project, monkeypatch):
    rid = post(project)["run"]["id"]
    monkeypatch.setenv("SPECAGENT_API_TOKEN", "run")
    project.client.headers["Authorization"] = "Bearer run"
    assert post(project, "validate")["ok"]
    assert post(project, "verify", pre_run_id=rid)["verdict"] == "NO_CHANGE"
    assert post(project, "draft", text="preview")["compiler"] == "deterministic-demo"
    body = post(project)
    assert body["run"]["id"].startswith("run_")
    assert project.client.get(f"/api/runs/{body['run']['id']}/triage").status_code == 200


def test_posix_redaction_preserves_project_relative_label(project):
    from app.web_presenters import web_payload
    assert web_payload("[project]/specs/behavior.yaml /srv/private/data.txt") == "[project]/specs/behavior.yaml [path]"
