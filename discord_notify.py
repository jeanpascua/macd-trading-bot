import os
import logging

try:
    import requests
except ImportError:
    requests = None

log = logging.getLogger(__name__)

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK_URL', '').strip()
TIMEOUT = 5


def notify(message: str, level: str = 'info') -> None:
    if not WEBHOOK_URL or requests is None:
        return
    prefix = {'info': '', 'buy': '🟢 ', 'sell': '🔴 ', 'warn': '⚠️ ', 'error': '🛑 ', 'start': '🚀 ', 'done': '✅ '}.get(level, '')
    payload = {'content': f'{prefix}{message}'[:1900]}
    try:
        requests.post(WEBHOOK_URL, json=payload, timeout=TIMEOUT)
    except Exception as e:
        log.warning(f'Discord notify failed: {e}')
