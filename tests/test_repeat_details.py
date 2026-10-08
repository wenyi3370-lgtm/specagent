"""Repeated evidence survives final aggregation and is read without execution."""
import asyncio
import copy

import pytest
from fastapi.testclient import TestClient

from app import main, orchestrator
from app.adapters.base import TransientAgentError
from app.models import AgentExecution, BehaviorSpec, TestCase as Case, TraceEvent
from app.orchestrator import execute_suite, persist_run, result_to_storage
from app.storage import Store


SPEC = BehaviorSpec()
CASE = Case(id='T-repeat', rule_id='R', category='normal', user_input='exercise', forbidden_calls=['delete_account'])


class SequenceAdapter:
    name = 'repeat-test'

    def __init__(self, statuses, response='response'):
        self.statuses, self.response, self.calls = statuses, response, 0

    async def execute(self, case, context):
        status = self.statuses[self.calls % len(self.statuses)]
        self.calls += 1
        if isinstance(status, Exception):
            raise status
        return AgentExecution(response=self.response + str(self.calls),
            latency_ms=self.calls * 11, error='timeout' if status == 'ERROR' else None,
            trace=[TraceEvent(type='tool_call', name='delete_account' if status == 'FAIL' else 'lookup_balance',
                             args={'amount':self.calls, 'token':'private-snapshot-token'})],
            raw={'response':'unbounded native raw payload'})


def suite(adapter, repeat=3, **kwargs):
    return asyncio.run(execute_suite(SPEC, [CASE], adapter=adapter, repeat=repeat, concurrency=1, retries=0, **kwargs))[0]


@pytest.fixture
def saved(tmp_path, monkeypatch):
    db = str(tmp_path / 'repeat.db')
    store = Store(db)
    monkeypatch.setattr(main, 'store', store)
    client = TestClient(main.app, client=('127.0.0.1', 50000), headers={'Host':'127.0.0.1'})
    def save(result):
        rid = persist_run(store, project_id='repeat-project', spec=SPEC, tests=[CASE],
                          results=[result], agent_label='repeat-test')
        return rid, store.get_run(rid)['results'][0]['id']
    return store, client, save, db


def test_mixed_outcomes_preserve_original_status_response_trace_and_latency():
    result = suite(SequenceAdapter(['PASS','FAIL','PASS']))
    assert result.status == 'FLAKY' and not result.passed
    rows = result_to_storage(result)['repeat']
    assert [r['status'] for r in rows] == ['PASS','FAIL','PASS']
    assert [r['execution']['response'] for r in rows] == ['response1','response2','response3']
    assert [r['execution']['latency_ms'] for r in rows] == [11,22,33]
    assert rows[0]['passed'] and rows[0]['violations'] == [] and rows[1]['violations']
    assert rows[1]['execution']['trace'][0]['name'] == 'delete_account'
    assert rows[0]['execution']['trace'][0]['args']['token'] == '[REDACTED]'
    assert 'raw' not in rows[0]['execution']
    result.execution.response = 'mutated';result.violations.append('later review')
    assert result_to_storage(result)['repeat'] == rows
    rows[0]['status'] = 'changed'
    assert result_to_storage(result)['repeat'][0]['status'] == 'PASS'
    assert 'repeat' not in result.model_dump() and '_repeat_snapshots' not in result.model_dump_json()


@pytest.mark.parametrize('statuses,final', [(['PASS'],'PASS'),(['FAIL'],'FAIL'),(['ERROR'],'ERROR'),
    (['PASS','ERROR'],'FLAKY'),([RuntimeError('boom')],'ERROR')])
def test_stable_error_and_mixed_error_repeats_keep_existing_aggregation(statuses,final):
    result = suite(SequenceAdapter(statuses))
    assert result.status == final and len(result_to_storage(result)['repeat']) == 3


