"""Authenticated, side-effect-free settings on private configuration files."""
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app import __version__, main, settings_view as view
from app.errors import SpecValidationError
from app.llm_client import DEFAULT_MODEL
from app.storage import Store


@pytest.fixture
def settings(tmp_path, monkeypatch):
    cfg = tmp_path / 'specagent.yaml'
    monkeypatch.setenv('SPECAGENT_PROJECT_CONFIG', str(cfg))
    for name in ('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_MODEL',
                 'SPECAGENT_AGENT_MODEL', 'SPECAGENT_API_TOKEN',
                 'SPECAGENT_AGENT_API_INSECURE', 'TARGET_AGENT_URL'):
        monkeypatch.delenv(name, raising=False)
    store = Store(str(tmp_path / 'settings.db'))
    monkeypatch.setattr(main, 'store', store)
    client = TestClient(main.app)

    def write(adapter=None, **extra):
        data = {'project': 'settings-demo', 'spec': 'missing-spec.yaml',
                'adapter': adapter or {'type': 'demo', 'variant': 'patched'}}
        data.update(extra)
        cfg.write_text(yaml.safe_dump(data), encoding='utf-8')
        return cfg

    return cfg, client, write


def read(settings):
    response = settings[1].get('/api/settings')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    return response.json()


def test_missing_config_is_an_explicit_offline_snapshot(settings):
    d = read(settings)
    assert d['version'] == __version__
    assert d['db_backend'] == 'sqlite'
    assert datetime.fromisoformat(d['read_at']).utcoffset().total_seconds() == 0
    assert d['target']['state'] == 'missing' and not d['target']['configured']
    assert d['llm']['key_status'] == 'missing'
    assert d['llm']['provider'] == 'sdk_default'
    assert d['llm']['connection'] == 'not_checked'
    assert d['auth'] == {'mode': 'local', 'agent_api_enabled': False,
                         'individual_accounts': False}
    assert d['capabilities'] == {'deterministic_gate': True, 'llm_judge_advisory': True,
                                'draft_preview_only': True, 'fix_apply_in_browser': False,
                                'settings_read_only': True}


@pytest.mark.parametrize('kind', ['demo', 'python', 'http', 'langgraph', 'openai'])
def test_all_adapters_are_described_without_resolving_targets(settings, monkeypatch, kind):
    settings[2]({'type': kind, 'agent': 'unavailable_target:run', 'model': 'target-demo',
                 'variant': 'patched', 'instructions': 'private instructions',
                 'allowed_hosts': ['private-host'], 'endpoint_env': 'SETTINGS_ENDPOINT'},
                run={'concurrency': 2, 'repeat': 3, 'llm_expand': True},
                gate={'fail_on': ['high']}, agent={'allow_source': True, 'max_steps': 6})
    monkeypatch.setenv('SETTINGS_ENDPOINT', 'https://private.example/path')
    d = read(settings)
    a = d['target']['adapter']
    assert d['target']['state'] == 'loaded' and a['type'] == kind
    assert not a['verified']
    assert a['entrypoint'] == ('unavailable_target:run' if kind in ('python', 'openai', 'langgraph') else None)
    assert a['variant'] == ('patched' if kind == 'demo' else None)
    assert a['endpoint_env'] == ('SETTINGS_ENDPOINT' if kind == 'http' else None)
    assert a['endpoint_configured'] == (True if kind == 'http' else None)
    assert a['target_model'] == ({'name': 'target-demo', 'source': 'adapter.model', 'truncated': False} if kind == 'openai' else None)
    assert a['model_state'] == ('override' if kind == 'openai' else 'not_applicable' if kind == 'demo' else 'target_owned')
    assert d['target']['run']['repeat'] == 3
    assert d['target']['gate']['fail_on'] == ['high']
    assert d['target']['agent']['allow_source']
    text = json.dumps(d)
    for private in ('private instructions', 'private-host', 'private.example', 'missing-spec', str(settings[0].parent)):
        assert private not in text


@pytest.mark.parametrize('model', [None, ''])
def test_openai_without_override_does_not_guess_target_model(settings, model):
    settings[2]({'type': 'openai', 'agent': 'unavailable:definition', 'model': model})
    a = read(settings)['target']['adapter']
    assert a['target_model'] is None and a['model_state'] == 'target_owned'


@pytest.mark.parametrize('body', ['[]', 'adapter: [', 'project: demo',
                                  'spec: missing.yaml\nadapter:\n  type: invalid'])
