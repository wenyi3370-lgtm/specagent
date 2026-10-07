"""Observe actual case completions before a blocked suite finishes."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app import main, orchestrator
from app.cancellation import CancelRegistry
from app.models import AgentExecution, BehaviorSpec, TestCase as Case, TraceEvent
from app.storage import Store
from test_cli_web_parity import project, post


def case(number, **fields):
    return Case(id=f'case-{number}', rule_id='R', category='normal', user_input='test', **fields)


def execution(tool='tool'):
    return AgentExecution(trace=[TraceEvent(type='tool_call', name=tool, seq=1)])


def test_live_api_counts_cases_out_of_order_before_completion(tmp_path, monkeypatch):
    store = Store(str(tmp_path/'live.db'))
    monkeypatch.setattr(main, 'store', store)
    client = TestClient(main.app)

    async def scenario():
        release = asyncio.Event()
        class Adapter:
            name = 'test:progress'
            async def execute(self, test, context):
                if test.id == 'case-1':
                    await release.wait()
                    return execution('danger')
                if test.id == 'case-3':
                    raise RuntimeError('adapter failure')
                return execution()
        task = asyncio.create_task(orchestrator.run_with_diff(
            store, project_id='live', spec=BehaviorSpec(), tests=[case(1, forbidden_calls=['danger']), case(2), case(3)],
            adapter=Adapter(), concurrency=3))
        try:
            for _ in range(100):
                await asyncio.sleep(.01)
                runs = store.list_runs('live')
                if runs and store.get_progress(runs[0]['id'])['completed'] == 2:
                    break
            assert not task.done()
            rid = runs[0]['id']
            live = client.get(f'/api/runs/{rid}/progress').json()
            assert (live['completed'], live['total'], live['passed'], live['errors']) == (2, 3, 1, 1)
            assert not live['final'] and live['last_case']['position'] == 3
        finally:
            release.set()
            rid, results, _ = await task
        final = client.get(f'/api/runs/{rid}/progress').json()
        assert final['final'] and final['phase'] == 'completed'
        assert (final['passed'], final['failed'], final['errors'], final['completed']) == (1, 1, 1, 3)
        assert [r.status for r in results] == ['FAIL', 'PASS', 'ERROR']
    asyncio.run(scenario())


def test_repeats_and_retries_count_one_case_and_report_flaky(tmp_path):
    from app.adapters.base import TransientAgentError
    store = Store(str(tmp_path/'repeat.db'))
    class Adapter:
        name = 'test:repeat'
        calls = 0
        async def execute(self, test, context):
            self.calls += 1
            if self.calls == 1:
                raise TransientAgentError('retry')
            return execution('danger' if self.calls == 3 else 'tool')
    rid, results, _ = asyncio.run(orchestrator.run_with_diff(
        store, project_id='repeat', spec=BehaviorSpec(), tests=[case(1, forbidden_calls=['danger'])],
        adapter=Adapter(), repeat=2, retries=1))
    p = store.get_progress(rid)
    assert (p['completed'], p['total'], p['flaky'], p['failed']) == (1, 1, 1, 1)
    assert results[0].status == 'FLAKY' and p['final']


def test_cancel_keeps_completed_cases_and_final_counts(tmp_path):
    store = Store(str(tmp_path/'cancel.db'))
    registry = CancelRegistry()
    class Adapter:
        name = 'test:cancel'
        async def execute(self, test, context):
            rid = store.list_runs('cancel')[0]['id']
            assert registry.cancel(rid)
            return execution()
    rid, results, _ = asyncio.run(orchestrator.run_with_diff(
        store, project_id='cancel', spec=BehaviorSpec(), tests=[case(1), case(2), case(3)],
        adapter=Adapter(), concurrency=1, cancel_registry=registry))
    p = store.get_progress(rid)
    assert [r.status for r in results] == ['PASS', 'CANCELED', 'CANCELED']
    assert (p['completed'], p['passed'], p['canceled'], p['total']) == (3, 1, 2, 3)
    assert p['final'] and not registry.cancel(rid)
    assert store.get_run(rid)['status'] == 'canceled'


def test_final_counts_include_whole_suite_unverified_downgrade(tmp_path):
    store = Store(str(tmp_path/'empty-trace.db'))
    class Adapter:
        name = 'test:empty'
        async def execute(self, test, context):
            return AgentExecution(response='refusal')
    rid, _, _ = asyncio.run(orchestrator.run_with_diff(
        store, project_id='empty', spec=BehaviorSpec(), tests=[case(1), case(2)], adapter=Adapter()))
    p = store.get_progress(rid)
    assert p['passed'] == 0 and p['errors'] == 2 and p['final']
    assert p['last_case']['status'] == 'ERROR'


def test_existing_database_gains_progress_table_and_retains_old_summary(tmp_path):
    from sqlalchemy import text
    path = str(tmp_path/'existing.db')
    old = Store(path)
    rid = old.create_run(project_id='old', spec={}, tests=[case(1).model_dump()], label='keep me')
    old.complete_run(rid, passed=1, failed=0, errors=0, total=1, score=100)
    with old._engine.begin() as connection:
        connection.execute(text('DROP TABLE run_progress'))
    old._engine.dispose()
    upgraded = Store(path)
    p = upgraded.get_progress(rid)
    assert p['final'] and not p['available'] and p['completed'] == p['total'] == p['passed'] == 1
    assert upgraded.get_run(rid)['label'] == 'keep me'
    upgraded._save_progress(rid, {**p, 'available':True})
    assert Store(path).get_progress(rid)['available']


def test_progress_authentication_and_missing_run(tmp_path, monkeypatch):
    store = Store(str(tmp_path/'auth.db'))
    monkeypatch.setattr(main, 'store', store)
    monkeypatch.setenv('SPECAGENT_API_TOKEN', 'progress-test-token')
    rid = store.create_run(project_id='p', spec={}, tests=[])
    client = TestClient(main.app)
    url = f'/api/runs/{rid}/progress'
    assert client.get(url).status_code == 401
    for headers in ({'Authorization':'Bearer progress-test-token'}, {'X-API-Key':'progress-test-token'}):
        assert client.get(url, headers=headers).status_code == 200
        assert client.get('/api/runs/missing/progress', headers=headers).status_code == 404
    store._save_progress(rid, {'available':True, 'phase':'running', 'cancellable':True})
    # A persisted snapshot does not recreate cancellation authority after restart.
    headers = {'X-API-Key':'progress-test-token'}
    assert not client.get(url, headers=headers).json()['cancellable']
    main.cancel_registry.register(rid)
    try:
        assert client.get(url, headers=headers).json()['cancellable']
    finally:
        main.cancel_registry.unregister(rid)


def test_empty_suite_finishes_without_fake_case_counts(tmp_path):
    store = Store(str(tmp_path/'zero.db'))
    class Adapter:
        name = 'test:zero'
        async def execute(self, test, context):
            raise AssertionError('no cases')
    rid, _, _ = asyncio.run(orchestrator.run_with_diff(store, project_id='zero', spec=BehaviorSpec(), tests=[], adapter=Adapter()))
    p = store.get_progress(rid)
    assert p['final'] and p['total'] == p['completed'] == 0 and p['last_case'] is None


@pytest.mark.parametrize('entry', ['verify', 'agent'])
def test_web_verify_and_approved_agent_run_record_final_progress(project, monkeypatch, entry):
    from app import agent_api
    pre = post(project)['run']['id']
    if entry == 'verify':
        result = post(project, 'verify', pre_run_id=pre)
        rid = result['run_id']
    else:
        monkeypatch.setattr(agent_api, '_store', project.store)
        monkeypatch.setattr(agent_api, 'client_factory', lambda: None)
        monkeypatch.setenv('SPECAGENT_API_TOKEN', 'progress-test-token')
        project.client.headers['Authorization'] = 'Bearer progress-test-token'
        session = project.client.post('/api/agent/sessions', json={}).json()['session_id']
        pending = project.client.post(f'/api/agent/sessions/{session}/messages', json={'text':'run it'}).json()['pending'][0]
        result = project.client.post(f'/api/agent/sessions/{session}/approve',
            json={'action_id':pending['action_id'], 'approve':True}).json()
        assert result['action']['ok']
        rid = result['action']['run_id']
    progress = project.store.get_progress(rid)
    run = project.store.get_run(rid)
    assert progress['available'] and progress['final'] and not progress['cancellable']
    assert progress['completed'] == progress['total'] == run['total']
    assert (progress['passed'], progress['failed'], progress['errors']) == (run['passed'], run['failed'], run['errors'])
