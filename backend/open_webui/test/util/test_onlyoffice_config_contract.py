from pathlib import Path
import ast
import os
import subprocess
import sys


ONLYOFFICE_SYMBOLS = [
    "ENABLE_ONLYOFFICE_PREVIEW",
    "ONLYOFFICE_DOCUMENT_SERVER_URL",
    "ONLYOFFICE_PUBLIC_BASE_URL",
    "ONLYOFFICE_JWT_SECRET",
    "ONLYOFFICE_FILE_TOKEN_EXPIRES_IN",
    "ONLYOFFICE_EDIT_CALLBACK_TOKEN_EXPIRES_IN",
    "ONLYOFFICE_CALLBACK_ALLOWED_HOSTS",
]

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


def test_onlyoffice_runtime_flags_are_defined_and_assigned_to_app_config() -> None:
    config_text = CONFIG_PATH.read_text()
    main_text = MAIN_PATH.read_text()
    imports = _main_config_imports()

    for symbol in ONLYOFFICE_SYMBOLS:
        assert f"{symbol} = ConfigVar(" in config_text
        assert symbol in imports
        assert f"app.state.config.{symbol} = {symbol}" in main_text


def test_onlyoffice_callback_allowlist_parses_comma_separated_env() -> None:
    env = os.environ.copy()
    env.update(
        {
            "ENABLE_DB_MIGRATIONS": "false",
            "ENABLE_PERSISTENT_CONFIG": "false",
            "WEBUI_SECRET_KEY": "test-secret",
            "ONLYOFFICE_CALLBACK_ALLOWED_HOSTS": "onlyoffice.internal, docs.example.com ",
        }
    )

    output = subprocess.check_output(
        [
            sys.executable,
            "-c",
            (
                "from open_webui.config import ONLYOFFICE_CALLBACK_ALLOWED_HOSTS;"
                "print('|'.join(ONLYOFFICE_CALLBACK_ALLOWED_HOSTS.value))"
            ),
        ],
        cwd=OPEN_WEBUI_DIR.parent,
        env=env,
        text=True,
    )

    assert output.strip().splitlines()[-1] == "onlyoffice.internal|docs.example.com"
