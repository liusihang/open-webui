from pathlib import Path
import ast


SYMBOL = 'NATIVE_ATTACHED_KNOWLEDGE_BYPASS_LEGACY_FILE_RETRIEVAL'
OPEN_WEBUI_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = OPEN_WEBUI_DIR / 'config.py'
MAIN_PATH = OPEN_WEBUI_DIR / 'main.py'


def _main_config_imports() -> list[str]:
    tree = ast.parse(MAIN_PATH.read_text())
    imported = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == 'open_webui.config':
            imported.extend(alias.name for alias in node.names if alias.name != '*')
    return imported


def _config_public_names() -> set[str]:
    tree = ast.parse(CONFIG_PATH.read_text())
    exported = set()

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    exported.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            exported.add(node.target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            exported.add(node.name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                exported.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                exported.add(alias.asname or alias.name.split('.')[0])

    return exported


def test_native_attached_knowledge_bypass_flag_is_defined_in_config() -> None:
    main_text = MAIN_PATH.read_text()
    config_text = CONFIG_PATH.read_text()
    config_path = (
        'layered_knowledge.'
        'native_attached_knowledge_bypass_legacy_file_retrieval'
    )

    assert f'{SYMBOL} = (' in config_text
    assert f"'{config_path}': {SYMBOL}" in config_text
    assert f"'{SYMBOL}': '{config_path}'" in main_text


def test_main_only_imports_public_names_from_config() -> None:
    missing = sorted(set(_main_config_imports()) - _config_public_names())

    assert missing == []
