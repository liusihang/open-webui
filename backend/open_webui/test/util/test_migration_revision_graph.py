from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


REPO_ROOT = Path(__file__).resolve().parents[4]
MIGRATIONS_DIR = REPO_ROOT / "backend" / "open_webui" / "migrations"


def _load_script_directory() -> ScriptDirectory:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return ScriptDirectory.from_config(config)


def test_migration_graph_resolves_afc_hotfix_head():
    script = _load_script_directory()

    assert script.get_current_head() == "e8c4b9a2d1f0"
    assert script.get_revision("20260327_add_knowledge_layer_embedding_state") is not None
    assert script.get_revision("e8c4b9a2d1f0") is not None


def test_migration_graph_keeps_legacy_knowledge_revision_upgradeable():
    script = _load_script_directory()

    chunking_revision = script.get_revision("d4e5f6a7b8c9")
    bridge_revision = script.get_revision("20260327_add_knowledge_layer_embedding_state")
    automation_revision = script.get_revision("da5f6a7b8c90")
    tasks_revision = script.get_revision("a3dd5bedd151")
    head_revision = script.get_revision("e8c4b9a2d1f0")

    assert chunking_revision is not None
    assert bridge_revision is not None
    assert automation_revision is not None
    assert tasks_revision is not None
    assert head_revision is not None
    assert chunking_revision.down_revision == "c3d4e5f6a7b8"
    assert bridge_revision.down_revision == "d4e5f6a7b8c9"
    assert tasks_revision.down_revision == "20260327_add_knowledge_layer_embedding_state"
    assert automation_revision.down_revision == "a3dd5bedd151"
    assert head_revision.down_revision == "56359461a091"