def test_limits_apply_to_every_saved_repeat_without_native_raw_bypass():
    result = suite(SequenceAdapter(['PASS'], response='x'*100), max_response_chars=10, max_trace_events=1)
    for row in result_to_storage(result)['repeat']:
        assert len(row['execution']['response']) == 10 and row['execution']['truncated']
        assert 'raw' not in row['execution']


def test_cancellation_between_repeats_records_unexecuted_marker_without_extra_calls():
    adapter = SequenceAdapter(['PASS'])
    result = suite(adapter, should_cancel=lambda:adapter.calls>=1)
    rows = result_to_storage(result)['repeat']
    assert adapter.calls == 1 and result.status == 'CANCELED'
    assert [row['status'] for row in rows] == ['PASS','CANCELED']
    assert rows[1]['index'] == 2 and rows[1]['requested'] == 3 and not rows[1]['executed']
    assert rows[1]['execution']['trace'] == []


def test_cancellation_before_start_and_single_repeat_compatibility():
    adapter = SequenceAdapter(['PASS'])
    result = suite(adapter, should_cancel=lambda:True)
    assert adapter.calls == 0 and result_to_storage(result)['repeat'][0]['executed'] is False
    assert result_to_storage(suite(adapter, repeat=1))['repeat'] == []


def test_suite_level_unverified_downgrade_does_not_rewrite_original_repeat_judgments():
    class TextOnly:
        async def execute(self, case, context): return AgentExecution(response='refusal')
    result = suite(TextOnly())
    assert result.status == 'ERROR' and 'nothing was verified' in result.execution.error
    assert [r['status'] for r in result_to_storage(result)['repeat']] == ['PASS']*3


def test_transient_retry_is_one_repeat_not_an_extra_record():
    adapter = SequenceAdapter([TransientAgentError('temporary'), 'PASS'])
    result = asyncio.run(execute_suite(SPEC,[CASE],adapter=adapter,repeat=2,retries=1,concurrency=1))[0]
    assert adapter.calls == 4 and len(result_to_storage(result)['repeat']) == 2 and result.status == 'PASS'


def test_judge_exception_preserves_prior_repeat_and_records_error(monkeypatch):
    original = orchestrator.judge
    def failing(case, execution):
        if execution.latency_ms == 22: raise RuntimeError('judge crash')
        return original(case,execution)
    monkeypatch.setattr(orchestrator,'judge',failing)
    result = suite(SequenceAdapter(['PASS']))
    assert result.status == 'ERROR'
    assert [r['status'] for r in result_to_storage(result)['repeat']] == ['PASS','ERROR']


def test_persistence_restart_read_and_diff_do_not_run_adapter(saved, monkeypatch):
    store, client, save, db = saved
    adapter = SequenceAdapter(['PASS','FAIL','PASS'])
    rid, eid = save(suite(adapter))
    before = copy.deepcopy(store.get_run(rid))
    reopened = Store(db);monkeypatch.setattr(main,'store',reopened)
    data = client.get('/api/executions/'+eid+'/repeats').json()
    assert data['available'] and data['requested'] == 3 and data['executed'] == 3 and data['final_status'] == 'FLAKY'
    assert [r['index'] for r in data['items']] == [1,2,3]
    diff = client.get('/api/executions/'+eid+'/repeats/diff', params={'left':1,'right':2}).json()
    assert diff['left']['status'] == 'PASS' and diff['right']['status'] == 'FAIL'
    assert diff['added_violations'] and 'lookup_balance' in diff['trace_diff'] and 'delete_account' in diff['trace_diff']
    assert '-response1' in diff['response_diff'] and '+response2' in diff['response_diff']
    assert adapter.calls == 3 and reopened.get_run(rid) == before
    same = client.get('/api/executions/'+eid+'/repeats/diff',params={'left':2,'right':2}).json()
    assert not same['trace_diff'] and not same['response_diff'] and not same['added_violations']
    removed = client.get('/api/executions/'+eid+'/repeats/diff',params={'left':2,'right':3}).json()
    assert removed['removed_violations'] and not removed['added_violations']


