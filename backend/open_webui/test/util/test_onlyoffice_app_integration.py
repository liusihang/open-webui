import ast
from pathlib import Path


OPEN_WEBUI_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = OPEN_WEBUI_DIR / 'config.py'
MAIN_PATH = OPEN_WEBUI_DIR / 'main.py'

ONLYOFFICE_CONFIG = {
    'ENABLE_ONLYOFFICE_PREVIEW': 'onlyoffice.enable_preview',
    'ONLYOFFICE_DOCUMENT_SERVER_URL': 'onlyoffice.document_server_url',
    'ONLYOFFICE_PUBLIC_BASE_URL': 'onlyoffice.public_base_url',
    'ONLYOFFICE_JWT_SECRET': 'onlyoffice.jwt_secret',
    'ONLYOFFICE_FILE_TOKEN_EXPIRES_IN': 'onlyoffice.file_token_expires_in',
    'ONLYOFFICE_EDIT_CALLBACK_TOKEN_EXPIRES_IN': 'onlyoffice.edit_callback_token_expires_in',
    'ONLYOFFICE_CALLBACK_ALLOWED_HOSTS': 'onlyoffice.callback_allowed_hosts',
}


def _module_ast(path: Path) -> ast.Module:
    return ast.parse(path.read_text())


def _top_level_names(path: Path) -> set[str]:
    names = set()
    for node in _module_ast(path).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _dict_literal_assignment(path: Path, name: str) -> dict[str, str]:
    for node in _module_ast(path).body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            continue
        if not isinstance(node.value, ast.Dict):
            return {}

        values = {}
        for key_node, value_node in zip(node.value.keys, node.value.values):
            if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
                continue
            if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str):
                values[key_node.value] = value_node.value
            elif isinstance(value_node, ast.Name):
                values[key_node.value] = value_node.id
        return values
    return {}


def _router_imports() -> set[str]:
    imported = set()
    for node in _module_ast(MAIN_PATH).body:
        if isinstance(node, ast.ImportFrom) and node.module == 'open_webui.routers':
            imported.update(alias.name for alias in node.names)
    return imported


def _router_mounts() -> set[tuple[str, str]]:
    mounts = set()
    for node in ast.walk(_module_ast(MAIN_PATH)):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != 'include_router':
            continue
        if not isinstance(node.func.value, ast.Name) or node.func.value.id != 'app':
            continue
        if not node.args:
            continue
        router_arg = node.args[0]
        if not (
            isinstance(router_arg, ast.Attribute)
            and router_arg.attr == 'router'
            and isinstance(router_arg.value, ast.Name)
        ):
            continue
        prefix = None
        for keyword in node.keywords:
            if keyword.arg == 'prefix' and isinstance(keyword.value, ast.Constant):
                prefix = keyword.value.value
        if isinstance(prefix, str):
            mounts.add((router_arg.value.id, prefix))
    return mounts


def test_onlyoffice_defaults_are_seeded_for_runtime_config() -> None:
    config_names = _top_level_names(CONFIG_PATH)
    default_config = _dict_literal_assignment(CONFIG_PATH, 'DEFAULT_CONFIG')

    assert set(ONLYOFFICE_CONFIG) <= config_names
    assert set(ONLYOFFICE_CONFIG.values()) <= set(default_config)


def test_onlyoffice_legacy_config_attributes_map_to_seeded_keys() -> None:
    aliases = _dict_literal_assignment(MAIN_PATH, 'CONFIG_ATTR_ALIASES')

    for attr_name, config_key in ONLYOFFICE_CONFIG.items():
        assert aliases.get(attr_name) == config_key


def test_onlyoffice_router_is_imported_and_mounted() -> None:
    assert 'onlyoffice' in _router_imports()
    assert ('onlyoffice', '/api/v1/onlyoffice') in _router_mounts()
