"""Only the private browser fixture; never a real deployment database."""
from app.accounts import Accounts
from app.storage import Store

store = Store()
store.create_project('accounts-demo', 'Project Atlas', description='Private browser fixture')
store.create_project('private-other', 'Project Cedar')
accounts = Accounts(store)
for username, role in [('admin-demo', 'admin'), ('editor-demo', 'editor'), ('viewer-demo', 'viewer')]:
    uid = accounts.create_user(username, 'browser-fixture-password-2026', admin=role == 'admin')
    if role != 'admin':
        accounts.grant(uid, 'accounts-demo', role)
