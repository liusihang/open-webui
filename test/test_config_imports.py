import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / 'backend' / 'open_webui' / 'config.py'


def _top_level_imports(module: ast.Module) -> set[str]:
    imported_names: set[str] = set()

    for node in module.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.asname or alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module == 're':
                imported_names.add('re')

    return imported_names


def _uses_re_split(module: ast.Module) -> bool:
    for node in ast.walk(module):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != 'split':
            continue
        if isinstance(node.func.value, ast.Name) and node.func.value.id == 're':
            return True

    return False


def test_config_imports_re_when_using_re_split():
    module = ast.parse(CONFIG_PATH.read_text(encoding='utf-8'))

    assert _uses_re_split(module), 'expected config.py to use re.split in ONLYOFFICE host parsing'
    assert 're' in _top_level_imports(module)
