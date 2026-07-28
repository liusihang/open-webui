import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from open_webui.utils import tools as tools_mod


OPEN_WEBUI_ROOT = Path(__file__).resolve().parents[2]
BUILTIN_TOOLS_PATH = OPEN_WEBUI_ROOT / 'tools' / 'builtin.py'
TOOL_REGISTRY_PATH = OPEN_WEBUI_ROOT / 'utils' / 'tools.py'
OFFICIAL_SUBAGENTS_PATH = OPEN_WEBUI_ROOT / 'utils' / 'subagents.py'
CUSTOM_SUBAGENTS_PATH = OPEN_WEBUI_ROOT / 'agent' / 'subagents.py'

EXCLUDED_BUILTINS = {
    'delegate_task',
    'list_chat_files',
    'grep_chat_files',
    'query_chat_files',
}


def _defined_functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_excluded_official_builtins_are_not_defined_or_registered():
    assert _defined_functions(BUILTIN_TOOLS_PATH).isdisjoint(EXCLUDED_BUILTINS)

    registry_source = TOOL_REGISTRY_PATH.read_text(encoding='utf-8')
    for name in EXCLUDED_BUILTINS:
        assert name not in registry_source


def test_official_duplicate_subagent_runtime_is_absent():
    assert not OFFICIAL_SUBAGENTS_PATH.exists()
    assert CUSTOM_SUBAGENTS_PATH.exists()


def test_retained_builtins_include_notify_timer_and_existing_file_knowledge_tools():
    functions = _defined_functions(BUILTIN_TOOLS_PATH)

    assert {
        'notify',
        'timer',
        'view_file',
        'query_knowledge_files',
        'grep_knowledge_files',
    }.issubset(functions)


@pytest.mark.asyncio
async def test_timer_is_registered_without_official_subagent_configuration(monkeypatch):
    async def fake_get_many(*keys):
        return {key: False for key in keys}

    async def fake_get_chat(_chat_id):
        return SimpleNamespace(meta={})

    monkeypatch.setattr(tools_mod.Config, 'get_many', fake_get_many)
    monkeypatch.setattr(tools_mod, 'is_saved_chat_id', lambda _chat_id: True)
    monkeypatch.setattr(tools_mod.Chats, 'get_chat_by_id', fake_get_chat)

    request = SimpleNamespace(state=SimpleNamespace(internal=False, direct=False))
    tools = await tools_mod.get_builtin_tools(
        request,
        {
            '__user__': {'id': 'user-1', 'role': 'admin'},
            '__metadata__': {'chat_id': 'chat-1'},
        },
        features={},
        model={
            'info': {
                'meta': {
                    'builtinTools': {
                        'time': True,
                        'knowledge': False,
                        'chats': False,
                        'memory': False,
                        'web_search': False,
                        'image_generation': False,
                        'code_interpreter': False,
                        'notes': False,
                        'channels': False,
                        'skills': False,
                        'tasks': False,
                        'automations': False,
                        'calendar': False,
                        'notifications': False,
                    }
                }
            }
        },
    )

    assert set(tools) == {'get_current_timestamp', 'calculate_timestamp', 'timer'}


@pytest.mark.asyncio
async def test_notify_is_registered_when_webhooks_are_enabled(monkeypatch):
    async def fake_get_many(*keys):
        return {key: key == 'ui.enable_user_webhooks' for key in keys}

    monkeypatch.setattr(tools_mod.Config, 'get_many', fake_get_many)

    request = SimpleNamespace(state=SimpleNamespace(internal=False, direct=False))
    tools = await tools_mod.get_builtin_tools(
        request,
        {'__user__': {'id': 'user-1', 'role': 'admin'}, '__metadata__': {}},
        features={},
        model={
            'info': {
                'meta': {
                    'builtinTools': {
                        'time': False,
                        'knowledge': False,
                        'chats': False,
                        'memory': False,
                        'web_search': False,
                        'image_generation': False,
                        'code_interpreter': False,
                        'notes': False,
                        'channels': False,
                        'skills': False,
                        'tasks': False,
                        'automations': False,
                        'calendar': False,
                        'notifications': True,
                    }
                }
            }
        },
    )

    assert set(tools) == {'notify'}
