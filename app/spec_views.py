"""Read-only original source and behavior-version comparisons."""
import difflib
import json

import yaml

from .web_presenters import web_payload

MAX_SOURCE_CHARS = 1_000_000


def spec_view(record):
    if len(record.get('source') or '') > MAX_SOURCE_CHARS:
        raise ValueError('spec_source_too_large')
    view = web_payload(record)
    source = view.get('source') or ''
    kind = 'text'
    try:
        value = json.loads(source)
        if isinstance(value, dict) and 'rules' in value:
            kind = 'json'
    except (ValueError, RecursionError):
        try:
            value = yaml.safe_load(source)
            if isinstance(value, dict) and 'rules' in value:
                kind = 'yaml'
        except (yaml.YAMLError, RecursionError):
            pass
    return {**view, 'source_kind':kind, 'source_redacted':source != record.get('source', ''),
            'compiler': (view.get('compiled') or {}).get('compiler', ''),
            'rules':len((view.get('compiled') or {}).get('rules') or [])}


def spec_diff(baseline, candidate):
    a, b = spec_view(baseline), spec_view(candidate)
    a_rules = {r['id'] for r in (a.get('compiled') or {}).get('rules', [])}
    b_rules = {r['id'] for r in (b.get('compiled') or {}).get('rules', [])}
    lines = difflib.unified_diff(a['source'].splitlines(), b['source'].splitlines(),
        fromfile=f"v{a['version']} ({a['id']})", tofile=f"v{b['version']} ({b['id']})", lineterm='')
    return {'project_id':a['project_id'], 'baseline_id':a['id'], 'candidate_id':b['id'],
            'baseline_version':a['version'], 'candidate_version':b['version'],
            'diff':'\n'.join(lines), 'source_redacted':a['source_redacted'] or b['source_redacted'],
            'added_rules':sorted(b_rules - a_rules), 'removed_rules':sorted(a_rules - b_rules)}