def test_invalid_configuration_returns_sanitized_state(settings, body):
    settings[0].write_text(body, encoding='utf-8')
    d = read(settings)['target']
    assert not d['configured'] and d['state'] == 'invalid' and d['errors']
    assert str(settings[0].parent) not in json.dumps(d)


@pytest.mark.parametrize('error', [PermissionError('private error'), UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'invalid')])
def test_unreadable_configuration_hides_exception(settings, monkeypatch, error):
    settings[2]()
    def fail(*_a, **_kw):
        raise error
    monkeypatch.setattr(view, 'load_config', fail)
    target = read(settings)['target']
    assert target['state'] == 'unreadable'
    assert target['errors'] == ['project_config_unreadable']


def test_configuration_stat_failure_is_handled(settings, monkeypatch):
    def fail(_self):
        raise PermissionError('private path')
    monkeypatch.setattr(Path, 'is_file', fail)
    assert read(settings)['target']['state'] == 'unreadable'


@pytest.mark.parametrize('agent,behavior,expected,source', [
    (None, None, DEFAULT_MODEL, 'default'),
    (None, 'behavior-demo', 'behavior-demo', 'OPENAI_MODEL'),
    ('agent-demo', 'behavior-demo', 'agent-demo', 'SPECAGENT_AGENT_MODEL'),
    ('', 'behavior-demo', 'behavior-demo', 'OPENAI_MODEL'),
    ('', '', DEFAULT_MODEL, 'default'),
    ('agent-demo', '', 'agent-demo', 'SPECAGENT_AGENT_MODEL'),
])
def test_model_precedence_and_explicit_empty_behavior(settings, monkeypatch, agent, behavior, expected, source):
    for name, value in [('SPECAGENT_AGENT_MODEL', agent), ('OPENAI_MODEL', behavior)]:
        if value is not None:
            monkeypatch.setenv(name, value)
    llm = read(settings)['llm']
    assert llm['agent_and_draft_model'] == {'name': expected, 'source': source, 'truncated': False}
    assert llm['behavior_model'] == {'name': DEFAULT_MODEL if behavior is None else behavior,
                                    'source': 'default' if behavior is None else 'OPENAI_MODEL',
                                    'truncated': False}


@pytest.mark.parametrize('key,status', [('', 'missing'), ('  \t', 'blank'), ('settings-fake-provider-key', 'configured')])
def test_key_state_never_claims_connectivity(settings, monkeypatch, key, status):
    monkeypatch.setenv('OPENAI_API_KEY', key)
    monkeypatch.setenv('OPENAI_BASE_URL', 'https://private.example/compatible')
    llm = read(settings)['llm']
    assert llm['key_status'] == status and llm['key_configured'] == (status == 'configured')
    assert llm['connection'] == 'not_checked' and llm['provider'] == 'custom'
    assert 'private.example' not in json.dumps(llm)
    if key.strip():
        assert key not in json.dumps(llm)


@pytest.mark.parametrize('found', [True, False, 'import_error', 'value_error'])
def test_sdk_detection_is_not_a_client_initialization(settings, monkeypatch, found):
    def find(name):
        assert name == 'openai'
        if found == 'import_error':
            raise ImportError
        if found == 'value_error':
            raise ValueError
        return object() if found else None
    monkeypatch.setattr(view.importlib.util, 'find_spec', find)
    assert read(settings)['llm']['sdk_installed'] == (found is True)


def test_shared_auth_is_required_and_never_echoes_token(settings, monkeypatch):
    token = 'settings-fake-shared-token'
    monkeypatch.setenv('SPECAGENT_API_TOKEN', token)
    client = settings[1]
    for headers in ({}, {'Authorization': 'Bearer wrong'}):
        r = client.get('/api/settings', headers=headers)
        assert r.status_code == 401 and 'llm' not in r.json()
    for headers in ({'Authorization': 'Bearer ' + token}, {'X-API-Key': token}):
        r = client.get('/api/settings', headers=headers)
        assert r.status_code == 200
        assert r.json()['auth'] == {'mode': 'shared_token', 'agent_api_enabled': True, 'individual_accounts': False}
        assert token not in r.text
    assert client.post('/api/settings', headers={'X-API-Key': token}, json={}).status_code == 405


def test_local_agent_opt_in_is_reported(settings, monkeypatch):
    monkeypatch.setenv('SPECAGENT_AGENT_API_INSECURE', '1')
    assert read(settings)['auth']['agent_api_enabled']


