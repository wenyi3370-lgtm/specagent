"""Trend values share metric and regression functions; no real providers."""
from datetime import datetime, timedelta, timezone
import asyncio

import pytest
from fastapi.testclient import TestClient

from app import main, regression, orchestrator
from app.metrics import compute_run_metrics
from app.models import AgentExecution, BehaviorSpec, TestCase as Case, TraceEvent
from app.storage import Run
from test_cli_web_parity import project, post

FIELDS = ['behavior_pass_rate', 'critical_violation_rate', 'new_regression_count',
          'flaky_rate', 'tool_accuracy', 'latency_ms']


def history(p, **params):
    response = p.client.get('/api/metrics/history', params={'project_id':p.pid, **params})
    assert response.status_code == 200, response.text
    return response.json()


def synthetic(p, started_at, status='completed', pid=None, label=''):
    rid = p.store.create_run(project_id=pid or p.pid, spec={}, tests=[], label=label)
    if status != 'running':
        p.store.complete_run(rid, passed=0, failed=0, errors=0, total=0, score=0, status=status)
    with p.store._session() as s:
        s.get(Run, rid).started_at = started_at
        s.commit()
    return rid


def test_real_runs_trend_values_match_shared_metrics_and_latest_tiles(project):
    first = post(project, set_baseline=True)['run']
    project.switch('broken')
    second = post(project)['run']
    project.switch('fixed')
    third = post(project)['run']
    d = history(project, days='all')
    assert d['baseline_id'] == first['id'] and d['comparison'] == 'current_baseline'
    assert [p['run_id'] for p in d['points']] == [first['id'], second['id'], third['id']]
    baseline = project.store.get_run(first['id'])
    for point in d['points']:
        run = project.store.get_run(point['run_id'])
        expected = compute_run_metrics(run)
        for field in FIELDS:
            if field != 'new_regression_count':
                assert point[field] == expected[field]
        assert point['new_regression_count'] == (regression.diff_runs(baseline, run).new_regressions if run['id'] != first['id'] else 0)
    tiles = project.client.get('/api/metrics', params={'project_id':project.pid}).json()
    assert {k:d['points'][-1][k] for k in FIELDS} == {k:tiles[k] for k in FIELDS}
    assert d['points'][1]['new_regression_count'] == 4


def test_baseline_changes_recompute_comparisons_with_explicit_identity(project):
    fixed = post(project, set_baseline=True)['run']
    project.switch('broken')
    broken = post(project)['run']
    assert history(project)['points'][-1]['new_regression_count'] == 4
    project.store.set_baseline(broken['id'])
    d = history(project)
    assert d['baseline_id'] == broken['id'] and d['points'][-1]['new_regression_count'] == 0
    assert d['points'][0]['run_id'] == fixed['id'] and d['points'][0]['behavior_pass_rate'] == 1


def test_time_filter_before_limit_and_final_statuses_and_project_scope(project):
    now = datetime.now(timezone.utc)
    stamp = lambda days: (now-timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%S')
    older = synthetic(project, stamp(40))
    recent = synthetic(project, stamp(10))
    canceled = synthetic(project, stamp(1), 'canceled')
    synthetic(project, stamp(0), 'running')
    synthetic(project, stamp(0), 'error')
    synthetic(project, stamp(0), pid='unrelated-project')
    synthetic(project, stamp(-1))  # a future timestamp is outside the snapshot
    assert [p['run_id'] for p in history(project, days='all')['points']] == [older, recent, canceled]
    assert history(project, days='30', limit=1)['total'] == 2
    d = history(project, days='30', limit=1)
    assert d['truncated'] and [p['run_id'] for p in d['points']] == [canceled]
    assert history(project, days='7')['total'] == 1
    assert history(project, days='90')['total'] == 3


def test_empty_history_and_no_baseline_are_explicit(project):
    d = history(project)
    assert d['points'] == [] and d['total'] == 0 and not d['truncated']
    assert d['baseline_id'] is None and d['timezone'] == 'UTC'
    post(project)
    d = history(project)
    assert d['baseline_id'] is None and d['points'][0]['new_regression_count'] == 0


@pytest.mark.parametrize('params', [{'days':'0'}, {'days':'365'}, {'limit':0}, {'limit':501}, {'limit':'invalid'}])
def test_query_bounds(project, params):
    assert project.client.get('/api/metrics/history', params=params).status_code == 422


def test_authentication_and_label_redaction(project, monkeypatch):
    secret = 'trend-private-secret-value'
    synthetic(project, '2026-01-01T00:00:00', label=secret+' C:\\private\\data.txt')
    monkeypatch.setenv('TREND_TEST_SECRET', secret)
    monkeypatch.setenv('SPECAGENT_API_TOKEN', 'trend-test-token')
    path='/api/metrics/history?project_id='+project.pid+'&days=all'
    assert TestClient(main.app).get(path).status_code == 401
    for headers in ({'Authorization':'Bearer trend-test-token'}, {'X-API-Key':'trend-test-token'}):
        response=project.client.get(path, headers=headers)
        assert response.status_code == 200 and secret not in response.text
        assert '[redacted]' in response.text and 'private' not in response.json()['points'][0]['label']


def test_actual_flaky_and_error_results_keep_common_metric_denominators(project):
    class Adapter:
        name = 'test:trend-repeat'
        calls = 0
        async def execute(self, case, context):
            if case.id == 'error':
                raise RuntimeError('test adapter failure')
            self.calls += 1
            return AgentExecution(trace=[TraceEvent(type='tool_call', seq=1,
                name='danger' if self.calls == 2 else 'safe')])
    spec = BehaviorSpec(rules=[{'id':'R', 'title':'Rule', 'action':'danger', 'severity':'critical', 'condition':'test'}])
    rid, _, _ = asyncio.run(orchestrator.run_with_diff(project.store, project_id=project.pid,
        spec=spec, tests=[Case(id='repeat', rule_id='R', category='normal', user_input='test', forbidden_calls=['danger']),
                         Case(id='error', rule_id='R', category='normal', user_input='test')],
        adapter=Adapter(), repeat=2, concurrency=1))
    p = history(project)['points'][0]
    assert p['run_id'] == rid and p['flaky_rate'] == .5 and p['critical_violation_rate'] == .5
    assert p['errors'] == 1 and p['tool_accuracy'] == 0 and p['behavior_pass_rate'] == 0


def test_history_is_not_cut_off_by_existing_100_or_200_run_lists(project):
    for _ in range(205):
        synthetic(project, '2026-01-01T00:00:00')
    d = history(project, days='all', limit=500)
    assert d['total'] == len(d['points']) == 205 and not d['truncated']
    default = history(project, days='all')
    assert default['total'] == 205 and len(default['points']) == 200 and default['truncated']
