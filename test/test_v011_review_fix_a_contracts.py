import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OPEN_WEBUI_ROOT = REPO_ROOT / 'backend' / 'open_webui'


def _module_tree(relative_path: str) -> ast.Module:
    return ast.parse((OPEN_WEBUI_ROOT / relative_path).read_text())


def test_orjson_http_json_patch_runs_before_fastapi_app_creation() -> None:
    tree = _module_tree('main.py')
    patch_call_lines = [
        node.lineno
        for node in tree.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == 'apply_orjson_http_json'
    ]
    app_creation_lines = [
        node.lineno
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == 'app' for target in node.targets)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == 'FastAPI'
    ]

    assert patch_call_lines, 'main.py must call apply_orjson_http_json() at module scope'
    assert app_creation_lines, 'main.py must create the FastAPI app at module scope'
    assert patch_call_lines[0] < app_creation_lines[0]


def test_alembic_env_registers_official_and_custom_model_metadata() -> None:
    tree = _module_tree('migrations/env.py')
    model_imports = {
        node.module: {alias.name for alias in node.names}
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith('open_webui.models.')
    }

    assert {'ChatMessage'} <= model_imports.get('open_webui.models.chat_messages', set())
    assert {'Chat'} <= model_imports.get('open_webui.models.chats', set())
    assert {'AgentRun'} <= model_imports.get('open_webui.models.agent_runs', set())
    assert {'Calendar'} <= model_imports.get('open_webui.models.calendar', set())
