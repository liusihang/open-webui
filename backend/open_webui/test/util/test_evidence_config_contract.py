from pathlib import Path
import ast


SYMBOL = "ENABLE_MULTIMODAL_KNOWLEDGE_EVIDENCE"
OPEN_WEBUI_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = OPEN_WEBUI_DIR / "config.py"
MAIN_PATH = OPEN_WEBUI_DIR / "main.py"


def _main_config_imports() -> list[str]:
    tree = ast.parse(MAIN_PATH.read_text())
    imported = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "open_webui.config":
            imported.extend(alias.name for alias in node.names if alias.name != "*")
    return imported


def test_multimodal_evidence_flag_is_defined_in_config_and_main_imports_it() -> None:
    config_text = CONFIG_PATH.read_text()
    main_text = MAIN_PATH.read_text()

    assert SYMBOL in config_text
    assert f"{SYMBOL} = ConfigVar(" in config_text
    assert SYMBOL in _main_config_imports()
    assert f"app.state.config.{SYMBOL} = {SYMBOL}" in main_text
