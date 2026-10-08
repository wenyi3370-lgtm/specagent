"""Project creation stores metadata, never loads or selects a tested Agent."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import main, project as core
from test_cli_web_parity import project, post


def create(p, **fields):
    return p.client.post('/api/projects', json={'id':'web-project', 'name':'Web project', **fields})


def test_metadata_creation_does_not_import_adapter_or_change_project_files(project, monkeypatch):
    before = {p.relative_to(project.root):p.read_bytes() for p in project.root.rglob('*') if p.is_file()}
    def forbidden(*args, **kwargs):
        pytest.fail('metadata creation must not load a tested Agent')
    monkeypatch.setattr(core.Project, 'load', forbidden)
    monkeypatch.setattr(main, 'resolve_adapter', forbidden)
    response = create(project, name='审查项目', description='用于评审\n只保存资料', adapter_type='http')
    assert response.status_code == 200
    data = response.json()
    assert data['id'] == 'web-project' and data['name'] == '审查项目'
    assert data['description'] == '用于评审\n只保存资料' and data['adapter_type'] == 'http'
    assert data['created_at']
    entry = next(p for p in project.client.get('/api/projects').json() if p['id'] == data['id'])
    assert entry == {**data, 'runs':0, 'last_run_at':None}
    assert project.store.list_runs(project_id=data['id']) == []
    assert {p.relative_to(project.root):p.read_bytes() for p in project.root.rglob('*') if p.is_file()} == before


def test_creation_retains_configured_target_and_existing_run_baseline_and_spec(project):
    run = post(project, set_baseline=True)['run']
    before = project.client.get('/api/project').json()
    assert create(project, adapter_type='openai').status_code == 200
    assert project.client.get('/api/project').json() == before
    assert project.store.get_baseline(project.pid)['id'] == run['id']
    assert project.store.get_run(run['id'])['spec_id'] == run['spec_id']
    assert project.client.get('/api/runs', params={'project_id':'web-project'}).json() == []
    assert project.client.get('/api/specs', params={'project_id':'web-project'}).json() == []


def test_duplicate_returns_conflict_without_overwriting_metadata(project):
    first = create(project, description='original').json()
    response = create(project, name='overwrite', description='changed')
    assert response.status_code == 409 and response.json()['detail'] == 'project_already_exists'
    saved = next(p for p in project.store.list_projects() if p['id'] == 'web-project')
    assert {k:saved[k] for k in first} == first


def test_concurrent_duplicate_creation_returns_one_record_and_conflicts(project):
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda n:create(project, name=f'Candidate {n}'), range(8)))
    assert sorted(r.status_code for r in responses) == [200]+[409]*7
    winner = next(r.json() for r in responses if r.status_code == 200)
    assert next(p for p in project.store.list_projects() if p['id']=='web-project')['name'] == winner['name']


@pytest.mark.parametrize('identifier', [None, ''])
def test_existing_api_name_derived_id_contract(project, identifier):
    response = create(project, id=identifier, name='Simple project')
    assert response.status_code == 200 and response.json()['id'] == 'simple-project'


@pytest.mark.parametrize('fields', [
    {'name':''}, {'name':' \t '}, {'name':'n'*129}, {'id':'i'*65}, {'id':'  '}, {'id':'line\nbreak'},
    {'id':None,'name':'n'*65}, {'description':'d'*2001}, {'adapter_type':'a'*33},
    {'endpoint':'https://user:private-password@example.invalid'},
    {'spec':'C:\\private\\behavior.yaml'}, {'config':'C:\\private\\specagent.yaml'},
    {'adapter':{'type':'python','agent':'danger:run'}},
])
def test_invalid_metadata_and_target_configuration_fields_are_rejected(project, fields):
    response = create(project, **fields)
    assert response.status_code == 422
    assert 'private-password' not in response.text and 'C:\\' not in response.text
    assert project.store.list_projects() == []


def test_metadata_authentication_and_display_redaction(project, monkeypatch):
    secret = 'private-project-test-secret'
    monkeypatch.setenv('SPECAGENT_API_TOKEN', 'project-test-token')
    monkeypatch.setenv('PROJECT_TEST_SECRET', secret)
    assert create(project).status_code == 401
    assert project.client.get('/api/projects').status_code == 401
    headers = {'Authorization':'Bearer project-test-token'}
    response = project.client.post('/api/projects', headers=headers, json={
        'id':'web-project', 'name':'Project '+secret,
        'description':'C:\\private\\data.txt https://user:private-password@example.invalid '+secret,
    })
    assert response.status_code == 200
    for data in (response, project.client.get('/api/projects', headers={'X-API-Key':'project-test-token'})):
        assert data.status_code == 200 and secret not in data.text and 'private-password' not in data.text
        assert '[redacted]' in data.text and '[path]' in data.text
    assert next(p for p in project.store.list_projects() if p['id']=='web-project')['name'] == 'Project '+secret


def test_unicode_and_quotes_are_metadata_not_paths_or_executable_code(project):
    identifier="评审';window.projectInjected=true;//"
    response = create(project, id=identifier, name='<img src=x onerror=alert(1)>', description='<script>test</script>')
    assert response.status_code == 200 and response.json()['id'] == identifier
    assert next(p for p in project.client.get('/api/projects').json() if p['id']==identifier)['description'] == '<script>test</script>'


def test_explicit_id_allows_full_valid_metadata_lengths(project):
    response = create(project, id='i'*64, name='n'*128, description='d'*2000, adapter_type='a'*32)
    assert response.status_code == 200
    data = response.json()
    assert tuple(len(data[k]) for k in ('id','name','description','adapter_type')) == (64,128,2000,32)
