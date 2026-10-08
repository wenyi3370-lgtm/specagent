"""History queries and reversible deletion on private DBs, without Agent loads."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import inspect

from app import main
from app.cancellation import CancelRegistry
from app.storage import Store, RunTrash, RunManagementError
from test_cli_web_parity import project, post


def page(p, **params):
    response = p.client.get('/api/runs/search', params={'project_id':p.pid, **params})
    assert response.status_code == 200, response.text
    return response.json()


def seed(p, label='', status='completed', pid=None):
    rid = p.store.create_run(project_id=pid or p.pid, spec={}, tests=[], label=label, agent='demo:fixed')
    if status != 'running':
        p.store.complete_run(rid, passed=0, failed=0, errors=0, total=0, score=0, status=status)
    return rid


def trash(p, rid, **body):
    return p.client.request('DELETE','/api/runs/'+rid, json={'confirm_run_id':rid, **body})


def test_pagination_reaches_all_207_records_with_no_duplicates_or_200_cutoff(project):
    ids = [seed(project, f'History {i}') for i in range(207)]
    seed(project, pid='another-project')
    seen = []
    for offset in (0,100,200):
        d = page(project, limit=100, offset=offset)
        assert d['total'] == 207 and d['has_more'] == (offset!=200)
        seen.extend(r['id'] for r in d['items'])
    assert set(seen) == set(ids) and len(seen) == len(set(seen))
    assert page(project, offset=1000)['items'] == []
    assert len(project.client.get('/api/runs', params={'project_id':project.pid,'limit':200}).json()) == 200


def test_literal_case_insensitive_search_and_status_baseline_filters(project):
    literal = seed(project, r'100%_完成\记录')
    completed = seed(project, 'Release BLUE')
    canceled = seed(project, 'release blue', 'canceled')
    seed(project, 'unrelated', 'running')
    project.store.set_baseline(completed)
    for q in ('%', '%_', '\\', '完成'):
        assert [r['id'] for r in page(project,q=q)['items']] == [literal]
    assert page(project,q="' OR 1=1 --")['total'] == 0
    assert page(project,q='blue')['total'] == 2
    assert [r['id'] for r in page(project,q='blue',status='canceled')['items']] == [canceled]
    assert [r['id'] for r in page(project,baseline='yes')['items']] == [completed]
    assert page(project,baseline='no')['total'] == 3
    assert page(project,q='demo:fixed')['total'] == 4


def test_label_edit_clear_and_redaction_leave_results_and_config_unchanged(project, monkeypatch):
    run = post(project)['run']
    before = project.store.get_run(run['id'])
    cfg, spec = project.cfg.read_bytes(), (project.root/'specs/behavior.yaml').read_bytes()
    secret = 'private-run-label-secret'
    monkeypatch.setenv('LABEL_TEST_SECRET', secret)
    response = project.client.patch('/api/runs/'+run['id']+'/label', json={'label':'<img> '+secret+' C:\\private\\data.txt'})
    assert response.status_code == 200 and secret not in response.text and '[path]' in response.text
    after = project.store.get_run(run['id'])
    assert {k:v for k,v in before.items() if k!='label'} == {k:v for k,v in after.items() if k!='label'}
    assert after['label'].startswith('<img> '+secret)
    assert secret not in project.client.get('/api/runs/search',params={'project_id':project.pid}).text
    assert project.client.patch('/api/runs/'+run['id']+'/label', json={'label':''}).status_code == 200
    assert project.store.get_run(run['id'])['label'] == ''
    assert project.cfg.read_bytes()==cfg and (project.root/'specs/behavior.yaml').read_bytes()==spec


def test_delete_restore_preserves_all_evidence_and_updates_every_default_count(project):
    baseline = post(project,set_baseline=True)['run']
    project.switch('broken')
    candidate = post(project)['run']
    rid = candidate['id']
    before = project.store.get_run(rid)
    management = project.client.get('/api/runs/'+rid+'/management').json()
    assert management['can_delete'] and management['executions'] == candidate['total'] and management['violations']>0
    assert trash(project,rid).status_code == 200
    assert project.store.get_run(rid) == before
    assert page(project)['total'] == 1 and page(project,view='trash')['items'][0]['id']==rid
    assert project.client.get('/api/metrics',params={'project_id':project.pid}).json()['runs']==1
    assert project.client.get('/api/metrics/history',params={'project_id':project.pid}).json()['total']==1
    assert next(p for p in project.store.list_projects() if p['id']==project.pid)['runs']==1
    assert project.store.get_spec(candidate['spec_id'])['run_count']==2  # links/evidence retained
    assert project.client.get('/api/runs/'+rid+'/export?format=json').status_code == 200
    assert project.store.get_baseline(project.pid)['id']==baseline['id']
    assert project.client.post('/api/runs/'+rid+'/baseline').status_code == 404
    assert project.store.get_baseline(project.pid)['id']==baseline['id']  # failed setter rolls back
    assert project.client.post('/api/runs/'+rid+'/restore',json={}).status_code==200
    assert project.store.get_run(rid)==before and page(project)['total']==2 and page(project,view='trash')['total']==0
    assert project.client.get('/api/metrics',params={'project_id':project.pid}).json()['runs']==2


def test_current_baseline_and_running_records_and_live_registry_are_protected(project, monkeypatch):
    baseline = seed(project)
    project.store.set_baseline(baseline)
    assert trash(project,baseline).status_code==409
    running = seed(project,status='running')
    assert trash(project,running).status_code==409
    assert not project.client.get('/api/runs/'+running+'/management').json()['can_delete']
    finished = seed(project)
    registry = CancelRegistry();registry.register(finished)
    monkeypatch.setattr(main,'cancel_registry',registry)
    assert trash(project,finished).status_code==409
    assert page(project,view='trash')['total']==0


def test_confirmation_rechecked_against_new_baseline_and_duplicate_delete(project):
    rid = seed(project)
    assert project.client.get('/api/runs/'+rid+'/management').json()['can_delete']
    project.store.set_baseline(rid)
    assert trash(project,rid).json()['detail']=='baseline_protected'
    other = seed(project)
    assert trash(project,other,confirm_run_id=rid).status_code==422
    assert trash(project,other).status_code==200
    assert trash(project,other).status_code==409
    assert project.client.post('/api/runs/'+other+'/restore',json={}).status_code==200
    assert project.client.post('/api/runs/'+other+'/restore',json={}).status_code==200


def test_concurrent_baseline_and_deletion_never_create_a_trashed_baseline(project):
    for _ in range(10):
        rid = seed(project)
        def set_base():
            try: project.store.set_baseline(rid)
            except KeyError: pass
        def remove():
            try: project.store._trash_run(rid)
            except RunManagementError: pass
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda fn:fn(), (set_base,remove)))
        data = project.store.get_run_management(rid)
        assert not (data['run']['is_baseline'] and data['deleted_at'])


def test_pre_feature_database_gains_trash_table_without_losing_records(project):
    rid = seed(project,'legacy label')
    before = project.store.get_run(rid)
    RunTrash.__table__.drop(project.store._engine)
    project.store._engine.dispose()
    upgraded = Store(project.db)
    assert 'run_trash' in inspect(upgraded._engine).get_table_names()
    assert upgraded.get_run(rid)==before and upgraded.get_runs_page(project.pid)['total']==1
    upgraded._trash_run(rid);upgraded._restore_run(rid)
    assert upgraded.get_run(rid)==before


@pytest.mark.parametrize('params', [{'q':'q'*201},{'limit':0},{'limit':101},{'offset':-1},
    {'status':'FAIL'},{'baseline':'invalid'},{'view':'other'},{'project_id':''}])
def test_search_parameters_are_bounded(project,params):
    assert project.client.get('/api/runs/search',params=params).status_code==422


@pytest.mark.parametrize('method,suffix,body', [
    ('PATCH','/label',{'label':'x'*257}),('PATCH','/label',{'label':'x','agent':'danger'}),
    ('DELETE','',{'confirm_run_id':'r','spec':'C:\\private\\spec.yaml'}),
    ('POST','/restore',{'endpoint':'https://user:private-password@example.invalid'}),
])
def test_mutation_bodies_reject_config_fields_and_do_not_echo_inputs(project,method,suffix,body):
    response=project.client.request(method,'/api/runs/run_missing'+suffix,json=body)
    assert response.status_code==422 and 'private-password' not in response.text and 'C:\\' not in response.text


def test_auth_json_and_loopback_host_guards_and_missing_records(project,monkeypatch):
    rid=seed(project)
    for method,suffix,body in [('PATCH','/label',{'label':'valid'}),('DELETE','',{'confirm_run_id':rid}),('POST','/restore',{})]:
        url='/api/runs/'+rid+suffix
        assert project.client.request(method,url,json=body,headers={'Host':'attacker.invalid'}).status_code==403
        assert project.client.request(method,url,content='{}',headers={'Content-Type':'text/plain'}).status_code==422
    monkeypatch.setenv('SPECAGENT_API_TOKEN','management-test-token')
    assert page_auth(project,'/api/runs/search')==401
    assert project.client.get('/api/runs/'+rid+'/management').status_code==401
    assert project.client.patch('/api/runs/'+rid+'/label',json={'label':'x'}).status_code==401
    for headers in ({'Authorization':'Bearer management-test-token'},{'X-API-Key':'management-test-token'}):
        assert project.client.get('/api/runs/search',headers=headers).status_code==200
        assert project.client.patch('/api/runs/'+rid+'/label',json={'label':'x'},headers=headers).status_code==200
    headers={'Authorization':'Bearer management-test-token'}
    assert project.client.get('/api/runs/missing/management',headers=headers).status_code==404
    assert project.client.post('/api/runs/missing/restore',json={},headers=headers).status_code==404


def page_auth(p,url):
    return p.client.get(url).status_code