def test_old_empty_repeat_rows_are_explicitly_unavailable(saved):
    store, client, save, db = saved
    rid,eid = save(suite(SequenceAdapter(['PASS']),repeat=1))
    before=store.get_run(rid)
    data=client.get('/api/executions/'+eid+'/repeats').json()
    assert not data['available'] and data['requested'] is None and data['total'] == 0 and data['items'] == []
    assert client.get('/api/executions/'+eid+'/repeats/diff',params={'left':1,'right':2}).status_code == 404
    assert Store(db).get_run(rid) == before


def test_repeat_pagination_reaches_all_records_and_has_no_cross_execution_data(saved):
    store,client,save,db = saved
    _,eid=save(suite(SequenceAdapter(['PASS']),repeat=23))
    _,other=save(suite(SequenceAdapter(['FAIL']),repeat=2))
    seen=[]
    for offset in (0,10,20):
        data=client.get('/api/executions/'+eid+'/repeats',params={'offset':offset,'limit':10}).json()
        assert data['total']==23 and data['has_more']==(offset<20)
        seen.extend(r['index'] for r in data['items'])
    assert seen == list(range(1,24))
    assert client.get('/api/executions/'+other+'/repeats').json()['total'] == 2


def test_repeat_views_redact_credentials_paths_and_limit_display_without_changing_storage(saved, monkeypatch):
    store,client,save,db=saved
    monkeypatch.setenv('REPEAT_TEST_SECRET','repeat-env-secret')
    _,eid=save(suite(SequenceAdapter(['PASS','FAIL'],response='repeat-env-secret C:\\private\\file.txt Bearer unsafe-token '+ 'x'*22000), max_response_chars=30000))
    before=store.get_execution(eid)
    data=client.get('/api/executions/'+eid+'/repeats')
    assert data.status_code==200 and 'repeat-env-secret' not in data.text and 'C:\\' not in data.text and 'unsafe-token' not in data.text
    assert data.json()['items'][0]['view_limited'] and len(data.json()['items'][0]['execution']['response'])==20000
    diff=client.get('/api/executions/'+eid+'/repeats/diff',params={'left':1,'right':2})
    assert diff.json()['limited'] and 'repeat-env-secret' not in diff.text
    assert store.get_execution(eid)==before


@pytest.mark.parametrize('suffix,params', [('',{'offset':-1}),('',{'limit':0}),('',{'limit':21}),
    ('/diff',{'left':0,'right':2}),('/diff',{'left':1}),('/diff',{'left':'private-input','right':2})])
def test_repeat_query_validation(saved,suffix,params):
    _,client,save,_=saved
    _,eid=save(suite(SequenceAdapter(['PASS'])))
    response=client.get('/api/executions/'+eid+'/repeats'+suffix,params=params)
    assert response.status_code == 422 and 'private-input' not in response.text


def test_repeat_auth_missing_records_and_no_mutations(saved, monkeypatch):
    store,client,save,db=saved
    _,eid=save(suite(SequenceAdapter(['PASS','FAIL'])))
    monkeypatch.setenv('SPECAGENT_API_TOKEN','repeat-test-token')
    for suffix,params in [('',{}),('/diff',{'left':1,'right':2})]:
        url='/api/executions/'+eid+'/repeats'+suffix
        assert client.get(url,params=params).status_code==401
        for headers in ({'Authorization':'Bearer repeat-test-token'},{'X-API-Key':'repeat-test-token'}):
            assert client.get(url,params=params,headers=headers).status_code==200
    headers={'Authorization':'Bearer repeat-test-token'}
    assert client.get('/api/executions/missing/repeats',headers=headers).status_code==404
    assert client.get('/api/executions/'+eid+'/repeats/diff',params={'left':1,'right':99},headers=headers).status_code==404
    assert client.post('/api/executions/'+eid+'/repeats',json={},headers=headers).status_code==405
