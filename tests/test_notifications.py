"""Notification tests use only private data and fake transports."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import update

from app import main, notifications as module
from app.accounts import Accounts
from app.notifications import Delivery, Destination, Notifications
from test_cli_web_parity import project, post


@pytest.fixture
def notify(project, tmp_path, monkeypatch):
    run=post(project)['run']['id']
    cfg=tmp_path/'notifications.json'
    profiles=[{'id':'hook','name':'Build feed','kind':'webhook','url':'https://fixture.invalid/secret-route','secret_env':'NOTIFICATION_FIXTURE_AUTH'},
              {'id':'mail','name':'QA mail','kind':'email','host':'smtp.fixture.invalid','sender':'robot@fixture.invalid','recipients':['qa@fixture.invalid'],'username':'fixture-user','password_env':'NOTIFICATION_FIXTURE_MAIL'},
              {'id':'pr','name':'Review thread','kind':'pr_comment','repository':'fixture/example','pull_number':123,'token_env':'NOTIFICATION_FIXTURE_PR'}]
    cfg.write_text(json.dumps({'channels':profiles}), encoding='utf8')
    monkeypatch.setenv(module.CONFIG_ENV,str(cfg))
    for key in ('AUTH','MAIL','PR'):
        monkeypatch.setenv('NOTIFICATION_FIXTURE_'+key, 'fake-notification-'+key.lower()+'-credential')
    monkeypatch.setenv('SPECAGENT_API_TOKEN','fake-notification-shared-token')
    project.client.headers['Authorization']='Bearer fake-notification-shared-token'
    sent=[]
    adapter=module.deliver
    monkeypatch.setattr(module, 'deliver', lambda profile,payload:sent.append((profile.id,payload)))
    repository=Notifications(project.store)
    return SimpleNamespace(project=project,run=run,cfg=cfg,profiles=profiles,repository=repository,sent=sent,client=project.client,adapter=adapter)


def configure(n, enabled=True):
    response=n.client.post('/api/notifications/channels/hook', json={'enabled':enabled})
    assert response.status_code==200,response.text


def preview(n):
    response=n.client.post('/api/notifications/preview',json={'run_id':n.run,'channel_id':'hook'})
    assert response.status_code==200,response.text
    return response.json()


def send(n, item, **extra):
    return n.client.post('/api/notifications/send',json={'delivery_id':item['id'],'confirm_run_id':n.run,'confirm':True,**extra})


def test_defaults_closed_no_auto_delivery_and_no_private_destination_fields(notify):
    data=notify.client.get('/api/notifications/channels',params={'project_id':notify.project.pid}).json()
    assert data['state']=='loaded' and len(data['channels'])==3
    assert all(not p['enabled'] for p in data['channels']) and data['permissions']=={'configure':True,'send':True}
    text=json.dumps(data)
    for private in ('fixture.invalid','secret-route','fixture-user','fake-notification-auth-credential','NOTIFICATION_FIXTURE_AUTH'):
        assert private not in text
    assert notify.client.post('/api/notifications/preview',json={'run_id':notify.run,'channel_id':'hook'}).status_code==409
    configure(notify)
    assert notify.sent==[]


def test_preview_confirmation_and_persistent_history_are_bound_and_single_use(notify):
    configure(notify)
    before=notify.project.store.get_run(notify.run)
    item=preview(notify)
    assert item['state']=='prepared' and notify.sent==[]
    assert set(item['summary'])=={'id','project_id','status','passed','failed','errors','canceled','score','total'}
    assert send(notify,item,confirm=False).status_code==422
    assert send(notify,item,confirm_run_id='wrong').status_code==422
    response=send(notify,item)
    assert response.status_code==200 and response.json()['state']=='sent'
    assert len(notify.sent)==1 and notify.sent[0][1]==item['summary']
    assert send(notify,item).status_code==409 and len(notify.sent)==1
    history=Notifications(notify.project.store).history(notify.project.pid,0,20)
    assert history['total']==1 and history['items'][0]['state']=='sent'
    assert all(k not in str(history) for k in ('fake-notification','secret-route','binding','owner'))
    assert notify.project.store.get_run(notify.run)==before


@pytest.mark.parametrize('change',['disabled','re-enabled','credentials','destination','run','expired','removed'])
def test_stale_previews_never_dispatch(notify,monkeypatch,change):
    configure(notify)
    item=preview(notify)
    if change=='disabled':configure(notify,False)
    elif change=='re-enabled':configure(notify,False);configure(notify,True)
    elif change=='credentials':monkeypatch.setenv('NOTIFICATION_FIXTURE_AUTH','rotated-private-credential')
    elif change=='destination':
        notify.profiles[0]['url']='https://changed.fixture.invalid/new';notify.cfg.write_text(json.dumps({'channels':notify.profiles}),encoding='utf8')
    elif change=='removed':notify.cfg.write_text('{"channels":[]}',encoding='utf8')
    elif change=='expired':
        with notify.repository.session.begin() as db:db.execute(update(Delivery).values(expires=0))
    else:
        original=notify.project.store.get_run
        monkeypatch.setattr(notify.project.store,'get_run',lambda rid:{**original(rid),'passed':999})
    assert send(notify,item).status_code==409 and notify.sent==[]


def test_failure_records_no_exception_payload_or_automatic_retry(notify,monkeypatch):
    configure(notify);item=preview(notify)
    def fail(*_):raise RuntimeError('https://fixture.invalid/private fake-notification-auth-credential')
    monkeypatch.setattr(module,'deliver',fail)
    response=send(notify,item)
    assert response.status_code==200 and response.json()['state']=='failed'
    assert response.json()['error']=='delivery_failed_outcome_unknown'
    assert 'fixture.invalid' not in response.text and 'credential' not in response.text
    assert send(notify,item).status_code==409


def test_concurrent_confirmations_make_one_attempt(notify,monkeypatch):
    configure(notify);item=preview(notify)
    started=threading.Event();release=threading.Event();calls=[]
    def slow(*_):calls.append(True);started.set();assert release.wait(10)
    monkeypatch.setattr(module,'deliver',slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(send,notify,item);assert started.wait(10)
        second=pool.submit(send,notify,item)
        assert second.result(timeout=10).status_code==409
        release.set();assert first.result(timeout=10).status_code==200
    assert calls==[True]


@pytest.mark.parametrize('body',[{'enabled':'true'},{'enabled':True,'url':'https://client.invalid'},{'enabled':True,'password':'should-not-echo'}])
def test_browser_cannot_supply_destinations_credentials_or_coerce_confirmation(notify,body):
    response=notify.client.post('/api/notifications/channels/hook',json=body)
    assert response.status_code==422 and 'should-not-echo' not in response.text and 'client.invalid' not in response.text
    configure(notify);item=preview(notify)
    assert send(notify,item,confirm='true').status_code==422
    assert send(notify,item,url='https://client.invalid').status_code==422
    assert notify.sent==[]


def test_local_no_token_is_read_only_and_anonymous_token_failure(notify,monkeypatch):
    client=TestClient(main.app)
    assert client.get('/api/notifications/channels').status_code==401
    monkeypatch.setenv('SPECAGENT_API_TOKEN','')
    assert client.get('/api/notifications/channels').status_code==200
    assert client.post('/api/notifications/channels/hook',json={'enabled':True}).status_code==403
    assert client.post('/api/notifications/preview',json={'run_id':notify.run,'channel_id':'hook'}).status_code==403


def test_multiuser_roles_project_scope_csrf_and_preview_ownership(notify,monkeypatch):
    accounts=Accounts(notify.project.store)
    password='notification-account-fixture-password'
    ids={name:accounts.create_user(name,password,admin=name=='notify-admin') for name in ('notify-admin','notify-editor','notify-viewer','notify-other')}
    for name,role in [('notify-editor','editor'),('notify-viewer','viewer'),('notify-other','editor')]:accounts.grant(ids[name],notify.project.pid,role)
    monkeypatch.setenv('SPECAGENT_AUTH_MODE','multiuser')
    def client(name):
        c=TestClient(main.app,base_url='https://testserver');response=c.post('/api/auth/login',json={'username':name,'password':password});assert response.status_code==200
        c.headers['X-SpecAgent-CSRF']=response.json()['csrf'];return c
    admin,editor,viewer,other=map(client,['notify-admin','notify-editor','notify-viewer','notify-other'])
    assert editor.post('/api/notifications/channels/hook',json={'enabled':True}).status_code==403
    assert admin.post('/api/notifications/channels/hook',json={'enabled':True}).status_code==200
    assert viewer.post('/api/notifications/preview',json={'run_id':notify.run,'channel_id':'hook'}).status_code==404
    assert viewer.get('/api/notifications/channels',params={'project_id':notify.project.pid}).json()['permissions']=={'configure':False,'send':False}
    assert viewer.get('/api/notifications/history?project_id=foreign').status_code==403
    item=editor.post('/api/notifications/preview',json={'run_id':notify.run,'channel_id':'hook'}).json()
    body={'delivery_id':item['id'],'confirm_run_id':notify.run,'confirm':True}
    assert other.post('/api/notifications/send',json=body).status_code==404
    csrf=editor.headers.pop('X-SpecAgent-CSRF')
    assert editor.post('/api/notifications/send',json=body).status_code==403
    editor.headers['X-SpecAgent-CSRF']=csrf
    accounts.revoke(ids['notify-editor'],notify.project.pid)
    assert editor.post('/api/notifications/send',json=body).status_code==403 and notify.sent==[]
    accounts.grant(ids['notify-editor'],notify.project.pid,'editor')
    assert editor.post('/api/auth/logout').status_code==200
    fresh=client('notify-editor')
    assert fresh.post('/api/notifications/send',json=body).status_code==404
    assert viewer.get('/api/notifications/history',params={'project_id':notify.project.pid}).json()['items'][0]['actor']=='notify-editor'
    for malformed in (None, [], {'run_id':[]}, {'run_id':{'private':'never-echo'}}, {'run_id':1}, {'run_id':'x'*65}):
        response=fresh.post('/api/notifications/preview',content=json.dumps(malformed),headers={'Content-Type':'application/json'})
        assert response.status_code==422 and 'never-echo' not in response.text
    for malformed in (None, [], {'delivery_id':{}}, {'delivery_id':True}):
        assert fresh.post('/api/notifications/send',content=json.dumps(malformed),headers={'Content-Type':'application/json'}).status_code==422


def test_config_missing_invalid_duplicate_and_env_file_are_opaque(notify,monkeypatch):
    monkeypatch.setenv(module.CONFIG_ENV,'')
    assert notify.repository.channels()=={'state':'missing','channels':[]}
    monkeypatch.setenv(module.CONFIG_ENV,str(notify.cfg))
    for data in ('invalid '+str(notify.cfg),json.dumps({'channels':[notify.profiles[0],notify.profiles[0]]}),json.dumps({'channels':[{**notify.profiles[0],'url':'https://user:password@fixture.invalid'}]})):
        notify.cfg.write_text(data,encoding='utf8');assert notify.repository.channels()=={'state':'invalid','channels':[]}
    env_path=notify.cfg.with_name('.env')
    monkeypatch.setenv(module.CONFIG_ENV,str(env_path))
    original=module.Path.read_text
    monkeypatch.setattr(module.Path,'read_text',lambda path,*a,**kw:pytest.fail('env read') if path.name=='.env' else original(path,*a,**kw))
    assert notify.repository.channels()=={'state':'invalid','channels':[]}


def test_explicit_private_reference_and_summary_redaction_preserve_fields(notify):
    profile=Destination.model_validate({**notify.profiles[0],'name':'Build fake-notification-auth-credential https://fixture.invalid/secret-route'})
    assert 'credential' not in profile.public()['name'] and 'fixture.invalid' not in profile.public()['name']
    payload=profile.safe({'id':'id','project_id':'fake-notification-auth-credential'})
    assert payload=={'id':'id','project_id':'[redacted]'}


def test_webhook_hmac_and_pr_comment_use_only_server_destinations(notify,monkeypatch):
    captured=[];monkeypatch.setattr(module,'post_json',lambda *args:captured.append(args))
    payload={'id':'run-fixture','project_id':'<bad>@person`','passed':3}
    # Restore the actual adapter only for fake low-level transports.
    adapter=notify.adapter
    adapter(Destination.model_validate(notify.profiles[0]),payload)
    url,body,headers=captured[-1]
    import hashlib,hmac
    assert url==notify.profiles[0]['url'] and headers['X-SpecAgent-Signature']=='sha256='+hmac.new(b'fake-notification-auth-credential',body,hashlib.sha256).hexdigest()
    adapter(Destination.model_validate(notify.profiles[2]),payload)
    url,body,headers=captured[-1]
    assert url=='https://api.github.com/repos/fixture/example/issues/123/comments'
    assert headers['Authorization']=='Bearer fake-notification-pr-credential'
    comment=json.loads(body)['body'];assert '@person' not in comment and '<bad>' not in comment


@pytest.mark.parametrize('tls',['ssl','starttls'])
def test_mail_adapter_uses_verified_tls_without_logging_credentials(notify,monkeypatch,tls):
    calls=[]
    class SMTP:
        def __init__(self,*a,**kw):calls.append(('connect',a,kw))
        def __enter__(self):return self
        def __exit__(self,*_):return False
        def starttls(self,**kw):calls.append(('tls',kw))
        def login(self,*a):calls.append(('login',a))
        def send_message(self,message):calls.append(('message',message))
    monkeypatch.setattr(module.smtplib,'SMTP_SSL',SMTP);monkeypatch.setattr(module.smtplib,'SMTP',SMTP)
    adapter=notify.adapter
    adapter(Destination.model_validate({**notify.profiles[1],'tls':tls}),{'id':'run-fixture','passed':3})
    import ssl
    context=calls[0][2]['context'] if tls=='ssl' else next(call[1]['context'] for call in calls if call[0]=='tls')
    assert context.verify_mode==ssl.CERT_REQUIRED and context.check_hostname
    assert ('login',('fixture-user','fake-notification-mail-credential')) in calls
    message=next(call[1] for call in calls if call[0]=='message')
    assert message['To']=='qa@fixture.invalid' and 'credential' not in message.get_content()


def test_redirect_is_never_followed(notify):
    assert module.NoRedirect().redirect_request(None,None,302,'redirect',{},'https://other.invalid') is None


def test_pagination_and_existing_database_upgrade_preserve_evidence(notify):
    configure(notify);before=notify.project.store.get_run(notify.run)
    for _ in range(22):preview(notify)
    a=notify.repository.history(notify.project.pid,0,20);b=notify.repository.history(notify.project.pid,20,20)
    assert len(a['items'])==20 and a['has_more'] and len(b['items'])==2 and not b['has_more']
    assert len({item['id'] for item in a['items']+b['items']})==22
    Notifications(notify.project.store)
    assert notify.project.store.get_run(notify.run)==before


def test_missing_credentials_and_running_run_never_create_delivery(notify,monkeypatch):
    monkeypatch.delenv('NOTIFICATION_FIXTURE_AUTH')
    assert not notify.repository.channels()['channels'][0]['ready']
    assert notify.client.post('/api/notifications/channels/hook',json={'enabled':True}).status_code==409
    monkeypatch.setenv('NOTIFICATION_FIXTURE_AUTH','fake-restored-credential');configure(notify)
    original=notify.project.store.get_run
    monkeypatch.setattr(notify.project.store,'get_run',lambda rid:{**original(rid),'status':'running'})
    assert notify.client.post('/api/notifications/preview',json={'run_id':notify.run,'channel_id':'hook'}).status_code==409
    assert notify.repository.history(notify.project.pid,0,20)['total']==0 and notify.sent==[]


@pytest.mark.parametrize('status',[200,204,302,400,500])
def test_http_delivery_requires_success_and_never_reads_provider_body(monkeypatch,status):
    calls=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*_):return False
        def read(self):pytest.fail('provider body must not be read')
    response=Response();response.status=status
    class Opener:
        def open(self,request,timeout):calls.append((request,timeout));return response
    monkeypatch.setattr(module,'build_opener',lambda policy:Opener())
    if status<300:module.post_json('https://fixture.invalid',b'{}',{})
    else:
        with pytest.raises(RuntimeError,match='delivery_rejected'):module.post_json('https://fixture.invalid',b'{}',{})
    assert calls[0][0].get_method()=='POST' and calls[0][1]==10


def test_partial_smtp_refusal_is_not_reported_as_success(notify,monkeypatch):
    class SMTP:
        def __init__(self,*a,**kw):pass
        def __enter__(self):return self
        def __exit__(self,*_):return False
        def login(self,*_):pass
        def send_message(self,message):return {'qa@fixture.invalid':(550,b'private rejection')}
    monkeypatch.setattr(module.smtplib,'SMTP_SSL',SMTP)
    with pytest.raises(RuntimeError,match='mail_recipients_rejected'):
        notify.adapter(Destination.model_validate(notify.profiles[1]),{'id':'run-fixture'})
