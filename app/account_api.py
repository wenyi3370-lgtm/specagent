"""Browser login/logout and administrator membership controls."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .accounts import COOKIE, SESSION_SECONDS, csrf_token
from .auth import (account_repository, auth_mode, require_api_token,
                   require_same_origin, _host_header_allowed, _LOOPBACK_HOSTS)

router = APIRouter(dependencies=[Depends(require_api_token)])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class MembershipRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_id: str = Field(min_length=1, max_length=32)
    project_id: str = Field(min_length=1, max_length=64)
    role: Literal['viewer', 'editor']


def _enabled(response):
    if auth_mode() != 'multiuser':
        raise HTTPException(404, 'account_mode_disabled')
    response.headers['Cache-Control'] = 'no-store'


@router.post('/api/auth/login')
def login(body: LoginRequest, request: Request, response: Response):
    _enabled(response)
    require_same_origin(request)
    if request.headers.get('content-type', '').partition(';')[0].lower().strip() != 'application/json':
        raise HTTPException(422, 'json_required')
    peer = request.client.host if request.client else ''
    if request.url.scheme != 'https' and not (peer in _LOOPBACK_HOSTS and _host_header_allowed(request)):
        raise HTTPException(403, 'https_required')
    try:
        accounts = account_repository()
        token = accounts.login(body.username, body.password, peer, request.cookies.get(COOKIE))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, 'account_store_unavailable') from None
    if token is None:
        raise HTTPException(401, 'invalid_login')
    response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
                        secure=request.url.scheme == 'https', samesite='strict', path='/')
    return {'ok': True, 'csrf': csrf_token(token)}


@router.get('/api/auth/me')
def me(request: Request, response: Response):
    _enabled(response)
    principal = request.state.principal
    accounts = account_repository()
    projects = [{'id': p['id'], 'name': p['name'], 'role': accounts.role(principal, p['id'])}
                for p in accounts.store.list_projects() if accounts.role(principal, p['id'])]
    return {'username': principal.username, 'admin': principal.admin,
            'csrf': csrf_token(request.cookies[COOKIE]), 'projects': projects,
            'server_project_role': _target_role(accounts, principal)}


def _target_role(accounts, principal):
    from .web_access import configured_project_id
    try:
        return accounts.role(principal, configured_project_id())
    except HTTPException:
        return None


@router.post('/api/auth/logout')
def logout(request: Request, response: Response):
    _enabled(response)
    account_repository().logout(request.cookies[COOKIE])
    response.delete_cookie(COOKIE, path='/', httponly=True, secure=request.url.scheme == 'https', samesite='strict')
    return {'ok': True}


@router.get('/api/accounts/users')
def users(response: Response):
    _enabled(response)
    return account_repository().users()


@router.get('/api/accounts/memberships')
def memberships(response: Response, project_id: str = Query(min_length=1, max_length=64)):
    _enabled(response)
    return account_repository().memberships(project_id)


@router.put('/api/accounts/memberships')
def grant(body: MembershipRequest, response: Response):
    _enabled(response)
    account_repository().grant(body.user_id, body.project_id, body.role)
    return {'ok': True}


@router.delete('/api/accounts/memberships/{user_id}')
def revoke(user_id: str, response: Response, project_id: str = Query(min_length=1, max_length=64)):
    _enabled(response)
    account_repository().revoke(user_id, project_id)
    return {'ok': True}
