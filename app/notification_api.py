"""Protected manual notification previews, confirmation and history."""
import hashlib
import threading

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .accounts import principal_context
from .auth import account_repository, configured_token, require_api_token
from .notifications import Notifications
from .web_presenters import web_payload

router = APIRouter(prefix='/api/notifications', dependencies=[Depends(require_api_token)])
_lock = threading.Lock()


def repository(store=None):
    if store is None:
        from .main import store
    with _lock:
        result = getattr(store, '_notifications', None)
        if result is None:
            result = Notifications(store)
            store._notifications = result
        return result


def identity():
    principal = principal_context.get()
    if principal:
        return principal.login_id, principal.username
    token = configured_token()
    if not token:
        raise HTTPException(403, 'notifications_require_authenticated_user')
    return hashlib.sha256(token.encode()).hexdigest(), 'shared_token_user'


class ChannelUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: StrictBool


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    run_id: str = Field(min_length=1, max_length=64)
    channel_id: str = Field(pattern=r'^[a-z][a-z0-9_-]{0,39}$')


class SendRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    delivery_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    confirm_run_id: str = Field(min_length=1, max_length=64)
    confirm: StrictBool


@router.get('/channels')
def channels(project_id: str = Query(default='default', min_length=1, max_length=64)):
    principal = principal_context.get()
    manage = principal.admin if principal else bool(configured_token())
    send = account_repository().role(principal, project_id) in ('editor', 'admin') if principal else bool(configured_token())
    return web_payload({**repository().channels(), 'permissions': {'configure': manage, 'send': send}})


@router.post('/channels/{channel_id}')
def configure(channel_id: str, body: ChannelUpdate):
    identity()
    return web_payload(repository().configure(channel_id, body.enabled))


@router.get('/history')
def history(project_id: str = Query(min_length=1, max_length=64), offset: int = Query(default=0, ge=0, le=10000), limit: int = Query(default=20, ge=1, le=50)):
    return web_payload(repository().history(project_id, offset, limit))


@router.post('/preview')
def preview(body: PreviewRequest):
    owner, actor = identity()
    return web_payload(repository().preview(body.run_id, body.channel_id, owner, actor))


@router.post('/send')
def send(body: SendRequest):
    owner, _ = identity()
    return web_payload(repository().send(body.delivery_id, body.confirm_run_id, owner, body.confirm))
