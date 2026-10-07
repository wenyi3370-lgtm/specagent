"""Read-only CLI/API proposal parity and real local apply/verify on private copies."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.agent.sandbox import ProjectSandbox
from app.agent.tools import ToolContext, ToolRegistry
from app.project import Project
from app.suggestions import load_suggestion, save_verification, suggestion_view
from cli import specagent as cli
from test_agent_tools import _approve
from test_cli_web_parity import project, post


def tree_hash(root):
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if not path.is_file() or any(part in ('.specagent', '__pycache__', '.git') for part in path.parts):
            continue
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


@pytest.fixture
def proposal(project):
    project.switch('broken')
    pre = post(project)['run']['id']
    core = Project.load(str(project.cfg), store=project.store)
    target = core.agent_path.split(':')[0] + '.py'
    fixed = next(project.root.glob('parity_fixed_*.py')).read_text(encoding='utf-8')
    registry = ToolRegistry()
    ctx = ToolContext(project=core, sandbox=ProjectSandbox(project.root, True), allow_source=True, confirm=_approve)
    assert registry.call(ctx, 'read_file', {'path': target}, 'r').payload['ok']
    before = tree_hash(project.root)
    result = registry.call(ctx, 'write_fix_suggestion', {
        'rule_id': 'ACCOUNT_SCOPE', 'diagnosis': 'Stop trusting the message account. <img src=x>',
        'pre_fix_run_id': pre, 'changes': [{'path': target, 'new_content': fixed}],
    }, 'w').payload
    assert result['ok'], result
    assert tree_hash(project.root) == before
    return project, result['id'], target, fixed, pre, before


def command(capsys, p, verb, *args):
    capsys.readouterr()
    code = cli.main([verb, *args, '--config', str(p.cfg), '--db', p.db])
    return code, capsys.readouterr().out


def test_list_show_cli_json_matches_web_and_is_readonly(proposal, capsys):
    p, sid, target, _, pre, before = proposal
    for args, path in ((['list'], '/api/project/suggestions'), (['show', sid], '/api/project/suggestions/'+sid)):
        code, output = command(capsys, p, 'suggestions', *args, '--json')
        web = p.client.get(path)
        assert code == 0 and web.status_code == 200
        assert json.loads(output) == web.json()
    detail = p.client.get('/api/project/suggestions/'+sid).json()
    assert detail['files'][0]['path'] == target and detail['pre_fix_run_id'] == pre
    assert detail['latest_verdict'] is None and detail['verification_history'] == []
    assert detail['apply_command'] == f'git apply .specagent/suggestions/{sid}/fix.diff'
    code, output = command(capsys, p, 'suggestions', 'show', sid)
    assert code == 0 and detail['apply_command'] in output
    assert str(p.root) not in detail['apply_command']
    assert tree_hash(p.root) == before


def test_diff_download_and_cli_stdout_are_original_bytes(proposal):
    p, sid, _, _, _, before = proposal
    raw = (p.root/'.specagent'/'suggestions'/sid/'fix.diff').read_bytes()
    response = p.client.get(f'/api/project/suggestions/{sid}/fix.diff')
    assert response.status_code == 200 and response.content == raw
    assert response.headers['content-type'] == 'text/plain; charset=utf-8'
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert 'attachment' in response.headers['content-disposition']
    env = dict(os.environ, SPECAGENT_SKIP_DOTENV='1', OPENAI_API_KEY='')
    child = subprocess.run([sys.executable, '-m', 'cli.specagent', 'suggestions', 'show', sid, '--diff',
                            '--config', str(p.cfg), '--db', p.db], capture_output=True, env=env,
                           cwd=main.BASE.parent, timeout=30)
    assert child.returncode == 0, child.stderr
    assert child.stdout == raw
    assert tree_hash(p.root) == before


def test_raw_crlf_and_utf8_are_preserved(proposal):
    p, sid, _, _, _, _ = proposal
    raw = '--- a/x.py\r\n+++ b/x.py\r\n@@ -1 +1 @@\r\n-旧值\r\n+新值\r\n'.encode('utf-8')
    (p.root/'.specagent'/'suggestions'/sid/'fix.diff').write_bytes(raw)
    response = p.client.get(f'/api/project/suggestions/{sid}/fix.diff')
    assert response.content == raw
    assert p.client.get(f'/api/project/suggestions/{sid}').json()['diff'].encode('utf-8') == raw


def test_real_git_apply_then_web_verify_and_history(proposal, capsys):
    p, sid, _, _, pre, _ = proposal
    assert shutil.which('git'), 'git is required, not skipped'
    relative = f'.specagent/suggestions/{sid}/fix.diff'
    subprocess.run(['git', 'init', '-q'], cwd=p.root, check=True, capture_output=True)
    for args in (['--check'], []):
        child = subprocess.run(['git', '-c', 'core.autocrlf=false', 'apply', *args, relative], cwd=p.root, capture_output=True)
        assert child.returncode == 0, child.stderr
    result = post(p, 'verify', suggestion=sid)
    assert result['verdict'] == 'ALL_FIXED' and result['pre_run_id'] == pre
    detail = p.client.get(f'/api/project/suggestions/{sid}').json()
    assert detail['latest_verdict'] == 'ALL_FIXED'
    assert detail['verification_history'][-1]['quote'] == result['quote']
    record = p.root/'.specagent'/'suggestions'/sid/detail['verification_history'][-1]['record']
    assert json.loads(record.read_text(encoding='utf-8')) == result
    code, output = command(capsys, p, 'suggestions', 'show', sid, '--json')
    assert code == 0 and json.loads(output) == detail


def test_partial_fix_is_reported_and_saved(proposal):
    p, sid, target, fixed, _, _ = proposal
    old = (p.root/target).read_text(encoding='utf-8')
    # Repair lookup scope only; the vulnerable transfer and approval remain.
    begin, end = old.index('def _lookup_balance'), old.index('def _transfer')
    replacement = fixed[fixed.index('def _lookup_balance'):fixed.index('def _transfer')]
    (p.root/target).write_text(old[:begin]+replacement+old[end:], encoding='utf-8')
    result = post(p, 'verify', suggestion=sid)
    assert result['verdict'] == 'PARTIAL'
    assert result['counts']['fixed'] > 0 and result['counts']['still_failing'] > 0
    assert p.client.get(f'/api/project/suggestions/{sid}').json()['latest_verdict'] == 'PARTIAL'


def test_triage_adds_matching_hint_and_preserves_json_parity(proposal, capsys):
    p, sid, _, _, pre, _ = proposal
    code, output = command(capsys, p, 'triage', '--run', pre)
    assert code == 0 and f'Suggested fixes: {sid} (1 file(s))' in output
    code, output = command(capsys, p, 'triage', '--run', pre, '--json')
    assert code == 0 and json.loads(output) == p.client.get(f'/api/runs/{pre}/triage').json()
    p.switch('fixed')
    other = post(p)['run']['id']
    code, output = command(capsys, p, 'triage', '--run', other)
    assert 'Suggested fixes:' not in output


@pytest.mark.parametrize('suffix', ['', '/fix.diff'])
def test_strict_ids_and_missing_records(proposal, suffix):
    p, _, _, _, _, _ = proposal
    for sid in ('bad', 'fix_20261007T000000_ABCDEF', 'fix_20261007T000000_abcdef_extra',
                'fix_'+'1'*300+'_abcdef', '%2E%2E%2Fx', 'fix_x%2Fy', 'fix_x%5Cy'):
        assert p.client.get('/api/project/suggestions/'+sid+suffix).status_code == 422
    assert p.client.get('/api/project/suggestions/fix_20261007T000000_abcdef'+suffix).status_code == 404


@pytest.mark.parametrize('suffix', ['', '/fix.diff', None])
def test_authentication(proposal, monkeypatch, suffix):
    p, sid, *_ = proposal
    monkeypatch.setenv('SPECAGENT_API_TOKEN', 'suggestion-test-token')
    path = '/api/project/suggestions'+('/'+sid+suffix if suffix is not None else '')
    assert TestClient(main.app).get(path).status_code == 401
    for headers in ({'Authorization':'Bearer suggestion-test-token'}, {'X-API-Key':'suggestion-test-token'}):
        assert p.client.get(path, headers=headers).status_code == 200


def test_symlink_does_not_expose_outside_file(proposal, tmp_path):
    p, sid, *_ = proposal
    directory = p.root/'.specagent'/'suggestions'/sid
    outside = tmp_path/'outside.diff'
    outside.write_bytes(b'private-outside-content')
    diff = directory/'fix.diff'
    diff.unlink()
    try:
        diff.symlink_to(outside)
    except OSError:
        return
    response = p.client.get(f'/api/project/suggestions/{sid}/fix.diff')
    assert response.status_code == 403 and b'private-outside-content' not in response.content
    assert p.client.get(f'/api/project/suggestions/{sid}').status_code == 403


def test_verification_records_do_not_overwrite_within_a_second(proposal):
    p, sid, *_ = proposal
    first = save_verification(p.root, sid, {'verdict':'PARTIAL'})
    second = save_verification(p.root, sid, {'verdict':'ALL_FIXED'})
    assert first != second and first.is_file() and second.is_file()
    assert suggestion_view(p.root, sid)['latest_verdict'] == 'ALL_FIXED'


def test_empty_list_does_not_create_state(project, capsys):
    assert project.client.get('/api/project/suggestions').json() == []
    assert not (project.root/'.specagent').exists()
    untouched = project.root/'should-not-create.db'
    assert cli.main(['suggestions', 'list', '--config', str(project.cfg), '--db', str(untouched)]) == 0
    assert capsys.readouterr().out == 'No fix suggestions yet.\n'
    assert not untouched.exists() and not (project.root/'.specagent').exists()


def test_read_hash_uses_the_same_relative_path_through_a_root_alias(proposal, tmp_path):
    p, _, target, fixed, pre, _ = proposal
    alias = tmp_path/'project-alias'
    try:
        alias.symlink_to(p.root, target_is_directory=True)
    except OSError:
        assert os.name == 'nt'
        from test_agent_sandbox import _mklink_junction
        _mklink_junction(alias, p.root)
    core = Project.load(alias/'specagent.yaml', store=p.store)
    context = ToolContext(core, ProjectSandbox(core.root, True), allow_source=True, confirm=_approve)
    registry = ToolRegistry()
    read = registry.call(context, 'read_file', {'path':target}, 'read').payload
    assert read['path'] == target and context.read_hashes[target] == read['sha256']
    result = registry.call(context, 'write_fix_suggestion', {
        'rule_id':'ACCOUNT_SCOPE', 'diagnosis':'Alias-root regression', 'pre_fix_run_id':pre,
        'changes':[{'path':target, 'new_content':fixed}],
    }, 'write').payload
    assert result['ok'], result
