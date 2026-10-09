"""Reviewable snapshot and behavioral tests for the v1 candidate boundary."""
import asyncio
import json
from pathlib import Path

import httpx

from app.adapters.base import ExecutionContext
from app.adapters.http_adapter import HttpAdapter
from app.models import DiffEntry, DiffSummary, TestCase as Case
from app.regression import gate_violations
from scripts.export_contract import SNAPSHOT, changed_sections, isolated_capture


def test_declared_public_contract_matches_reviewed_snapshot():
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    actual = isolated_capture()
    assert not changed_sections(expected, actual), (
        "Public contract changed. Review compatibility and explicitly regenerate the snapshot.")


def test_contract_drift_detects_removed_route_and_changed_cli_default():
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    removed = json.loads(json.dumps(expected))
    removed["openapi"]["paths"].pop("/api/project/runs")
    assert changed_sections(expected, removed) == ["openapi"]
    changed = json.loads(json.dumps(expected))
    option = next(a for a in changed["cli"]["commands"]["run"]["actions"] if a["dest"] == "config")
    option["default"] = "changed.yaml"
    assert changed_sections(expected, changed) == ["cli"]
    changed = json.loads(json.dumps(expected))
    timeout = next(f for f in changed["adapter"]["context_fields"] if f["name"] == "timeout_seconds")
    assert timeout["default"] == 30 and not timeout["required"]
    timeout["default"] = 60
    assert changed_sections(expected, changed) == ["adapter"]


def test_http_adapter_wire_contract_preserves_identity_history_and_denial(monkeypatch):
    monkeypatch.setenv("CONTRACT_TARGET", "https://contract.invalid/execute")
    monkeypatch.delenv("TARGET_AGENT_TOKEN", raising=False)
    seen = []
    def respond(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"response": "declined", "latency_ms": 5,
            "trace": [{"id": "a1", "seq": 1, "type": "approval_result",
                       "name": "request_human_approval", "result": {"approved": False}}]})
    adapter = HttpAdapter(endpoint_env="CONTRACT_TARGET", transport=httpx.MockTransport(respond))
    case = Case(id="contract", rule_id="r", category="multi_turn", user_input="refund",
                history=["first request"], actor={"account_id": "synthetic", "role": "customer"})
    execution = asyncio.run(adapter.execute(case, ExecutionContext(case.id)))
    assert seen == [{"message": "refund", "test_case_id": "contract",
                     "context": case.actor, "history": ["first request"]}]
    assert execution.trace[0].result == {"approved": False}
    assert execution.trace[0].type == "approval_result" and execution.latency_ms == 5


def test_default_gate_keeps_execution_errors_flaky_and_historical_failures_separate():
    entries = [DiffEntry(test_case_id=kind, diff_type=kind, severity="critical",
               candidate_status={'NEW_ERROR': 'ERROR', 'FLAKY': 'FLAKY'}.get(kind, 'FAIL'))
               for kind in ("NEW_REGRESSION", "PERSISTENT_FAIL", "FLAKY", "NEW_ERROR", "CANCELED")]
    entries += [DiffEntry(test_case_id="low", diff_type="NEW_REGRESSION", severity="low")]
    diff = DiffSummary(candidate_run_id="candidate", entries=entries)
    assert [e.test_case_id for e in gate_violations(diff)] == ["NEW_REGRESSION", 'FLAKY', 'NEW_ERROR']
    assert [e.test_case_id for e in gate_violations(diff, ["low"])] == ['FLAKY', 'NEW_ERROR', "low"]
    assert [e.test_case_id for e in gate_violations(diff, block_errors=False, block_flaky=False)] == ['NEW_REGRESSION']
