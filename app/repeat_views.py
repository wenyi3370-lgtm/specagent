"""Read-only, redacted views of per-repeat evidence; never re-run the Agent."""
import difflib
import json

from .agent.sandbox import redact_text
from .trace import _redact
from .web_presenters import web_payload

MAX_TEXT = 20000
MAX_EVENTS = 200


def _safe(value):
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _safe(v) for k, v in value.items()}
    return value


def _entry(raw, index):
    view = _safe(web_payload(_redact(raw)))
    execution = view.get('execution') or {}
    response = execution.get('response') or ''
    trace = execution.get('trace') or []
    trace_text = json.dumps(trace[:MAX_EVENTS], ensure_ascii=False, sort_keys=True, indent=2)
    error = execution.get('error') or ''
    limited = len(response) > MAX_TEXT or len(trace) > MAX_EVENTS or len(trace_text) > MAX_TEXT or len(error) > MAX_TEXT
    execution.pop('raw', None)
    execution.pop('trace', None)
    return {**view, 'index': index, 'execution': {**execution,
            'response': response[:MAX_TEXT], 'error': error[:MAX_TEXT],
            'trace_text': trace_text[:MAX_TEXT], 'trace_events': len(trace)},
            'view_limited': limited}


def _records(execution):
    rows = execution.get('repeat') or []
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('repeat_data_invalid')
    return rows


def repeat_page(execution, *, offset=0, limit=10):
    rows = _records(execution)
    requested = rows[0].get('requested') if rows else None
    return {'execution_id': execution['id'], 'run_id': execution['run_id'],
            'test_case_id': execution['test_case_id'], 'final_status': execution['status'],
            'available': bool(rows), 'requested': requested, 'total': len(rows),
            'executed': sum(row.get('executed', True) for row in rows),
            'offset': offset, 'limit': limit, 'has_more': offset + limit < len(rows),
            'items': [_entry(row, index) for index, row in
                      enumerate(rows[offset:offset + limit], offset + 1)]}


def _diff(a, b):
    text = '\n'.join(difflib.unified_diff(a.splitlines(), b.splitlines(),
                    fromfile='before', tofile='after', lineterm=''))
    return {'text': text[:MAX_TEXT], 'limited': len(text) > MAX_TEXT}


def repeat_diff(execution, left, right):
    rows = _records(execution)
    if left > len(rows) or right > len(rows):
        raise IndexError('repeat_not_found')
    a, b = _entry(rows[left - 1], left), _entry(rows[right - 1], right)
    response = _diff(a['execution'].get('response', ''), b['execution'].get('response', ''))
    traces = [x['execution']['trace_text'] for x in (a, b)]
    trace = _diff(*traces)
    before, after = a.get('violations', []), b.get('violations', [])
    return {'execution_id': execution['id'], 'left': a, 'right': b,
            'response_diff': response['text'], 'trace_diff': trace['text'],
            'added_violations': [v for v in after if v not in before],
            'removed_violations': [v for v in before if v not in after],
            'limited': response['limited'] or trace['limited'] or a['view_limited'] or b['view_limited']}
