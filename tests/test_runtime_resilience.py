"""Real subprocess regressions for timeouts, imports, locks and durable approvals."""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app import agent_api, main
from app.adapters import resolve_adapter
from app.adapters.python_adapter import PythonAdapter
from app.adapters.base import ExecutionContext
from app.config import AdapterConfig, GateConfig
from app.models import DiffEntry, DiffSummary, TestCase as Case
from app.project import release_run, try_acquire_run
from app.regression import gate_violations
from test_agent_api import _authed, _offline, _project, _session, _say, _approve, _fake, _outputs
from test_agent_loop import _call, _message

CASE = Case(id='isolation', rule_id='R', category='normal', user_input='hello')


def child(code, *, config=None):
    env = dict(os.environ, SPECAGENT_SKIP_DOTENV='1')
    if config:
        env['SPECAGENT_PROJECT_CONFIG'] = str(config)
    return subprocess.Popen([sys.executable, '-c', code], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def outcome(process):
    stdout, stderr = process.communicate(timeout=30)
    assert process.returncode == 0, stderr.decode('utf-8', errors='replace')
    return json.loads(stdout)


@pytest.mark.parametrize('asynchronous', [False, True])
def test_python_dead_loop_terminates_within_deadline(asynchronous):
    def hang(message):
        while True:
            pass
    async def async_hang(message):
        while True:
            pass
    adapter = PythonAdapter(async_hang if asynchronous else hang, 'injected:hang')
    processes = []
    original = adapter._start
    adapter._start = lambda: processes.append(original()) or processes[-1]
    start = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(adapter.execute(CASE, ExecutionContext(CASE.id, timeout_seconds=1)))
    assert time.monotonic() - start < 4
    assert processes[0].poll() is not None


def test_cancel_reaps_worker(tmp_path):
    marker = tmp_path / 'started'
    def hang(message):
        marker.write_text('ready')
        time.sleep(60)
    adapter = PythonAdapter(hang, 'injected:hang')
    processes = []
    original = adapter._start
    adapter._start = lambda: processes.append(original()) or processes[-1]
    async def run():
        task = asyncio.create_task(adapter.execute(CASE, ExecutionContext(CASE.id, timeout_seconds=30)))
        for _ in range(100):
            if marker.exists():
                break
            await asyncio.sleep(.05)
        assert marker.exists()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert processes[0].poll() is not None


def test_imports_and_lazy_siblings_never_cross_projects(tmp_path):
    adapters = []
    for name in ('a', 'b'):
        root = tmp_path / name
        root.mkdir()
        (root / 'utils.py').write_text(f'VALUE = "{name}"\n')
        (root / 'agent.py').write_text('def run_agent(message):\n import utils\n return {"response":utils.VALUE}\n')
        adapters.append(resolve_adapter(AdapterConfig(type='python', agent='agent:run_agent'), base_dir=str(root)))
    async def run():
        return await asyncio.gather(*(a.execute(CASE, ExecutionContext(CASE.id)) for a in adapters))
    assert [e.response for e in asyncio.run(run())] == ['a', 'b']
    source = tmp_path / 'a' / 'utils.py'
    old = source.stat()
    source.write_text('VALUE = "c"\n')  # Same size and timestamp; bytecode must not mask it.
    os.utime(source, ns=(old.st_atime_ns, old.st_mtime_ns))
    assert asyncio.run(adapters[0].execute(CASE, ExecutionContext(CASE.id))).response == 'c'


def test_worker_prints_and_exceptions_are_safe_protocol_data():
    def noisy(message):
        print('output is not a pipe message')
        return {'response': 'ok'}
    assert asyncio.run(PythonAdapter(noisy, 'injected:f').execute(CASE, ExecutionContext(CASE.id))).response == 'ok'
    def broken(message):
        raise ValueError('bad input')
    with pytest.raises(ValueError, match='bad input'):
        asyncio.run(PythonAdapter(broken, 'injected:f').execute(CASE, ExecutionContext(CASE.id)))


def test_timeout_kills_descendant_process(tmp_path):
    heartbeat = tmp_path / 'heartbeat'
    def spawn(message):
        code = ('from pathlib import Path; import time\n'
                f'p=Path({str(heartbeat)!r})\n'
                'while True:\n p.write_text(str(time.time_ns())); time.sleep(.02)\n')
        subprocess.Popen([sys.executable, '-c', code])
        time.sleep(60)
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(PythonAdapter(spawn, 'injected:spawn').execute(CASE, ExecutionContext(CASE.id, timeout_seconds=2)))
    assert heartbeat.exists()
    before = heartbeat.read_bytes()
    time.sleep(.2)
    assert heartbeat.read_bytes() == before


@pytest.mark.parametrize('configured', [False, True])
def test_project_lock_contends_across_processes_and_recovers_after_exit(tmp_path, monkeypatch, configured):
    if configured:
        monkeypatch.setenv('SPECAGENT_LOCK_DIR', str(tmp_path / 'shared-locks'))
    config = tmp_path / 'specagent.yaml'
    lock = try_acquire_run(config)
    code = f'from app.project import try_acquire_run,release_run; l=try_acquire_run({str(config)!r}); print("true" if l is None else "false"); release_run(l) if l else None'
    try:
        assert outcome(child(code)) is True
    finally:
        release_run(lock)
    assert outcome(child(code)) is False
    marker = tmp_path / 'locked'
    process = child(f'from app.project import try_acquire_run; from pathlib import Path; import time; l=try_acquire_run({str(config)!r}); Path({str(marker)!r}).write_text("ready"); time.sleep(60)')
    try:
        for _ in range(100):
            if marker.exists():
                break
            time.sleep(.05)
        assert marker.exists() and try_acquire_run(config) is None
    finally:
        process.kill()
        process.communicate(timeout=10)
    recovered = try_acquire_run(config)
    assert recovered is not None
    release_run(recovered)
    assert not (tmp_path / '.specagent').exists()


@pytest.fixture
def session_reset():
    agent_api.reset_sessions()
    yield
    agent_api.reset_sessions()


def test_offline_approval_survives_real_process_restart_and_cannot_repeat(tmp_path, monkeypatch, session_reset):
    pid = _project(tmp_path, monkeypatch)
    _offline(monkeypatch)
    client = _authed(monkeypatch)
    sid = _session(client)['session_id']
    pending = _say(client, sid).json()['pending'][0]
    log = agent_api._sessions[sid].transcript.path
    before = [json.loads(line)['seq'] for line in log.read_text(encoding='utf-8').splitlines()]
    code = f'''import json
from app import agent_api,main
from fastapi.testclient import TestClient
agent_api.client_factory=lambda:None
c=TestClient(main.app,headers={{'Authorization':'Bearer '+__import__('os').environ['SPECAGENT_API_TOKEN']}})
r=c.post('/api/agent/sessions/{sid}/approve',json={{'action_id':{pending['action_id']!r},'approve':True}})
print(json.dumps({{'status':r.status_code,'body':r.json()}}))'''
    restored = outcome(child(code))
    assert restored['status'] == 200 and restored['body']['action']['ok'] is True
    assert len(main.store.list_runs(pid)) == 1
    assert _approve(client, sid, pending['action_id']).status_code == 409
    assert len(main.store.list_runs(pid)) == 1
    seqs = [json.loads(line)['seq'] for line in log.read_text(encoding='utf-8').splitlines()]
    assert seqs[:len(before)] == before and len(set(seqs)) == len(seqs)


def test_two_workers_cannot_execute_one_approval_twice(tmp_path, monkeypatch, session_reset):
    pid = _project(tmp_path, monkeypatch)
    _offline(monkeypatch)
    client = _authed(monkeypatch)
    sid = _session(client)['session_id']
    action = _say(client, sid).json()['pending'][0]
    code = f'''import json,os
from app import agent_api,main
from fastapi.testclient import TestClient
agent_api.client_factory=lambda:None
c=TestClient(main.app,headers={{'Authorization':'Bearer '+os.environ['SPECAGENT_API_TOKEN']}})
r=c.post('/api/agent/sessions/{sid}/approve',json={{'action_id':{action['action_id']!r},'approve':True}})
print(json.dumps(r.status_code))'''
    workers = [child(code), child(code)]
    assert sorted(outcome(worker) for worker in workers) == [200, 409]
    assert len(main.store.list_runs(pid)) == 1


def test_busy_session_in_another_process_blocks_stream_and_ttl_eviction(tmp_path, monkeypatch, session_reset):
    _project(tmp_path, monkeypatch)
    _offline(monkeypatch)
    client = _authed(monkeypatch)
    sid = _session(client)['session_id']
    marker = tmp_path / 'busy'
    worker = child(f'from app.agent_api import _session_lock; from pathlib import Path; import time; lock=_session_lock({sid!r}); lock.acquire(); Path({str(marker)!r}).write_text("ready"); time.sleep(60)')
    try:
        for _ in range(100):
            if marker.exists():
                break
            time.sleep(.05)
        assert marker.exists()
        monkeypatch.setattr(agent_api, '_clock', lambda: time.time() + agent_api.SESSION_TTL_SECONDS + 1)
        r = client.post(f'/api/agent/sessions/{sid}/messages/stream', json={'text': 'run'})
        assert r.status_code == 409 and r.json()['detail'] == 'session_busy'
        assert agent_api._checkpoints.load(sid) is not None
    finally:
        worker.kill()
        worker.communicate(timeout=10)


def test_llm_conversation_and_parked_calls_restore_without_replay(tmp_path, monkeypatch, session_reset):
    _project(tmp_path, monkeypatch)
    _fake(monkeypatch, [[_call('run_suite', {}, 'run'), _call('inspect_project', {}, 'skipped')]])
    client = _authed(monkeypatch)
    sid = _session(client)['session_id']
    action = _say(client, sid, 'please run').json()['pending'][0]
    agent_api.reset_sessions(clear_persisted=False)
    fake = _fake(monkeypatch, [[_message('restored')]])
    result = _approve(client, sid, action['action_id']).json()
    assert result['action']['ok'] is True
    assert _outputs(fake.inputs[0])['skipped']['error'] == 'skipped_pending_approval'
    assert any(m.get('role') == 'user' for m in fake.inputs[0])


def test_sdk_message_object_is_json_in_checkpoint(tmp_path, monkeypatch, session_reset):
    from types import SimpleNamespace
    _project(tmp_path, monkeypatch)
    _fake(monkeypatch, [])
    client = _authed(monkeypatch)
    sid = _session(client)['session_id']
    session = agent_api._sessions[sid]
    session.agent.messages.append(SimpleNamespace(type='message', role='assistant', content=[
        SimpleNamespace(type='output_text', text='scripted browser test <img src=x>')]))
    with session.lock:
        agent_api._save(session)
    agent_api.reset_sessions(clear_persisted=False)
    restored = agent_api._get_session(sid)
    assert restored.agent.messages[-1]['content'][0]['text'] == 'scripted browser test <img src=x>'


def test_inflight_approval_fails_closed_after_restart(tmp_path, monkeypatch, session_reset):
    pid = _project(tmp_path, monkeypatch)
    _offline(monkeypatch)
    client = _authed(monkeypatch)
    sid = _session(client)['session_id']
    action = _say(client, sid).json()['pending'][0]
    session = agent_api._sessions[sid]
    with session.lock:
        agent_api._begin_operation(session, 'approval', action['action_id'])
    agent_api.reset_sessions(clear_persisted=False)
    result = _approve(client, sid, action['action_id'])
    assert result.status_code == 409 and 'session_operation_uncertain' in result.json()['detail']
    assert len(main.store.list_runs(pid)) == 0
    log = agent_api._sessions[sid].transcript.session_id
    history = client.get('/api/agent/logs/' + log).json()
    assert history['session']['interrupted'] and history['session']['active_session_id'] is None
    assert history['pending'] == []


def test_restart_history_is_read_only_and_spec_binding_is_rechecked(tmp_path, monkeypatch, session_reset):
    pid = _project(tmp_path, monkeypatch)
    _offline(monkeypatch)
    client = _authed(monkeypatch)
    sid = _session(client)['session_id']
    action = _say(client, sid).json()['pending'][0]
    log_id = agent_api._sessions[sid].transcript.session_id
    agent_api.reset_sessions(clear_persisted=False)
    monkeypatch.setattr(agent_api, 'client_factory', lambda: pytest.fail('reading history must not create an LLM client'))
    history = client.get('/api/agent/logs/' + log_id).json()
    assert history['session']['active_session_id'] == sid
    assert history['pending'][0]['action_id'] == action['action_id']
    source = tmp_path / 'specs' / 'behavior.yaml'
    source.write_text(source.read_text(encoding='utf-8').replace('title: t', 'title: changed'), encoding='utf-8')
    result = _approve(client, sid, action['action_id'])
    assert result.status_code == 200 and not result.json()['action']['ok']
    assert len(main.store.list_runs(pid)) == 0


def test_changed_config_cannot_restore_a_pending_approval(tmp_path, monkeypatch, session_reset):
    _project(tmp_path, monkeypatch)
    _offline(monkeypatch)
    client = _authed(monkeypatch)
    sid = _session(client)['session_id']
    action = _say(client, sid).json()['pending'][0]
    agent_api.reset_sessions(clear_persisted=False)
    config = tmp_path / 'specagent.yaml'
    config.write_text(config.read_text(encoding='utf-8') + '\nagent:\n  allow_source: true\n', encoding='utf-8')
    assert _approve(client, sid, action['action_id']).status_code == 409


def test_checkpoint_redacts_credentials_and_invalidates_changed_approval(tmp_path, monkeypatch, session_reset):
    _project(tmp_path, monkeypatch)
    _offline(monkeypatch)
    client = _authed(monkeypatch)
    sid = _session(client)['session_id']
    action = _say(client, sid).json()['pending'][0]
    session = agent_api._sessions[sid]
    secret = 'private-memory-credential-123456789'
    monkeypatch.setenv('PRIVATE_TEST_API_KEY', secret)
    session.ctx.pending[action['action_id']].args['note'] = secret
    with session.lock:
        agent_api._save(session)
    state = agent_api._checkpoints.load(sid)
    assert secret not in json.dumps(state) and state['redacted_actions']
    agent_api.reset_sessions(clear_persisted=False)
    assert _approve(client, sid, action['action_id']).status_code == 409


def test_crash_record_does_not_swallow_next_transcript_event(tmp_path):
    from app.agent.loop import Transcript
    log = Transcript(tmp_path)
    with log.path.open('ab') as file:
        file.write(b'{"broken":"\xe4')
    restored = Transcript.resume(tmp_path, log.session_id)
    restored.emit('restored', {'text': 'ok'})
    records = []
    for line in log.path.read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            pass
    assert [r['type'] for r in records] == ['session_start', 'restored']
    assert [r['seq'] for r in records] == [1, 2]


def test_workers_can_initialize_checkpoint_schema_together(tmp_path):
    from app.storage import Store
    database = tmp_path / 'cold-start.db'
    Store(str(database))  # Existing project DB, before the checkpoint migration.
    start = tmp_path / 'go'
    workers = []
    for index in range(4):
        ready = tmp_path / f'ready-{index}'
        workers.append(child(f'''from app.storage import Store
from app.agent_state import Checkpoints
from pathlib import Path
import time,json
store=Store({str(database)!r})
Path({str(ready)!r}).write_text('ready')
while not Path({str(start)!r}).exists(): time.sleep(.01)
Checkpoints(store)
print(json.dumps(True))'''))
    try:
        for _ in range(300):
            if all((tmp_path / f'ready-{index}').exists() for index in range(4)):
                break
            time.sleep(.1)
        assert all((tmp_path / f'ready-{index}').exists() for index in range(4))
        start.write_text('go')
        assert all(outcome(worker) is True for worker in workers)
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
                worker.communicate(timeout=10)


@pytest.mark.parametrize('status', ['ERROR', 'FLAKY'])
@pytest.mark.parametrize('severity', ['critical', 'high', 'medium', 'low'])
def test_quality_gate_blocks_every_severity_even_without_baseline(status, severity):
    entry = DiffEntry(test_case_id='x', diff_type='NEW_TEST', severity=severity, candidate_status=status)
    diff = DiffSummary(candidate_run_id='run', entries=[entry])
    assert gate_violations(diff) == [entry]
    assert gate_violations(diff, [], block_errors=False, block_flaky=False) == []
    assert GateConfig().block_errors and GateConfig().block_flaky


@pytest.mark.parametrize('status', ['ERROR', 'FLAKY'])
def test_cli_web_and_action_quality_gate_parity(tmp_path, monkeypatch, capsys, status):
    from app import project as module, ci
    from app.models import TestResult, AgentExecution
    from cli.specagent import main as cli
    pid = _project(tmp_path, monkeypatch)
    source = tmp_path / 'specs' / 'behavior.yaml'
    source.write_text(source.read_text(encoding='utf-8').replace('severity: critical', 'severity: low'), encoding='utf-8')
    async def results(spec, tests, **kwargs):
        return [TestResult(test=t, passed=False, status=status, execution=AgentExecution(response=''),
                           violations=['test quality failure']) for t in tests]
    monkeypatch.setattr(module.orchestrator, 'execute_suite', results)
    config = tmp_path / 'specagent.yaml'
    args = ['run', '--config', str(config), '--db', main.store.db_url, '--json', '--set-baseline']
    assert cli(args) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload['diff'] is None and payload['gate']['violations']
    assert not payload['run']['is_baseline'] and main.store.get_baseline(pid) is None
    assert 'Gate: FAILED' in ci.render_comment(payload)
    assert ('execution error' if status == 'ERROR' else 'flaky case') in ci.render_comment(payload)
    result_file = tmp_path / 'gate-result.json'
    result_file.write_text(json.dumps(payload), encoding='utf-8')
    assert ci.main(['quality-blocked', '--result', str(result_file)]) == 0
    assert capsys.readouterr().out.strip() == 'true'
    client = _authed(monkeypatch)
    web = client.post('/api/project/runs', json={'set_baseline': True}).json()
    assert web['gate']['failed'] and not web['run']['is_baseline']
    assert {v['candidate_status'] for v in web['gate']['violations']} == {status}
    assert 'Gate: FAILED' in web['summary']['gate_text']
    config.write_text(config.read_text(encoding='utf-8') + '\ngate:\n  block_errors: false\n  block_flaky: false\n', encoding='utf-8')
    assert cli(args) == 0
    relaxed = json.loads(capsys.readouterr().out)
    assert not relaxed['gate']['violations'] and relaxed['run']['is_baseline']


@pytest.mark.parametrize('status', ['ERROR', 'FLAKY'])
@pytest.mark.parametrize('baseline', [False, True])
def test_real_action_quality_fixture(tmp_path, capsys, status, baseline):
    import runpy
    from cli.specagent import main as cli
    create = runpy.run_path(str(Path(__file__).parent / 'fixtures' / 'create_quality_project.py'))['create']
    create(tmp_path, status)
    args = ['run', '--config', str(tmp_path / 'specagent.yaml'), '--db', str(tmp_path / 'quality.db'), '--json']
    if baseline:
        args.append('--set-baseline')
    assert cli(args) == 1
    payload = json.loads(capsys.readouterr().out)
    assert {e['candidate_status'] for e in payload['gate']['violations']} == {status}
    assert not payload['run']['is_baseline']
