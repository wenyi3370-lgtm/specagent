"""Read-only configuration projection; never build clients or import targets."""
import importlib.util
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .auth import agent_api_enabled, configured_token
from .agent.sandbox import redact_text
from .config import load_config
from .errors import SpecValidationError
from .llm_client import DEFAULT_MODEL, resolve_model
from .project import project_config_path
from .web_presenters import web_payload

ADAPTER_DESCRIPTIONS = {
    'demo': '内置确定性演示 Agent',
    'http': '通过 HTTP 协议读取响应与轨迹',
    'openai': 'OpenAI Responses 工具调用适配器',
    'langgraph': 'LangGraph 事件轨迹适配器',
    'python': '本机 Python callable 适配器',
}


def _sdk_installed():
    try:
        return importlib.util.find_spec('openai') is not None
    except (ImportError, ValueError):
        return False


def _model(name, source):
    return {'name': name, 'source': source, 'truncated': False}


def _safe(value):
    if isinstance(value, str):
        return redact_text(re.sub(r'https?://[^\s<>"\']+', '[url]', value, flags=re.IGNORECASE))
    if isinstance(value, list):
        return [_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _safe(v) for k, v in value.items()}
    return value


def _clip(record, key, limit=256):
    value = record.get(key)
    limited = isinstance(value, str) and len(value) > limit
    if limited:
        record[key] = value[:limit]
    return limited


def _target_config():
    path = Path(project_config_path())
    try:
        if not path.is_file():
            return {'configured': False, 'state': 'missing', 'adapter': None,
                    'errors': [], 'note': '未配置项目测试目标，旧演示运行仍按其请求和服务端环境选择目标。'}
        config = load_config(str(path))
    except SpecValidationError as exc:
        return {'configured': False, 'state': 'invalid', 'adapter': None,
                'errors': list(exc.errors), 'note': '项目配置无效。'}
    except (OSError, ValueError):
        return {'configured': False, 'state': 'unreadable', 'adapter': None,
                'errors': ['project_config_unreadable'], 'note': '无法读取项目配置。'}
    a = config.adapter
    endpoint_name = a.endpoint_env if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', a.endpoint_env) else None
    adapter = {'type': a.type, 'description': ADAPTER_DESCRIPTIONS[a.type],
               'entrypoint': a.agent if a.agent and a.type in ('python', 'openai', 'langgraph') else None,
               'entrypoint_truncated': False,
               'variant': a.variant if a.variant and a.type == 'demo' else None,
               'variant_truncated': False,
               'endpoint_env': endpoint_name if a.type == 'http' else None,
               'endpoint_configured': bool(os.getenv(a.endpoint_env)) if a.type == 'http' else None,
               'target_model': _model(a.model, 'adapter.model') if a.type == 'openai' and a.model else None,
               'model_state': 'override' if a.type == 'openai' and a.model else 'not_applicable' if a.type == 'demo' else 'target_owned',
               'verified': False}
    return {'configured': True, 'state': 'loaded', 'project_id': config.project,
            'adapter': adapter, 'run': config.run.model_dump(),
            'gate': config.gate.model_dump(), 'agent': config.agent.model_dump(),
            'errors': [], 'note': '只读取项目配置。规格文件、被测模块和远端服务未在此页面验证。'}


def settings_view(db_backend):
    key = os.getenv('OPENAI_API_KEY', '')
    agent_source = 'SPECAGENT_AGENT_MODEL' if os.getenv('SPECAGENT_AGENT_MODEL') else 'OPENAI_MODEL' if os.getenv('OPENAI_MODEL') else 'default'
    result = {
        'version': __version__, 'db_backend': db_backend,
        'read_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        'auth': {'mode': 'shared_token' if configured_token() else 'local',
                 'agent_api_enabled': agent_api_enabled(),
                 'individual_accounts': False},
        'llm': {'key_configured': bool(key.strip()),
                'key_status': 'configured' if key.strip() else 'blank' if key else 'missing',
                'sdk_installed': _sdk_installed(), 'connection': 'not_checked',
                'provider': 'custom' if os.getenv('OPENAI_BASE_URL') else 'sdk_default',
                'agent_and_draft_model': _model(resolve_model(), agent_source),
                'behavior_model': _model(os.getenv('OPENAI_MODEL', DEFAULT_MODEL), 'OPENAI_MODEL' if 'OPENAI_MODEL' in os.environ else 'default')},
        'target': _target_config(),
        'capabilities': {'deterministic_gate': True, 'llm_judge_advisory': True,
                         'draft_preview_only': True, 'fix_apply_in_browser': False,
                         'settings_read_only': True},
    }
    # Redact full strings before bounding them so truncated secret prefixes cannot escape.
    result = _safe(web_payload(result))
    for model in (result['llm']['agent_and_draft_model'], result['llm']['behavior_model']):
        model['truncated'] = _clip(model, 'name')
    target = result['target']
    target['project_truncated'] = _clip(target, 'project_id')
    errors = target['errors']
    target['errors_limited'] = len(errors) > 20 or any(len(e) > 1000 for e in errors)
    target['errors'] = [e[:1000] for e in errors[:20]]
    adapter = target['adapter']
    if adapter:
        adapter['entrypoint_truncated'] = _clip(adapter, 'entrypoint')
        adapter['variant_truncated'] = _clip(adapter, 'variant', 128)
        if adapter['target_model']:
            adapter['target_model']['truncated'] = _clip(adapter['target_model'], 'name')
    return result
