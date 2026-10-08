"""Server-owned audit identity, scoped to one operation and never a token value."""
import getpass
from contextlib import contextmanager
from contextvars import ContextVar

from .auth import configured_token

_identity = ContextVar('baseline_audit_identity', default=None)


def baseline_identity():
    return dict(_identity.get() or {'source':'local', 'actor':'local_process', 'identity':'unattributed'})


def web_identity(source='web'):
    from .accounts import principal_context
    principal = principal_context.get()
    if principal:
        return {'source': source, 'actor': principal.username, 'identity': 'account_user'}
    shared = configured_token() is not None
    return {'source':source, 'actor':'shared_token_user' if shared else 'local_browser_user',
            'identity':'shared_token' if shared else 'anonymous'}


def cli_identity():
    try:
        actor = getpass.getuser()
    except (OSError, KeyError):
        actor = 'local_process'
    return {'source':'cli', 'actor':actor[:128], 'identity':'os_user'}


@contextmanager
def baseline_context(**values):
    token = _identity.set({**baseline_identity(), **values})
    try:
        yield
    finally:
        _identity.reset(token)
