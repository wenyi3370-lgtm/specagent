"""Append-only baseline audit, identity attribution and guarded clear on private DBs."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import inspect, update

from app.baseline_audit import baseline_context, baseline_identity
from app.storage import BaselineAudit, Run, Store, RunManagementError
from cli.specagent import main as cli_main
from test_cli_web_parity import project, post


def seed(p, pid=None):
    rid=p.store.create_run(project_id=pid or p.pid,spec={},tests=[],agent='demo:fixed')
    p.store.complete_run(rid,passed=0,failed=0,errors=0,total=0,score=0)
    return rid


def history(p, **params):
    response=p.client.get('/api/baselines/history',params={'project_id':p.pid,**params})
    assert response.status_code==200,response.text
    return response.json()


def clear(p, snapshot, **body):
    return p.client.post('/api/baselines/clear',params={'project_id':p.pid},json={
        'confirm_run_id':snapshot['current']['id'],'expected_event_id':snapshot['revision'],**body})


def test_set_replace_reselect_clear_history_retains_exact_order_and_state(project):
    a,b=seed(project),seed(project)
    for rid in (a,b,b):
        assert project.client.post('/api/runs/'+rid+'/baseline').status_code==200
    data=history(project)
    assert data['current']['id']==b and data['total']==3 and data['revision']==data['items'][0]['id']
    assert [e['run_id'] for e in data['items']]==[b,b,a]
    assert [e['previous_run_id'] for e in data['items']]==[b,a,None]
    assert all(e['source']=='web' and e['actor']=='local_browser_user' and e['created_at'] for e in data['items'])
    before=project.store.get_run(b)
    assert clear(project,data).status_code==200
    after=history(project)
    assert after['current'] is None and after['total']==4 and after['items'][0]['action']=='clear'
    assert after['items'][0]['previous_run_id']==b and after['items'][0]['run_id'] is None
    assert {k:v for k,v in before.items() if k!='is_baseline'}=={k:v for k,v in project.store.get_run(b).items() if k!='is_baseline'}
    assert project.store.get_run(a) and len(project.store.list_runs(project.pid))==2


def test_preview_revision_rejects_change_away_and_back_and_repeated_clear(project):
    a,b=seed(project),seed(project);project.store.set_baseline(a);old=history(project)
    project.store.set_baseline(b);project.store.set_baseline(a)
    assert clear(project,old).status_code==409 and clear(project,old).json()['detail']=='baseline_changed'
    fresh=history(project)
    assert fresh['total']==3 and fresh['current']['id']==a
    assert clear(project,fresh).status_code==200 and clear(project,fresh).status_code==409
    assert history(project)['total']==4


def test_wrong_id_wrong_revision_no_baseline_and_failed_set_do_not_add_events(project):
    rid=seed(project);project.store.set_baseline(rid);data=history(project)
    assert clear(project,data,confirm_run_id='wrong').status_code==409
    assert clear(project,data,expected_event_id='wrong').status_code==409
    assert clear(project,data,expected_event_id=None).status_code==409
    assert project.client.post('/api/runs/missing/baseline').status_code==404
    archived=seed(project);project.store._trash_run(archived)
    assert project.client.post('/api/runs/'+archived+'/baseline').status_code==404
    assert history(project)['total']==1 and history(project)['current']['id']==rid


def test_old_database_builds_audit_table_and_does_not_invent_current_history(project):
    rid=seed(project)
    with project.store._session() as s:
        s.execute(update(Run).where(Run.id==rid).values(is_baseline=True));s.commit()
    before=project.store.get_run(rid)
    BaselineAudit.__table__.drop(project.store._engine);project.store._engine.dispose()
    reopened=Store(project.db)
    assert 'baseline_audit' in inspect(reopened._engine).get_table_names() and reopened.get_run(rid)==before
    data=reopened.get_baseline_history(project.pid)
    assert data['legacy_current'] and data['revision'] is None and data['total']==0
    reopened._clear_baseline(project.pid,rid,None)
    assert reopened.get_baseline_history(project.pid)['items'][0]['previous_run_id']==rid
    assert Store(project.db).get_baseline_history(project.pid)['total']==1


def test_shared_token_identity_is_not_the_token_and_cannot_be_supplied_by_client(project,monkeypatch):
    rid=seed(project)
    monkeypatch.setenv('SPECAGENT_API_TOKEN','private-baseline-token')
    headers={'Authorization':'Bearer private-baseline-token'}
    assert project.client.post('/api/runs/'+rid+'/baseline',headers=headers).status_code==200
    response=project.client.get('/api/baselines/history',params={'project_id':project.pid},headers=headers)
    data=response.json()
    assert data['items'][0]['actor']=='shared_token_user' and data['items'][0]['identity']=='shared_token'
    assert 'private-baseline-token' not in response.text
    body={'confirm_run_id':rid,'expected_event_id':data['revision'],'actor':'admin'}
    assert project.client.post('/api/baselines/clear',params={'project_id':project.pid},json=body,headers=headers).status_code==422
    assert project.store.get_baseline_history(project.pid)['total']==1


def test_cli_main_records_process_identity_and_existing_stdout(project,monkeypatch,capsys):
    rid=seed(project)
    monkeypatch.setattr('app.baseline_audit.getpass.getuser',lambda:'baseline-local-user')
    assert cli_main(['baseline',rid,'--db',project.db])==0
    assert 'baseline-local-user' not in capsys.readouterr().out
    event=history(project)['items'][0]
    assert event['source']=='cli' and event['actor']=='baseline-local-user' and event['identity']=='os_user'


def test_project_and_legacy_run_set_baseline_are_attributed_to_web(project):
    run=post(project,set_baseline=True)['run']
    assert history(project)['items'][0]['source']=='web' and history(project)['current']['id']==run['id']
    response=project.client.post('/api/runs',json={'project_id':project.pid,'text':'退款必须人工确认。','agent':'demo','set_baseline':True})
    assert response.status_code==200 and history(project)['items'][0]['source']=='web'


def test_agent_registry_and_dashboard_approval_keep_identity_and_human_gate(project,monkeypatch):
    from types import SimpleNamespace
    from app import agent_api
    from app.agent.tools import ToolContext, ToolRegistry, ConfirmResult
    rid=seed(project)
    ctx=ToolContext(project=SimpleNamespace(store=project.store,project_id=project.pid),sandbox=None,
                    confirm=lambda _:ConfirmResult(decision='approved'))
    def approve(*_):return ToolRegistry().call(ctx,'set_baseline',{'run_id':rid},'baseline-call')
    with baseline_context(source='cli',actor='local-agent-user',identity='os_user'):
        assert approve().payload['ok']
    assert history(project)['items'][0]['source']=='agent' and history(project)['items'][0]['actor']=='local-agent-user'
    monkeypatch.setenv('SPECAGENT_API_TOKEN','agent-baseline-test-token')
    monkeypatch.setattr(agent_api,'_approve_with_identity',approve)
    assert agent_api._approve_locked(None,None).payload['ok']
    event=project.store.get_baseline_history(project.pid)['items'][0]
    assert event['source']=='agent' and event['identity']=='shared_token' and event['actor']=='shared_token_user'


def test_audit_references_remain_readable_after_clear_and_recoverable_run_deletion(project):
    rid=seed(project);project.store.set_baseline(rid)
    assert clear(project,history(project)).status_code==200
    project.store._trash_run(rid)
    data=history(project)
    assert data['total']==2 and data['items'][0]['previous_run_id']==rid and data['items'][1]['run_id']==rid
    assert project.store.get_run(rid) is not None and project.store.list_runs(project.pid)==[]


def test_context_restores_and_parallel_actor_contexts_do_not_mix(project):
    original=baseline_identity()
    a,b=seed(project),seed(project)
    def change(pair):
        rid,actor=pair
        with baseline_context(source='agent',actor=actor,identity='os_user'):
            project.store.set_baseline(rid)
        assert baseline_identity()==original
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(change,[(a,'alice'),(b,'bob')]))
    assert {e['run_id']:e['actor'] for e in history(project)['items']}=={a:'alice',b:'bob'}
    assert baseline_identity()==original


def test_atomic_concurrent_sets_and_clear_never_lose_history_or_clear_a_new_baseline(project):
    a,b=seed(project),seed(project);project.store.set_baseline(a);snapshot=history(project)
    def change():project.store.set_baseline(b)
    def remove():
        try:project.store._clear_baseline(project.pid,a,snapshot['revision'])
        except RunManagementError:pass
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda fn:fn(),[change,remove]))
    data=history(project)
    assert data['current']['id']==b
    chronological=list(reversed(data['items']))
    assert len(chronological) in (2,3) and chronological[-1]['run_id']==b
    assert chronological[-1]['previous_run_id']==(None if len(chronological)==3 else a)


def test_history_pagination_project_isolation_and_quoted_identifiers(project):
    rid=seed(project)
    for _ in range(43):project.store.set_baseline(rid)
    other=seed(project,pid="quote';//");project.store.set_baseline(other)
    ids=[]
    for offset in (0,20,40):
        data=history(project,offset=offset)
        assert data['total']==43 and data['has_more']==(offset<40)
        ids.extend(e['id'] for e in data['items'])
    assert len(ids)==len(set(ids))==43
    response=project.client.get('/api/baselines/history',params={'project_id':"quote';//"})
    assert response.status_code==200 and response.json()['total']==1


def test_clear_updates_diff_metrics_trends_but_keeps_runs_specs_and_counts(project):
    baseline=post(project,set_baseline=True)['run'];project.switch('broken');candidate=post(project)['run']
    before=project.store.get_run(candidate['id']);total=project.store.get_spec(candidate['spec_id'])['run_count']
    assert clear(project,history(project)).status_code==200
    assert project.store.get_run(candidate['id'])==before and project.store.get_spec(candidate['spec_id'])['run_count']==total
    assert project.client.get('/api/diff',params={'project_id':project.pid,'candidate':candidate['id']}).status_code==404
    metrics=project.client.get('/api/metrics',params={'project_id':project.pid}).json()
    assert metrics['runs']==2
    trends=project.client.get('/api/metrics/history',params={'project_id':project.pid}).json()
    assert trends['baseline_id'] is None
    assert project.client.get('/api/diff',params={'baseline':baseline['id'],'candidate':candidate['id']}).status_code==200


@pytest.mark.parametrize('params',[{'offset':-1},{'limit':0},{'limit':101},{'project_id':''},{'project_id':'x'*65}])
def test_history_query_bounds(project,params):
    response=project.client.get('/api/baselines/history',params={'project_id':project.pid,**params})
    assert response.status_code==422


def test_auth_json_host_validation_and_redacted_audit_metadata(project,monkeypatch):
    rid=seed(project)
    with baseline_context(actor='C:\\private\\user.txt secret-audit-value',source='cli',identity='os_user'):project.store.set_baseline(rid)
    monkeypatch.setenv('BASELINE_TEST_SECRET','secret-audit-value')
    response=project.client.get('/api/baselines/history',params={'project_id':project.pid})
    assert 'secret-audit-value' not in response.text and 'C:\\' not in response.text
    snapshot=response.json();body={'confirm_run_id':rid,'expected_event_id':snapshot['revision']}
    assert project.client.post('/api/baselines/clear',params={'project_id':project.pid},json=body,headers={'Host':'attacker.invalid'}).status_code==403
    assert project.client.post('/api/baselines/clear',params={'project_id':project.pid},content='{}',headers={'Content-Type':'text/plain'}).status_code==422
    assert project.client.post('/api/baselines/clear',params={'project_id':project.pid},json={'confirm_run_id':rid}).status_code==422
    monkeypatch.setenv('SPECAGENT_API_TOKEN','baseline-auth-token')
    assert project.client.get('/api/baselines/history',params={'project_id':project.pid}).status_code==401
    assert project.client.post('/api/baselines/clear',params={'project_id':project.pid},json=body).status_code==401
    assert project.client.post('/api/baselines/clear',params={'project_id':project.pid},json=body,headers={'X-API-Key':'baseline-auth-token'}).status_code==200