@pytest.mark.parametrize('name', ['TARGET_AGENT_URL', 'not a variable', 'https://private.example', 'X' * 129])
def test_http_only_reports_valid_variable_names_and_presence(settings, monkeypatch, name):
    settings[2]({'type': 'http', 'endpoint_env': name})
    monkeypatch.setenv(name, 'https://private.example:1234/private')
    a = read(settings)['target']['adapter']
    assert a['endpoint_env'] == (name if name == 'TARGET_AGENT_URL' else None)
    assert a['endpoint_configured'] and 'private.example' not in json.dumps(a)


def test_redaction_precedes_clipping_and_hides_all_private_fields(settings, monkeypatch):
    secret = 'sk-' + 'x' * 300
    text = 'a' * 245 + ' ' + secret + ' tail ' + 'b' * 300
    settings[2]({'type': 'openai', 'agent': text, 'model': text}, project=text,
                db='postgresql://private.example/db')
    monkeypatch.setenv('SPECAGENT_AGENT_MODEL', text)
    monkeypatch.setenv('OPENAI_MODEL', text)
    monkeypatch.setenv('OPENAI_API_KEY', 'settings-fake-provider-key')
    d = read(settings)
    assert secret[:10] not in json.dumps(d)
    assert 'settings-fake-provider-key' not in json.dumps(d) and 'private.example' not in json.dumps(d)
    assert d['target']['project_truncated'] and len(d['target']['project_id']) == 256
    assert d['target']['adapter']['entrypoint_truncated']
    for model in (d['llm']['agent_and_draft_model'], d['llm']['behavior_model'], d['target']['adapter']['target_model']):
        assert model['truncated'] and len(model['name']) == 256


def test_unknown_credentials_urls_and_paths_are_redacted(settings):
    text = 'token=private-test-credential https://other.example/private HTTPS://upper.example/private C:\\private\\server\\file /srv/private/server/file'
    settings[2]({'type': 'python', 'agent': text})
    a = read(settings)['target']['adapter']
    assert all(private not in json.dumps(a) for private in ('private-test-credential', 'other.example', 'upper.example', 'server', 'srv'))


def test_variant_and_error_payloads_are_bounded_after_redaction(settings, monkeypatch):
    settings[2]({'type': 'demo', 'variant': 'v' * 200})
    a = read(settings)['target']['adapter']
    assert a['variant_truncated'] and len(a['variant']) == 128
    def invalid(*_a, **_kw):
        raise SpecValidationError(['token=private-test-credential ' + 'x' * 1500] * 25)
    monkeypatch.setattr(view, 'load_config', invalid)
    target = read(settings)['target']
    assert target['errors_limited'] and len(target['errors']) == 20
    assert all(len(e) == 1000 and 'private-test-credential' not in e for e in target['errors'])


def test_settings_never_reads_specs_imports_targets_builds_clients_or_writes(settings, monkeypatch):
    from app import adapters, llm_client, project, spec_yaml
    module = settings[0].parent / 'settings_forbidden_target.py'
    module.write_text('raise AssertionError("target must not be imported")', encoding='utf-8')
    settings[2]({'type': 'openai', 'agent': 'settings_forbidden_target:agent'})
    monkeypatch.syspath_prepend(str(module.parent))
    monkeypatch.setenv('OPENAI_API_KEY', 'settings-fake-provider-key')
    before = {p.name: p.read_bytes() for p in module.parent.iterdir() if p.is_file()}
    def forbidden(*_a, **_kw):
        raise AssertionError('settings must only read configuration')
    monkeypatch.setattr(project.Project, 'load', forbidden)
    monkeypatch.setattr(project, 'resolve_adapter', forbidden)
    monkeypatch.setattr(adapters, 'resolve_adapter', forbidden)
    monkeypatch.setattr(llm_client, 'make_client', forbidden)
    monkeypatch.setattr(spec_yaml, 'load_spec_file', forbidden)
    monkeypatch.setattr(main.store, '_session', forbidden)
    monkeypatch.setattr(main.store._engine, 'connect', forbidden)
    monkeypatch.setattr(main.store._engine, 'begin', forbidden)
    assert read(settings)['target']['state'] == 'loaded'
    assert 'settings_forbidden_target' not in sys.modules
    assert {p.name: p.read_bytes() for p in module.parent.iterdir() if p.is_file()} == before
