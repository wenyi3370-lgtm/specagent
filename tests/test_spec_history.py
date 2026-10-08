"""Read-only sources and behavior versions on private projects only."""
import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.spec_views import MAX_SOURCE_CHARS
from test_cli_web_parity import project, post


def saved(project, source='rules: []\n', rules=None, pid=None):
    return project.store.save_spec(pid or project.pid, source,
        {'compiler':'test', 'rules':rules or []})


def test_current_yaml_is_read_only_and_does_not_import_adapter(project):
    project.cfg.write_text(project.cfg.read_text().replace('agent: parity_', 'agent: missing_parity_'), encoding='utf-8')
    response = project.client.get('/api/project/spec')
    assert response.status_code == 200, response.text
    d = response.json()
    assert d['source'] == (project.root/'specs'/'behavior.yaml').read_bytes().decode('utf-8')
    assert d['project_id'] == project.pid and d['source_kind'] == 'yaml'
    assert d['compiled']['rules'] and not d['source_redacted']
    assert project.store.list_specs(project.pid) == []
    assert project.store.list_runs(project.pid) == []


def test_versions_keep_sources_diff_and_associated_real_runs(project):
    path = project.root/'specs'/'behavior.yaml'
    old = path.read_bytes().decode('utf-8')
    first = post(project)['run']
    path.write_bytes(old.replace('severity: critical', 'severity: high').encode('utf-8'))
    second = post(project)['run']
    assert first['spec_id'] != second['spec_id']
    versions = project.client.get('/api/specs', params={'project_id':project.pid}).json()
    assert [v['version'] for v in versions] == [2, 1]
    d = project.client.get('/api/specs/'+first['spec_id']).json()
    assert d['source'] == old and d['source_kind'] == 'yaml'
    assert d['run_count'] == 1 and d['runs'][0]['id'] == first['id']
    assert d['compiled'] == first['spec']
    diff = project.client.get('/api/specs/diff', params={'baseline':first['spec_id'], 'candidate':second['spec_id']}).json()
    assert '-    severity: critical' in diff['diff'] and '+    severity: high' in diff['diff']
    assert diff['added_rules'] == diff['removed_rules'] == []
    download = project.client.get('/api/specs/'+first['spec_id']+'/source')
    assert download.content == old.encode('utf-8')
    assert 'attachment' in download.headers['content-disposition'] and download.headers['x-content-type-options'] == 'nosniff'


def test_comment_changes_keep_behavior_version_but_current_source_updates(project):
    path = project.root/'specs'/'behavior.yaml'
    first = post(project)['run']
    original = path.read_bytes().decode('utf-8')
    path.write_bytes(('# updated comment\n'+original).encode('utf-8'))
    second = post(project)['run']
    assert first['spec_id'] == second['spec_id']
    assert len(project.store.list_specs(project.pid)) == 1
    assert project.client.get('/api/project/spec').json()['source'].startswith('# updated comment')
    d = project.client.get('/api/specs/'+first['spec_id']).json()
    assert d['source'] == original and d['run_count'] == 2


def test_rule_changes_empty_diff_and_cross_project_rejection(project):
    a = saved(project, 'rules: [A]\n', [{'id':'A'}])
    b = saved(project, 'rules: [B]\n', [{'id':'B'}])
    diff = project.client.get('/api/specs/diff', params={'baseline':a, 'candidate':b}).json()
    assert diff['added_rules'] == ['B'] and diff['removed_rules'] == ['A']
    assert project.client.get('/api/specs/diff', params={'baseline':b, 'candidate':b}).json()['diff'] == ''
    c = saved(project, pid='another-project')
    assert project.client.get('/api/specs/diff', params={'baseline':a, 'candidate':c}).status_code == 422
    assert project.client.get('/api/specs/diff', params={'baseline':'missing', 'candidate':c}).status_code == 404
    assert project.client.get('/api/specs/missing').status_code == 404
    assert project.client.get('/api/specs/missing/source').status_code == 404


@pytest.mark.parametrize('kind,source', [('yaml','rules: []\n'), ('json',json.dumps({'rules':[]})), ('text','ordinary draft text')])
def test_legacy_source_formats_and_download(project, kind, source):
    sid = saved(project, source)
    d = project.client.get('/api/specs/'+sid).json()
    assert d['source_kind'] == kind and d['source'] == source
    assert project.client.get('/api/specs/'+sid+'/source').text == source


def test_sources_and_diffs_use_existing_web_redaction(project, monkeypatch):
    secret='spec-history-private-secret'
    monkeypatch.setenv('TEST_SPEC_SECRET', secret)
    a = saved(project, '# '+secret+' C:\\private\\data.txt\nrules: []\n')
    b = saved(project, '# changed '+secret+'\nrules: [A]\n', [{'id':'A'}])
    for path in ['/api/specs/'+a, '/api/specs/'+a+'/source', '/api/specs/diff?baseline='+a+'&candidate='+b]:
        response = project.client.get(path)
        assert secret not in response.text and 'C:\\private' not in response.text
    d = project.client.get('/api/specs/'+a).json()
    assert d['source_redacted'] and '[redacted]' in d['source']
    assert project.client.get('/api/specs/'+a+'/source').text == d['source']
    path = project.root/'specs'/'behavior.yaml'
    path.write_text('# '+secret+'\n'+path.read_text(encoding='utf-8'), encoding='utf-8')
    current = project.client.get('/api/project/spec').json()
    assert current['source_redacted'] and secret not in str(current)


def test_authentication_all_read_endpoints(project, monkeypatch):
    sid = saved(project)
    monkeypatch.setenv('SPECAGENT_API_TOKEN', 'spec-history-test-token')
    paths=['/api/project/spec', '/api/specs?project_id='+project.pid, '/api/specs/'+sid,
           '/api/specs/'+sid+'/source', '/api/specs/diff?baseline='+sid+'&candidate='+sid]
    for path in paths:
        assert TestClient(main.app).get(path).status_code == 401
        for headers in ({'Authorization':'Bearer spec-history-test-token'}, {'X-API-Key':'spec-history-test-token'}):
            assert project.client.get(path, headers=headers).status_code == 200


def test_oversized_stored_source_is_bounded(project):
    sid = saved(project, 'x'*(MAX_SOURCE_CHARS+1))
    for path in ['/api/specs/'+sid, '/api/specs/'+sid+'/source', '/api/specs/diff?baseline='+sid+'&candidate='+sid]:
        assert project.client.get(path).status_code == 413


def test_associated_runs_limit_is_explicit_without_losing_total(project):
    sid = saved(project)
    for i in range(53):
        project.store.create_run(project_id=project.pid, spec={}, tests=[], spec_id=sid, label=str(i))
    d = project.client.get('/api/specs/'+sid).json()
    assert d['run_count'] == 53 and len(d['runs']) == 50
