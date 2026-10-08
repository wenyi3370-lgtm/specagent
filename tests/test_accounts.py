"""Account authentication and authorization on private databases and projects."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app import accounts as module, agent_api, auth, main
from app.accounts import Accounts, COOKIE, LoginSession, User, principal_context
from app.storage import Store
from test_cli_web_parity import project, post

PASSWORD = 'private-fixture-password-2026'


@pytest.fixture
def setup(project, monkeypatch):
    # Seed records through the existing local API before opting into accounts.
    result = post(project)
    run = result['run']['id']
    other = project.store.create_project('other-private', 'Other private')
    repository = Accounts(project.store)
    ids = {name: repository.create_user(name, PASSWORD, admin=name == 'admin-demo')
           for name in ('admin-demo', 'editor-demo', 'viewer-demo', 'outsider-demo')}
    repository.grant(ids['editor-demo'], project.pid, 'editor')
    repository.grant(ids['viewer-demo'], project.pid, 'viewer')
    monkeypatch.setenv('SPECAGENT_AUTH_MODE', 'multiuser')
    monkeypatch.setenv('SPECAGENT_API_TOKEN', 'ignored-shared-fixture-token')
    monkeypatch.setattr(agent_api, '_store', project.store)
    monkeypatch.setattr(agent_api, 'client_factory', lambda: None)
    agent_api.reset_sessions()

    def client(name='editor-demo'):
        c = TestClient(main.app, base_url='https://testserver')
        response = c.post('/api/auth/login', json={'username': name, 'password': PASSWORD})
        assert response.status_code == 200, response.text
        c.headers['X-SpecAgent-CSRF'] = response.json()['csrf']
        return c

    yield SimpleNamespace(project=project, repository=repository, ids=ids, run=run, client=client)
    agent_api.reset_sessions()


def test_provisioning_stores_only_salted_hashes_and_private_session_digests(setup):
    c = setup.client()
    cookie = c.cookies.get(COOKIE)
    with setup.repository.session() as db:
        user = db.scalar(select(User).where(User.username == 'editor-demo'))
        session = db.scalar(select(LoginSession).where(LoginSession.user_id == user.id))
        assert user.password_hash.startswith('pbkdf2_sha256$600000$')
        assert PASSWORD not in user.password_hash and cookie not in session.digest
        assert session.digest == module.session_digest(cookie)
    second = module.password_hash(PASSWORD)
    assert second != user.password_hash and module.password_matches(PASSWORD, second)
    assert not module.password_matches('wrong-password', second)


@pytest.mark.parametrize('username,password', [('unknown-user', PASSWORD), ('editor-demo', 'wrong')])
def test_login_has_uniform_failure_and_does_not_echo_credentials(setup, username, password):
    c = TestClient(main.app, base_url='https://testserver')
    response = c.post('/api/auth/login', json={'username': username, 'password': password})
    assert response.status_code == 401 and response.json() == {'detail': 'invalid_login'}
    assert COOKIE not in c.cookies and password not in response.text
    assert response.headers['cache-control'] == 'no-store'


def test_cookie_flags_rotation_and_logout_revoke_the_old_credential(setup):
    c = TestClient(main.app, base_url='https://testserver')
    body = {'username': 'editor-demo', 'password': PASSWORD}
    first = c.post('/api/auth/login', json=body)
    header = first.headers['set-cookie'].lower()
    assert all(flag in header for flag in ('httponly', 'secure', 'samesite=strict', 'max-age=28800'))
    old = c.cookies.get(COOKIE)
    second = c.post('/api/auth/login', json=body)
    assert c.cookies.get(COOKIE) != old and setup.repository.authenticate(old) is None
    fresh = c.cookies.get(COOKIE)
    c.headers['X-SpecAgent-CSRF'] = second.json()['csrf']
    assert c.post('/api/auth/logout').status_code == 200
    assert setup.repository.authenticate(fresh) is None
    assert c.get('/api/auth/me').status_code == 401


def test_multiuser_rejects_shared_tokens_and_missing_cookie(setup):
    for headers in ({}, {'Authorization': 'Bearer ignored-shared-fixture-token'}, {'X-API-Key': 'ignored-shared-fixture-token'}):
        c = TestClient(main.app, headers=headers)
        assert c.get('/api/projects').status_code == 401
        assert c.post('/api/agent/sessions', json={}).status_code == 401
    health = TestClient(main.app).get('/api/health').json()
    assert health['auth_mode'] == 'multiuser' and health['auth_required'] and health['agent_enabled']


@pytest.mark.parametrize('origin', ['https://evil.example', 'null'])
def test_cross_origin_login_and_mutations_are_denied(setup, origin):
    c = setup.client()
    headers = {'Origin': origin}
    assert c.post('/api/auth/login', json={'username': 'editor-demo', 'password': PASSWORD}, headers=headers).status_code == 403
    assert c.post('/api/runs/' + setup.run + '/baseline', headers=headers).status_code == 403
    assert c.post('/api/auth/logout', headers=headers).status_code == 403


def test_missing_and_wrong_csrf_are_denied_before_writes(setup):
    c = setup.client()
    c.headers.pop('X-SpecAgent-CSRF')
    for value in ('', 'wrong'):
        response = c.patch('/api/runs/' + setup.run + '/label', json={'label': 'new'}, headers={'X-SpecAgent-CSRF': value})
        assert response.status_code == 403 and response.json()['detail'] == 'csrf_required'
    assert setup.project.store.get_run(setup.run)['label'] != 'new'


def test_remote_http_denied_and_loopback_http_allowed(setup):
    body = {'username': 'editor-demo', 'password': PASSWORD}
    c = TestClient(main.app, base_url='http://testserver', client=('192.0.2.10', 50000))
    assert c.post('/api/auth/login', json=body).status_code == 403
    c = TestClient(main.app, base_url='http://127.0.0.1:8000', client=('127.0.0.1', 50000))
    r = c.post('/api/auth/login', json=body)
    assert r.status_code == 200 and 'Secure' not in r.headers['set-cookie']
    c = TestClient(main.app, base_url='http://rebound.example', client=('127.0.0.1', 50000))
    assert c.post('/api/auth/login', json=body).status_code == 403


def test_login_validation_never_echoes_password_and_body_is_bounded(setup):
    c = TestClient(main.app, base_url='https://testserver')
    for body in ({'username': 'editor-demo', 'password': PASSWORD, 'extra': PASSWORD}, {'username': 'editor-demo', 'password': PASSWORD * 12}):
        r = c.post('/api/auth/login', json=body)
        assert r.status_code == 422 and PASSWORD not in r.text
    r = c.post('/api/auth/login', content=b'x' * 5000, headers={'Content-Type': 'application/json'})
    assert r.status_code == 413 and 'x' * 32 not in r.text


def test_login_rate_limit_persists_across_repository_instances(setup):
    repo = Accounts(setup.project.store)
    for _ in range(20):
        assert repo.login('unknown-fixture', 'wrong', 'unique-peer') is None
    with pytest.raises(HTTPException) as error:
        Accounts(setup.project.store).login('unknown-fixture', PASSWORD, 'unique-peer')
    assert error.value.status_code == 429


def test_expiry_password_reset_and_disabled_accounts_revoke_sessions(setup, monkeypatch):
    c = setup.client()
    token = c.cookies.get(COOKIE)
    with setup.repository.session.begin() as db:
        db.execute(update(LoginSession).where(LoginSession.digest == module.session_digest(token)).values(expires=0))
    assert c.get('/api/auth/me').status_code == 401
    c = setup.client()
    setup.repository.update_user('editor-demo', password=PASSWORD + '-reset')
    assert c.get('/api/auth/me').status_code == 401
    assert setup.repository.login('editor-demo', PASSWORD, 'reset-peer') is None
    token = setup.repository.login('editor-demo', PASSWORD + '-reset', 'reset-peer')
    setup.repository.update_user('editor-demo', disable=True)
    assert setup.repository.authenticate(token) is None
    assert setup.repository.login('editor-demo', PASSWORD + '-reset', 'reset-peer') is None


def test_membership_metadata_and_omitted_project_run_list_are_filtered(setup):
    c = setup.client('viewer-demo')
    projects = c.get('/api/projects').json()
    assert [p['id'] for p in projects] == [setup.project.pid]
    assert [r['id'] for r in c.get('/api/runs').json()] == [setup.run]
    assert c.get('/api/runs?project_id=other-private').status_code == 403
    me = c.get('/api/auth/me').json()
    assert me['username'] == 'viewer-demo' and not me['admin']
    assert me['projects'][0]['role'] == 'viewer' and me['server_project_role'] == 'viewer'
    assert not any(k in me for k in ('password', 'password_hash', 'session', 'token'))


@pytest.mark.parametrize('path', ['/api/project/runs', '/api/project/verify', '/api/project/validate', '/api/project/draft', '/api/agent/sessions'])
def test_viewers_cannot_execute_compile_or_start_an_agent(setup, path):
    c = setup.client('viewer-demo')
    response = c.post(path, json={})
    assert response.status_code == 403
    assert len(setup.project.store.list_runs(setup.project.pid)) == 1
    assert not agent_api._sessions


@pytest.mark.parametrize('suffix,method,body', [('baseline','post',None), ('cancel','post',None), ('label','patch',{'label':'denied'}), ('','delete',{'confirm_run_id':'no'}), ('restore','post',{})])
def test_viewers_cannot_mutate_run_resources(setup, suffix, method, body):
    c = setup.client('viewer-demo')
    r = c.request(method.upper(), '/api/runs/' + setup.run + ('/' + suffix if suffix else ''), **({'json':body} if body is not None else {}))
    assert r.status_code == 404


def test_run_spec_execution_report_diff_and_configured_target_deny_outsiders(setup):
    c = setup.client('outsider-demo')
    detail = setup.project.store.get_run(setup.run)
    execution = detail['results'][0]['id']
    paths = ['/api/runs/' + setup.run + suffix for suffix in ('', '/report', '/triage', '/report.html', '/export', '/progress', '/management')]
    paths += ['/api/executions/' + execution + suffix for suffix in ('/trace', '/repeats', '/repeats/diff?left=1&right=2')]
    paths += ['/api/specs/' + detail['spec_id'] + suffix for suffix in ('', '/source')]
    paths += ['/api/diff?candidate=' + setup.run, '/api/specs/diff?baseline=' + detail['spec_id'] + '&candidate=' + detail['spec_id'], '/api/project', '/api/project/spec', '/api/project/suggestions', '/api/agent/logs']
    for path in paths:
        r = c.get(path)
        assert r.status_code in (403, 404), (path, r.text)
        assert setup.project.pid not in r.text


def test_admin_grants_revokes_and_validates_project_memberships(setup):
    admin = setup.client('admin-demo')
    outsider = setup.client('outsider-demo')
    viewer = setup.client('viewer-demo')
    body = {'user_id':setup.ids['outsider-demo'], 'project_id':setup.project.pid, 'role':'viewer'}
    assert viewer.get('/api/accounts/users').status_code == 403
    assert viewer.put('/api/accounts/memberships', json=body).status_code == 403
    assert admin.put('/api/accounts/memberships', json=body).status_code == 200
    assert outsider.get('/api/runs/' + setup.run).status_code == 200
    assert admin.delete('/api/accounts/memberships/' + setup.ids['outsider-demo'] + '?project_id=' + setup.project.pid).status_code == 200
    assert outsider.get('/api/runs/' + setup.run).status_code == 404
    assert admin.put('/api/accounts/memberships', json={**body, 'project_id':'missing'}).status_code == 404
    assert admin.put('/api/accounts/memberships', json={**body, 'role':'admin'}).status_code == 422


def test_account_baseline_audit_records_actual_username(setup):
    c = setup.client()
    assert c.post('/api/runs/' + setup.run + '/baseline').status_code == 200
    record = setup.project.store.get_baseline_history(setup.project.pid)['items'][0]
    assert record['actor'] == 'editor-demo' and record['identity'] == 'account_user'
    assert PASSWORD not in str(record)


def test_agent_sessions_logs_and_streams_are_private_even_to_other_editors(setup):
    setup.repository.grant(setup.ids['outsider-demo'], setup.project.pid, 'editor')
    owner, other, admin = setup.client(), setup.client('outsider-demo'), setup.client('admin-demo')
    response = owner.post('/api/agent/sessions', json={})
    assert response.status_code == 200, response.text
    sid = response.json()['session_id']
    log = owner.get('/api/agent/logs').json()['logs'][0]['id']
    for caller in (other, admin):
        assert caller.get('/api/agent/logs').json()['logs'] == []
        assert caller.get('/api/agent/logs/' + log).status_code == 404
        for suffix, body in [('messages', {'text':'private'}), ('messages/stream', {'text':'private'}), ('approve', {'action_id':'none','approve':True}), ('approve/stream', {'action_id':'none','approve':True})]:
            assert caller.post('/api/agent/sessions/' + sid + '/' + suffix, json=body).status_code == 404
    assert owner.get('/api/agent/logs/' + log).status_code == 200
    assert owner.post('/api/auth/logout').status_code == 200
    relog = setup.client()
    assert relog.post('/api/agent/sessions/' + sid + '/messages', json={'text':'old'}).status_code == 404
    view = relog.get('/api/agent/logs/' + log).json()
    assert view['session']['active_session_id'] is None and view['pending'] == []


def test_revoked_editor_cannot_continue_existing_agent(setup):
    c = setup.client()
    sid = c.post('/api/agent/sessions', json={}).json()['session_id']
    setup.repository.revoke(setup.ids['editor-demo'], setup.project.pid)
    assert c.post('/api/agent/sessions/' + sid + '/messages', json={'text':'run'}).status_code == 403
    assert c.get('/api/agent/logs').status_code == 403


def test_principal_context_does_not_leak_between_concurrent_users(setup):
    a, b = setup.client(), setup.client('viewer-demo')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda c:c.get('/api/auth/me').json()['username'], [a,b] * 5))
    assert results == ['editor-demo', 'viewer-demo'] * 5
    assert principal_context.get() is None


def test_existing_database_upgrade_preserves_runs_and_specs(setup):
    before = setup.project.store.get_run(setup.run)
    reopened = Store(setup.project.db)
    Accounts(reopened)
    assert reopened.get_run(setup.run) == before
    assert reopened.get_spec(before['spec_id']) == setup.project.store.get_spec(before['spec_id'])


def test_invalid_mode_fails_closed_even_with_a_correct_shared_token(setup, monkeypatch):
    monkeypatch.setenv('SPECAGENT_AUTH_MODE', 'misspelled')
    c = TestClient(main.app, headers={'Authorization':'Bearer ignored-shared-fixture-token'})
    assert c.get('/api/projects').status_code == 503
    assert c.get('/api/health').json()['auth_required'] is True


def test_local_provisioning_cli_prompts_without_password_in_argv(setup, monkeypatch, capsys):
    monkeypatch.setenv('SPECAGENT_DB', setup.project.db)
    monkeypatch.setattr(module.getpass, 'getpass', lambda _:PASSWORD)
    assert module.main(['create-user', 'operator-fixture']) == 0
    assert PASSWORD not in capsys.readouterr().out
    assert module.main(['grant','operator-fixture','--project',setup.project.pid,'--role','editor']) == 0
    user = next(u for u in setup.repository.users() if u['username'] == 'operator-fixture')
    token = setup.repository.login(user['username'], PASSWORD, 'provision-peer')
    assert setup.repository.role(setup.repository.authenticate(token), setup.project.pid) == 'editor'


@pytest.mark.parametrize('operation,body', [('runs', {'baseline':'foreign-run'}), ('verify', {'pre_run_id':'foreign-run'})])
def test_cross_project_execution_references_are_rejected_before_target_load(setup, monkeypatch, operation, body):
    original = setup.project.store.get_run
    monkeypatch.setattr(setup.project.store, 'get_run', lambda rid: {'project_id':'other-private'} if rid == 'foreign-run' else original(rid))
    monkeypatch.setattr(main, '_load_server_project', lambda:pytest.fail('unauthorized request loaded target'))
    response = setup.client().post('/api/project/' + operation, json=body)
    assert response.status_code == 404 and 'foreign-run' not in response.text


@pytest.mark.parametrize('key', ['run_id','candidate','baseline','pre_run_id','pre_fix_run_id'])
def test_account_agent_tool_registry_rejects_cross_project_references(setup, monkeypatch, key):
    from app.agent.tools import ToolRegistry
    from app.web_tools import AccountToolRegistry
    original = setup.project.store.get_run
    monkeypatch.setattr(setup.project.store, 'get_run', lambda rid:{'project_id':'other-private'} if rid == 'foreign-run' else original(rid))
    called=[]
    monkeypatch.setattr(ToolRegistry, 'call', lambda *_args:called.append(True))
    ctx=SimpleNamespace(project=SimpleNamespace(store=setup.project.store, project_id=setup.project.pid), transcript=None)
    registry=AccountToolRegistry()
    result=registry.call(ctx, 'set_baseline', {key:'foreign-run'}, 'private-call')
    assert result.payload == {'ok':False, 'error':'run_not_found'} and not called
    registry.call(ctx, 'set_baseline', {key:setup.run}, 'own-call')
    assert called == [True]


def test_agent_quota_cannot_evict_another_accounts_session(setup):
    owner=setup.client()
    ids=[owner.post('/api/agent/sessions', json={}).json()['session_id'] for _ in range(agent_api.MAX_SESSIONS)]
    setup.repository.grant(setup.ids['outsider-demo'], setup.project.pid, 'editor')
    other=setup.client('outsider-demo')
    assert other.post('/api/agent/sessions', json={}).status_code == 429
    assert set(agent_api._sessions) == set(ids)
    assert owner.post('/api/agent/sessions', json={}).status_code == 200
    assert ids[0] not in agent_api._sessions and len(agent_api._sessions) == agent_api.MAX_SESSIONS


def test_forged_cookie_and_cross_site_header_fail_closed(setup):
    c=TestClient(main.app, base_url='https://testserver')
    c.cookies.set(COOKIE, 'a' * 43)
    assert c.get('/api/projects').status_code == 401
    owner=setup.client()
    assert owner.post('/api/auth/logout', headers={'Sec-Fetch-Site':'cross-site'}).status_code == 403
    assert owner.get('/api/auth/me').status_code == 200


def test_unclassified_future_route_is_denied_to_admins_too(setup):
    import asyncio
    from app.web_access import authorize
    token=setup.repository.login('admin-demo', PASSWORD, 'future-route-peer')
    principal=setup.repository.authenticate(token)
    request=SimpleNamespace(scope={'route':SimpleNamespace(path='/api/future-private-data')}, method='GET')
    with pytest.raises(HTTPException) as error:
        asyncio.run(authorize(request, setup.repository, principal))
    assert error.value.status_code == 403


def test_stream_approval_audits_the_session_owner_in_worker_thread(setup, monkeypatch):
    c=setup.client()
    sid=c.post('/api/agent/sessions', json={}).json()['session_id']
    agent_api._sessions[sid].offline=SimpleNamespace(pending_action=SimpleNamespace(action_id='audit-fixture'))
    def approve(_session, _request):
        assert principal_context.get() is None  # The stream starts its own thread.
        setup.project.store.set_baseline(setup.run)
        return {'ok':True}
    monkeypatch.setattr(agent_api, '_approve_with_identity', approve)
    response=c.post('/api/agent/sessions/'+sid+'/approve/stream', json={'action_id':'audit-fixture','approve':True})
    assert response.status_code == 200 and 'event: result' in response.text
    event=setup.project.store.get_baseline_history(setup.project.pid)['items'][0]
    assert (event['actor'],event['source'],event['identity']) == ('editor-demo','agent','account_user')


def test_legacy_unowned_transcripts_are_admin_only(setup):
    from app.agent.loop import Transcript
    log=Transcript(setup.project.root)
    log.emit('dashboard_session', {'project':setup.project.pid, 'session_id':'legacy-fixture'})
    viewer=setup.client('viewer-demo')
    assert viewer.get('/api/agent/logs').json()['logs'] == []
    admin=setup.client('admin-demo')
    logs=admin.get('/api/agent/logs').json()['logs']
    assert len(logs) == 1
    assert admin.get('/api/agent/logs/'+logs[0]['id']).status_code == 200
    assert viewer.get('/api/agent/logs/'+logs[0]['id']).status_code == 404
