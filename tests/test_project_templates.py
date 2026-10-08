"""Static web scaffolds match CLI init without inspecting a deployment."""
import builtins
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main, project_templates as templates
from app.accounts import Accounts
from cli import specagent as cli
from test_cli_web_parity import project


@pytest.mark.parametrize('adapter',['demo','http','openai','python'])
def test_web_templates_match_cli_files_and_remain_parseable(tmp_path,capsys,adapter):
    target=tmp_path/adapter
    assert cli.main(['init',str(target),'--adapter',adapter])==0
    response=TestClient(main.app).get('/api/project/templates/'+adapter)
    assert response.status_code==200
    data=response.json()
    assert data['config_yaml']==(target/'specagent.yaml').read_text(encoding='utf-8')
    assert data['spec_yaml']==(target/'specs/behavior.yaml').read_text(encoding='utf-8')
    from app.config import load_config
    from app.spec_yaml import load_spec_file
    assert load_config(str(target/'specagent.yaml')).adapter.type==adapter
    assert load_spec_file(target/'specs/behavior.yaml').rules
    assert data['cli_command']=='specagent init --adapter '+adapter
    assert data['environment_variables']==['SPECAGENT_PROJECT_CONFIG']+({'http':['TARGET_AGENT_URL'],'openai':['OPENAI_API_KEY']}.get(adapter,[]))
    assert data['optional_environment_variables']==(['OPENAI_BASE_URL'] if adapter=='openai' else [])
    capsys.readouterr()


@pytest.mark.parametrize('adapter',['langgraph','unknown','HTTP','__init__','..%2Fprivate'])
def test_unknown_adapter_has_no_template(adapter):
    assert TestClient(main.app).get('/api/project/templates/'+adapter).status_code==404


def test_template_request_never_loads_project_database_files_or_private_values(monkeypatch):
    client=TestClient(main.app)
    for name in ('OPENAI_API_KEY','TARGET_AGENT_URL','SPECAGENT_PROJECT_CONFIG','OPENAI_BASE_URL'):
        monkeypatch.setenv(name,'private-must-never-appear-'+name)
    def blocked(*_,**__):pytest.fail('static template touched deployment state')
    monkeypatch.setattr(main,'project_config_path',blocked)
    monkeypatch.setattr(main.Project,'load',blocked)
    class NoStore:
        def __getattr__(self,_):blocked()
    monkeypatch.setattr(main,'store',NoStore())
    monkeypatch.setattr(builtins,'open',blocked)
    monkeypatch.setattr(Path,'read_text',blocked)
    for adapter in ('demo','http','openai','python'):
        response=client.get('/api/project/templates/'+adapter)
        assert response.status_code==200 and 'private-must-never-appear' not in response.text
    assert client.post('/api/project/templates/demo',json={'path':'private-must-never-appear'}).status_code==405


def test_template_authentication_and_unassigned_viewer_without_config(project,monkeypatch):
    monkeypatch.setenv('SPECAGENT_API_TOKEN','fake-guide-token')
    assert project.client.get('/api/project/templates/demo').status_code==401
    project.client.headers['Authorization']='Bearer fake-guide-token'
    assert project.client.get('/api/project/templates/demo').status_code==200
    accounts=Accounts(project.store);accounts.create_user('guide-viewer','guide-fixture-password-2026')
    monkeypatch.setenv('SPECAGENT_AUTH_MODE','multiuser')
    client=TestClient(main.app,base_url='https://testserver')
    assert client.get('/api/project/templates/demo').status_code==401
    response=client.post('/api/auth/login',json={'username':'guide-viewer','password':'guide-fixture-password-2026'})
    client.headers['X-SpecAgent-CSRF']=response.json()['csrf']
    monkeypatch.setattr(main,'project_config_path',lambda:pytest.fail('template inspected project'))
    assert client.get('/api/project/templates/demo').status_code==200
    assert client.post('/api/project/validate',json={}).status_code==403
