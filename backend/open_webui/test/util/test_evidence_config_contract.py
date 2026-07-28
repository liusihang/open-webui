from pathlib import Path


SYMBOL = "ENABLE_MULTIMODAL_KNOWLEDGE_EVIDENCE"
OPEN_WEBUI_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = OPEN_WEBUI_DIR / "config.py"
MAIN_PATH = OPEN_WEBUI_DIR / "main.py"

def test_multimodal_evidence_flag_is_defined_in_config_and_main_imports_it() -> None:
    config_text = CONFIG_PATH.read_text()
    main_text = MAIN_PATH.read_text()

    assert SYMBOL in config_text
    assert f"{SYMBOL} = (" in config_text
    assert f"'rag.enable_multimodal_knowledge_evidence': {SYMBOL}" in config_text
    assert f"'{SYMBOL}': 'rag.enable_multimodal_knowledge_evidence'" in main_text
