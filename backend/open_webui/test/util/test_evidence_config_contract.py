from pathlib import Path
import ast
import os
import subprocess
import sys


SYMBOL = "ENABLE_MULTIMODAL_KNOWLEDGE_EVIDENCE"
ASSET_SYMBOL = "RAG_EXTRACT_DOCUMENT_IMAGE_ASSETS"
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


def _read_asset_default(*, multimodal_env: str, asset_env: str | None = None) -> bool:
    env = os.environ.copy()
    env.update(
        {
            "ENABLE_DB_MIGRATIONS": "false",
            "ENABLE_PERSISTENT_CONFIG": "false",
            "WEBUI_SECRET_KEY": "test-secret",
            "ENABLE_MULTIMODAL_KNOWLEDGE_EVIDENCE": multimodal_env,
        }
    )
    if asset_env is None:
        env.pop(ASSET_SYMBOL, None)
    else:
        env[ASSET_SYMBOL] = asset_env

    output = subprocess.check_output(
        [
            sys.executable,
            "-c",
            (
                "from open_webui.config import RAG_EXTRACT_DOCUMENT_IMAGE_ASSETS;"
                "print(RAG_EXTRACT_DOCUMENT_IMAGE_ASSETS.value)"
            ),
        ],
        cwd=OPEN_WEBUI_DIR.parent,
        env=env,
        text=True,
    )
    return output.strip().splitlines()[-1] == "True"


def test_document_image_asset_extraction_defaults_to_multimodal_initial_env() -> None:
    assert _read_asset_default(multimodal_env="true") is True
    assert _read_asset_default(multimodal_env="false") is False


def test_document_image_asset_extraction_explicit_env_overrides_multimodal_default() -> None:
    assert _read_asset_default(multimodal_env="true", asset_env="false") is False
    assert _read_asset_default(multimodal_env="false", asset_env="true") is True
