"""Private browser server. It replaces delivery with a fake, never network I/O."""
import runpy

from app import main, notifications

runpy.run_path('tests/browser/seed_accounts.py')
attempts = []


def fake_delivery(profile, payload):
    attempts.append({'channel_id': profile.id, 'run': payload})
    if profile.id == 'failed-hook':
        raise TimeoutError('fake ambiguous transport failure')


notifications.deliver = fake_delivery
app = main.app


@app.get('/__test/notification-attempts')
def read_attempts():
    return attempts
