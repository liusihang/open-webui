from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


REPO_ROOT = Path(__file__).resolve().parents[4]
MIGRATIONS_DIR = REPO_ROOT / 'backend' / 'open_webui' / 'migrations'


def _load_script_directory() -> ScriptDirectory:
    config = Config()
    config.set_main_option('script_location', str(MIGRATIONS_DIR))
    return ScriptDirectory.from_config(config)


def test_migration_graph_resolves_afc_hotfix_head():
    script = _load_script_directory()

    assert script.get_current_head() == 'd1e2f3a4b5c6'
    assert script.get_revision('f0a1b2c3d4e5') is not None
    assert script.get_revision('e8c4b9a2d1f0') is not None
    assert script.get_revision('4de81c2a3af1') is not None
    assert script.get_revision('a0b1c2d3e4f5') is not None
    assert script.get_revision('461111b60977') is not None
    assert script.get_revision('b6f7c8d9e0a1') is not None
    assert script.get_revision('c7d8e9f0a1b2') is not None
    assert script.get_revision('d1e2f3a4b5c6') is not None


def test_migration_graph_keeps_legacy_knowledge_revision_upgradeable():
    script = _load_script_directory()

    chunking_revision = script.get_revision('d4e5f6a7b8c9')
    bridge_revision = script.get_revision('f0a1b2c3d4e5')
    automation_revision = script.get_revision('da5f6a7b8c90')
    tasks_revision = script.get_revision('a3dd5bedd151')
    compatibility_revision = script.get_revision('e8c4b9a2d1f0')
    pinned_note_revision = script.get_revision('4de81c2a3af1')
    stale_head_revision = script.get_revision('a0b1c2d3e4f5')
    legacy_pk_revision = script.get_revision('461111b60977')
    retrieval_chunk_revision = script.get_revision('b6f7c8d9e0a1')
    head_revision = script.get_revision('c7d8e9f0a1b2')
    evidence_head_revision = script.get_revision('d1e2f3a4b5c6')

    assert chunking_revision is not None
    assert bridge_revision is not None
    assert automation_revision is not None
    assert tasks_revision is not None
    assert compatibility_revision is not None
    assert pinned_note_revision is not None
    assert stale_head_revision is not None
    assert legacy_pk_revision is not None
    assert retrieval_chunk_revision is not None
    assert head_revision is not None
    assert evidence_head_revision is not None
    assert chunking_revision.down_revision == 'c3d4e5f6a7b8'
    assert bridge_revision.down_revision == 'd4e5f6a7b8c9'
    assert tasks_revision.down_revision == 'f0a1b2c3d4e5'
    assert automation_revision.down_revision == 'a3dd5bedd151'
    assert compatibility_revision.down_revision == '56359461a091'
    assert pinned_note_revision.down_revision == 'e8c4b9a2d1f0'
    assert stale_head_revision.down_revision == '4de81c2a3af1'
    assert legacy_pk_revision.down_revision == '3c9b0ca343fd'
    assert retrieval_chunk_revision.down_revision == '461111b60977'
    assert head_revision.down_revision == 'b6f7c8d9e0a1'
    assert evidence_head_revision.down_revision == 'c7d8e9f0a1b2'


def test_migration_revision_ids_fit_default_alembic_version_column():
    script = _load_script_directory()

    for revision in script.walk_revisions():
        assert len(revision.revision) <= 32, revision.revision
