"""Fail-closed authorization for every API route in account mode."""
from fastapi import HTTPException

from .config import load_config
from .project import project_config_path


def configured_project_id():
    try:
        return load_config(project_config_path()).project
    except Exception:
        raise HTTPException(403, 'project_access_denied') from None


async def authorize(request, accounts, principal):
    route = getattr(request.scope.get('route'), 'path', '')
    method = request.method
    store = accounts.store
    edit = method != 'GET'

    def project(pid, write=edit):
        if not isinstance(pid, str) or not pid:
            raise HTTPException(403, 'project_access_denied')
        accounts.require_project(principal, pid, write)

    def run(rid, write=edit):
        item = store.get_run(rid)
        if item is None:
            raise HTTPException(404, 'resource_not_found')
        try:
            project(item['project_id'], write)
        except HTTPException:
            raise HTTPException(404, 'resource_not_found') from None

    def spec(sid):
        item = store.get_spec(sid)
        if item is None:
            raise HTTPException(404, 'resource_not_found')
        try:
            project(item['project_id'])
        except HTTPException:
            raise HTTPException(404, 'resource_not_found') from None

    if route in ('/api/auth/me', '/api/auth/logout'):
        return
    if route in ('/api/settings', '/api/compile', '/api/specs/compile') or route.startswith('/api/accounts/') or (route == '/api/projects' and method == 'POST'):
        if not principal.admin:
            raise HTTPException(403, 'admin_required')
        return
    if route == '/api/projects' and method == 'GET':
        return  # The handler filters metadata.
    if route == '/api/runs':
        if method == 'GET':
            pid = request.query_params.get('project_id')
            if pid is not None:
                project(pid)
            return  # An omitted project is filtered in the handler.
        if method == 'POST':
            try:
                body = await request.json()
                project(body.get('project_id', 'default'))
            except (ValueError, AttributeError):
                raise HTTPException(422, 'invalid_request') from None
            return
    if route == '/api/run-all' and method == 'POST':
        project('default')
        return
    if route in ('/api/runs/search', '/api/specs', '/api/metrics', '/api/metrics/history', '/api/baselines/history', '/api/baselines/clear'):
        project(request.query_params.get('project_id', 'default'))
        return
    if route == '/api/diff':
        candidate = request.query_params.get('candidate')
        if not candidate:
            raise HTTPException(422, 'candidate_required')
        run(candidate)
        baseline = request.query_params.get('baseline')
        if baseline:
            run(baseline)
        else:
            project(request.query_params.get('project_id', 'default'))
        return
    if route == '/api/specs/diff':
        for key in ('baseline', 'candidate'):
            value = request.query_params.get(key)
            if not value:
                raise HTTPException(422, 'spec_required')
            spec(value)
        return
    if route.startswith('/api/runs/{run_id}'):
        run(request.path_params['run_id'])
        return
    if route.startswith('/api/specs/{spec_id}'):
        spec(request.path_params['spec_id'])
        return
    if route.startswith('/api/executions/{execution_id}'):
        item = store.get_execution(request.path_params['execution_id'])
        if item is None:
            raise HTTPException(404, 'resource_not_found')
        run(item['run_id'])
        return
    if route == '/api/project' or route.startswith('/api/project/'):
        # Resolve configuration before target imports, adapter construction or LLM work.
        if principal.admin:
            return
        project(configured_project_id())
        if method == 'POST' and route in ('/api/project/runs', '/api/project/verify'):
            try:
                body = await request.json()
                for key in ('baseline', 'pre_run_id'):
                    rid = body.get(key)
                    if rid and rid != 'last':
                        run(rid, False)
            except (ValueError, AttributeError):
                raise HTTPException(422, 'invalid_request') from None
        return
    if route in ('/api/agent/sessions', '/api/agent/logs', '/api/agent/logs/{log_id}'):
        if principal.admin:
            return
        project(configured_project_id())
        return
    if route.startswith('/api/agent/sessions/{session_id}/'):
        from .agent_api import _get_session
        session = _get_session(request.path_params['session_id'])
        project(session.project_id)
        return
    raise HTTPException(403, 'route_access_denied')
