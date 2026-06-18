import importlib
import os
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("WEBUI_SECRET_KEY", "test-secret")
os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")
os.environ.setdefault("DATABASE_ENABLE_SESSION_SHARING", "true")

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import BigInteger, Integer, Text, create_engine, inspect
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
MIGRATIONS_DIR = os.path.join(REPO_ROOT, "backend", "open_webui", "migrations")
AGENT_MEMORY_REVISION_ID = "f3a4b5c7d8e9"
AGENT_MEMORY_MIGRATION_MODULE = (
    f"open_webui.migrations.versions.{AGENT_MEMORY_REVISION_ID}_add_agent_memory_foundation"
)

EXPECTED_AGENT_MEMORY_TABLES = {
    "agent_memory_extraction_cache": {
        "columns": [
            "user_id",
            "chat_id",
            "source_updated_at",
            "raw_memory",
            "rollout_summary",
            "rollout_slug",
            "generated_at",
            "status",
        ],
        "primary_key": ["user_id", "chat_id"],
    },
    "agent_memory_extraction_job": {
        "columns": [
            "user_id",
            "chat_id",
            "status",
            "lease_until",
            "retry_at",
            "retry_count",
            "last_error",
            "updated_at",
        ],
        "primary_key": ["user_id", "chat_id"],
    },
    "agent_memory_consolidation_job": {
        "columns": [
            "user_id",
            "scope_type",
            "scope_id",
            "status",
            "lease_until",
            "retry_at",
            "retry_count",
            "last_error",
            "input_hash",
            "updated_at",
        ],
        "primary_key": ["user_id", "scope_type", "scope_id"],
    },
    "agent_memory_artifact": {
        "columns": [
            "user_id",
            "scope_type",
            "scope_id",
            "path",
            "content",
            "input_hash",
            "revision",
            "note_id",
            "note_content_hash",
            "updated_at",
        ],
        "primary_key": ["user_id", "scope_type", "scope_id", "path"],
    },
}

EXPECTED_AGENT_MEMORY_INDEXES = {
    "agent_memory_extraction_job": {
        "ix_agent_memory_extraction_job_claim": [
            "status",
            "retry_at",
            "lease_until",
            "updated_at",
            "chat_id",
        ],
    },
    "agent_memory_consolidation_job": {
        "ix_agent_memory_consolidation_job_claim": [
            "status",
            "retry_at",
            "lease_until",
            "updated_at",
            "scope_type",
            "scope_id",
        ],
    },
}

EXPECTED_AGENT_MEMORY_ADMIN_CONFIG_KEYS = {
    "ENABLE_AGENT_MEMORY",
    "AGENT_MEMORY_EXTRACTION_MODEL",
    "AGENT_MEMORY_CONSOLIDATION_MODEL",
    "AGENT_MEMORY_IDLE_THRESHOLD_SECONDS",
    "AGENT_MEMORY_STARTUP_CLAIM_LIMIT",
    "AGENT_MEMORY_EXTRACTION_CLAIM_LIMIT",
    "AGENT_MEMORY_CONSOLIDATION_CLAIM_LIMIT",
    "AGENT_MEMORY_LEASE_SECONDS",
    "AGENT_MEMORY_RETRY_BACKOFF_SECONDS",
    "AGENT_MEMORY_SUMMARY_TOKEN_BUDGET",
}


def _load_script_directory() -> ScriptDirectory:
    config = Config()
    config.set_main_option("script_location", MIGRATIONS_DIR)
    return ScriptDirectory.from_config(config)


def _agent_memory_migration():
    return importlib.import_module(AGENT_MEMORY_MIGRATION_MODULE)


def _run_migration(engine, direction):
    migration = _agent_memory_migration()
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        with patch.object(migration, "op", operations):
            getattr(migration, direction)()


def test_agent_memory_migration_revision_is_registered():
    script = _load_script_directory()

    revision = script.get_revision(AGENT_MEMORY_REVISION_ID)

    assert revision is not None
    assert revision.down_revision == "e2f3a4b5c7"
    current_head = script.get_current_head()
    head_lineage = tuple(script.iterate_revisions(current_head, revision.down_revision))
    assert AGENT_MEMORY_REVISION_ID in {ancestor.revision for ancestor in head_lineage}


def test_agent_memory_migration_upgrade_and_downgrade_create_minimal_tables():
    engine = create_engine("sqlite:///:memory:")

    _run_migration(engine, "upgrade")
    _run_migration(engine, "upgrade")

    inspector = inspect(engine)
    assert set(EXPECTED_AGENT_MEMORY_TABLES) <= set(inspector.get_table_names())
    for table_name, expected in EXPECTED_AGENT_MEMORY_TABLES.items():
        assert [column["name"] for column in inspector.get_columns(table_name)] == expected["columns"]
        assert inspector.get_pk_constraint(table_name)["constrained_columns"] == expected["primary_key"]
    for table_name, expected_indexes in EXPECTED_AGENT_MEMORY_INDEXES.items():
        indexes_by_name = {index["name"]: index["column_names"] for index in inspector.get_indexes(table_name)}
        for index_name, expected_columns in expected_indexes.items():
            assert indexes_by_name[index_name] == expected_columns

    _run_migration(engine, "downgrade")
    _run_migration(engine, "downgrade")

    inspector = inspect(engine)
    for table_name in EXPECTED_AGENT_MEMORY_TABLES:
        assert table_name not in inspector.get_table_names()


def test_agent_memory_table_metadata_declares_exact_columns():
    agent_memories = importlib.import_module("open_webui.models.agent_memories")

    expected_model_tables = {
        agent_memories.AgentMemoryExtractionCache: EXPECTED_AGENT_MEMORY_TABLES["agent_memory_extraction_cache"],
        agent_memories.AgentMemoryExtractionJob: EXPECTED_AGENT_MEMORY_TABLES["agent_memory_extraction_job"],
        agent_memories.AgentMemoryConsolidationJob: EXPECTED_AGENT_MEMORY_TABLES[
            "agent_memory_consolidation_job"
        ],
        agent_memories.AgentMemoryArtifact: EXPECTED_AGENT_MEMORY_TABLES["agent_memory_artifact"],
    }

    for model, expected in expected_model_tables.items():
        table = model.__table__
        assert list(table.columns.keys()) == expected["columns"]
        assert [column.name for column in table.primary_key.columns] == expected["primary_key"]

    assert isinstance(agent_memories.AgentMemoryExtractionCache.__table__.c.raw_memory.type, Text)
    assert isinstance(agent_memories.AgentMemoryExtractionCache.__table__.c.generated_at.type, BigInteger)
    assert isinstance(agent_memories.AgentMemoryExtractionJob.__table__.c.retry_count.type, Integer)
    assert isinstance(agent_memories.AgentMemoryConsolidationJob.__table__.c.input_hash.type, Text)
    assert isinstance(agent_memories.AgentMemoryArtifact.__table__.c.revision.type, Integer)
    expected_model_indexes = {
        agent_memories.AgentMemoryExtractionJob: EXPECTED_AGENT_MEMORY_INDEXES[
            "agent_memory_extraction_job"
        ],
        agent_memories.AgentMemoryConsolidationJob: EXPECTED_AGENT_MEMORY_INDEXES[
            "agent_memory_consolidation_job"
        ],
    }
    for model, expected_indexes in expected_model_indexes.items():
        indexes_by_name = {
            index.name: [column.name for column in index.columns]
            for index in model.__table__.indexes
        }
        assert indexes_by_name == expected_indexes
    assert agent_memories.EXTRACTION_CACHE_STATUSES == {"succeeded", "succeeded_no_output", "stale"}
    assert agent_memories.EXTRACTION_JOB_STATUSES == {"queued", "leased", "retry", "failed"}
    assert agent_memories.CONSOLIDATION_JOB_STATUSES == {"queued", "leased", "retry", "failed"}
    assert agent_memories.AGENT_MEMORY_SCOPE_TYPES == {"global", "folder"}
    assert agent_memories.AGENT_MEMORY_ARTIFACT_PATHS == {"memory_summary.md", "MEMORY.md"}


@pytest.mark.asyncio
async def test_agent_memory_table_api_round_trip_rows(tmp_path):
    agent_memories = importlib.import_module("open_webui.models.agent_memories")
    db_path = tmp_path / "agent-memory.db"
    sync_engine = create_engine(f"sqlite:///{db_path}")
    _run_migration(sync_engine, "upgrade")

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        cache = await agent_memories.AgentMemoryExtractionCaches.upsert_cache(
            user_id="user-1",
            chat_id="chat-1",
            source_updated_at=10,
            raw_memory="raw",
            rollout_summary="summary",
            rollout_slug="summary-slug",
            generated_at=11,
            status="succeeded",
            db=session,
        )
        extraction_job = await agent_memories.AgentMemoryExtractionJobs.upsert_job(
            user_id="user-1",
            chat_id="chat-1",
            status="queued",
            lease_until=None,
            retry_at=None,
            retry_count=0,
            last_error=None,
            updated_at=12,
            db=session,
        )
        consolidation_job = await agent_memories.AgentMemoryConsolidationJobs.upsert_job(
            user_id="user-1",
            scope_type="global",
            scope_id="",
            status="queued",
            lease_until=None,
            retry_at=None,
            retry_count=0,
            last_error=None,
            input_hash="input-a",
            updated_at=13,
            db=session,
        )
        artifact = await agent_memories.AgentMemoryArtifacts.upsert_artifact(
            user_id="user-1",
            scope_type="global",
            scope_id="",
            path="memory_summary.md",
            content="# Summary",
            input_hash="input-a",
            revision=1,
            note_id=None,
            note_content_hash=None,
            updated_at=14,
            db=session,
        )

        assert cache.raw_memory == "raw"
        assert extraction_job.status == "queued"
        assert consolidation_job.scope_id == ""
        assert artifact.content == "# Summary"
        assert (await agent_memories.AgentMemoryExtractionCaches.get_cache("user-1", "chat-1", db=session)).status == (
            "succeeded"
        )
        assert (await agent_memories.AgentMemoryExtractionJobs.get_job("user-1", "chat-1", db=session)).retry_count == 0
        assert (
            await agent_memories.AgentMemoryConsolidationJobs.get_job("user-1", "global", "", db=session)
        ).input_hash == "input-a"
        assert (
            await agent_memories.AgentMemoryArtifacts.get_artifact(
                "user-1", "global", "", "memory_summary.md", db=session
            )
        ).revision == 1

        assert await agent_memories.AgentMemoryExtractionCaches.delete_cache("user-1", "chat-1", db=session)
        assert await agent_memories.AgentMemoryExtractionJobs.delete_job("user-1", "chat-1", db=session)
        assert await agent_memories.AgentMemoryConsolidationJobs.delete_job("user-1", "global", "", db=session)
        assert await agent_memories.AgentMemoryArtifacts.delete_artifact(
            "user-1", "global", "", "memory_summary.md", db=session
        )

        assert await agent_memories.AgentMemoryExtractionCaches.get_cache("user-1", "chat-1", db=session) is None
        assert await agent_memories.AgentMemoryExtractionJobs.get_job("user-1", "chat-1", db=session) is None
        assert await agent_memories.AgentMemoryConsolidationJobs.get_job("user-1", "global", "", db=session) is None
        assert (
            await agent_memories.AgentMemoryArtifacts.get_artifact(
                "user-1", "global", "", "memory_summary.md", db=session
            )
            is None
        )

    await engine.dispose()


@pytest.mark.asyncio
async def test_agent_memory_admin_config_disables_feature_when_global_switch_is_off():
    auths = importlib.import_module("open_webui.routers.auths")
    config_values = {
        "SHOW_ADMIN_DETAILS": False,
        "ADMIN_EMAIL": None,
        "WEBUI_URL": "http://test",
        "ENABLE_SIGNUP": False,
        "ENABLE_API_KEYS": False,
        "ENABLE_API_KEYS_ENDPOINT_RESTRICTIONS": False,
        "API_KEYS_ALLOWED_ENDPOINTS": "",
        "DEFAULT_USER_ROLE": "user",
        "DEFAULT_GROUP_ID": "",
        "JWT_EXPIRES_IN": "-1",
        "ENABLE_COMMUNITY_SHARING": False,
        "ENABLE_MESSAGE_RATING": True,
        "ENABLE_FOLDERS": True,
        "FOLDER_MAX_FILE_COUNT": "",
        "AUTOMATION_MAX_COUNT": "",
        "AUTOMATION_MIN_INTERVAL": "",
        "ENABLE_AUTOMATIONS": False,
        "ENABLE_CHANNELS": True,
        "ENABLE_CALENDAR": True,
        "ENABLE_MEMORIES": True,
        "ENABLE_NOTES": True,
        "ENABLE_USER_WEBHOOKS": False,
        "ENABLE_USER_STATUS": False,
        "PENDING_USER_OVERLAY_TITLE": "",
        "PENDING_USER_OVERLAY_CONTENT": "",
        "RESPONSE_WATERMARK": "",
        "ENABLE_AGENT_MEMORY": False,
        "AGENT_MEMORY_EXTRACTION_MODEL": "",
        "AGENT_MEMORY_CONSOLIDATION_MODEL": "",
        "AGENT_MEMORY_IDLE_THRESHOLD_SECONDS": 900,
        "AGENT_MEMORY_STARTUP_CLAIM_LIMIT": 0,
        "AGENT_MEMORY_EXTRACTION_CLAIM_LIMIT": 5,
        "AGENT_MEMORY_CONSOLIDATION_CLAIM_LIMIT": 2,
        "AGENT_MEMORY_LEASE_SECONDS": 300,
        "AGENT_MEMORY_RETRY_BACKOFF_SECONDS": 600,
        "AGENT_MEMORY_SUMMARY_TOKEN_BUDGET": 1200,
    }
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=SimpleNamespace(**config_values))))

    response = await auths.get_admin_config(request, user=SimpleNamespace(id="admin"))

    assert response["ENABLE_AGENT_MEMORY"] is False
    assert EXPECTED_AGENT_MEMORY_ADMIN_CONFIG_KEYS <= set(response)
    assert response["AGENT_MEMORY_EXTRACTION_MODEL"] == ""
    assert response["AGENT_MEMORY_CONSOLIDATION_MODEL"] == ""
    assert response["AGENT_MEMORY_STARTUP_CLAIM_LIMIT"] == 0


@pytest.mark.asyncio
async def test_agent_memory_admin_config_preserves_blank_numeric_settings_on_save():
    auths = importlib.import_module("open_webui.routers.auths")
    config_values = {
        "SHOW_ADMIN_DETAILS": False,
        "ADMIN_EMAIL": None,
        "WEBUI_URL": "http://test",
        "ENABLE_SIGNUP": False,
        "ENABLE_API_KEYS": False,
        "ENABLE_API_KEYS_ENDPOINT_RESTRICTIONS": False,
        "API_KEYS_ALLOWED_ENDPOINTS": "",
        "DEFAULT_USER_ROLE": "user",
        "DEFAULT_GROUP_ID": "",
        "JWT_EXPIRES_IN": "-1",
        "ENABLE_COMMUNITY_SHARING": False,
        "ENABLE_MESSAGE_RATING": True,
        "ENABLE_FOLDERS": True,
        "FOLDER_MAX_FILE_COUNT": "",
        "AUTOMATION_MAX_COUNT": "",
        "AUTOMATION_MIN_INTERVAL": "",
        "ENABLE_AUTOMATIONS": False,
        "ENABLE_CHANNELS": True,
        "ENABLE_CALENDAR": True,
        "ENABLE_MEMORIES": True,
        "ENABLE_NOTES": True,
        "ENABLE_USER_WEBHOOKS": False,
        "ENABLE_USER_STATUS": False,
        "PENDING_USER_OVERLAY_TITLE": "",
        "PENDING_USER_OVERLAY_CONTENT": "",
        "RESPONSE_WATERMARK": "",
        "ENABLE_AGENT_MEMORY": False,
        "AGENT_MEMORY_EXTRACTION_MODEL": "extractor",
        "AGENT_MEMORY_CONSOLIDATION_MODEL": "consolidator",
        "AGENT_MEMORY_IDLE_THRESHOLD_SECONDS": 900,
        "AGENT_MEMORY_STARTUP_CLAIM_LIMIT": 5,
        "AGENT_MEMORY_EXTRACTION_CLAIM_LIMIT": 5,
        "AGENT_MEMORY_CONSOLIDATION_CLAIM_LIMIT": 2,
        "AGENT_MEMORY_LEASE_SECONDS": 300,
        "AGENT_MEMORY_RETRY_BACKOFF_SECONDS": 600,
        "AGENT_MEMORY_SUMMARY_TOKEN_BUDGET": 1200,
    }
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=SimpleNamespace(**config_values))))
    form_data = auths.AdminConfig(**{
        **config_values,
        "AGENT_MEMORY_IDLE_THRESHOLD_SECONDS": "",
        "AGENT_MEMORY_STARTUP_CLAIM_LIMIT": "",
        "AGENT_MEMORY_EXTRACTION_CLAIM_LIMIT": "",
        "AGENT_MEMORY_CONSOLIDATION_CLAIM_LIMIT": "",
        "AGENT_MEMORY_LEASE_SECONDS": "",
        "AGENT_MEMORY_RETRY_BACKOFF_SECONDS": "",
        "AGENT_MEMORY_SUMMARY_TOKEN_BUDGET": "",
    })

    response = await auths.update_admin_config(request, form_data, user=SimpleNamespace(id="admin"))

    assert response["AGENT_MEMORY_IDLE_THRESHOLD_SECONDS"] == ""
    assert response["AGENT_MEMORY_STARTUP_CLAIM_LIMIT"] == ""
    assert response["AGENT_MEMORY_EXTRACTION_CLAIM_LIMIT"] == ""
    assert response["AGENT_MEMORY_CONSOLIDATION_CLAIM_LIMIT"] == ""
    assert response["AGENT_MEMORY_LEASE_SECONDS"] == ""
    assert response["AGENT_MEMORY_RETRY_BACKOFF_SECONDS"] == ""
    assert response["AGENT_MEMORY_SUMMARY_TOKEN_BUDGET"] == ""


def test_agent_memory_features_are_independent_from_memories():
    backend_config = importlib.import_module("open_webui.config")

    features = backend_config.DEFAULT_USER_PERMISSIONS["features"]

    assert features["memories"] is True
    assert features["agent_memory"] is False
    assert "agent_memory" in features

    frontend_permissions = open(os.path.join(REPO_ROOT, "src/lib/constants/permissions.ts")).read()
    assert "memories: true" in frontend_permissions
    assert "agent_memory: false" in frontend_permissions


def test_chat_agent_memory_opt_out_helper_preserves_unrelated_meta_keys():
    chats = importlib.import_module("open_webui.models.chats")
    meta = {
        "tags": ["project-a"],
        "agent_memory": {"note": "keep"},
        "ui": {"expanded": True},
    }

    disabled = chats.set_agent_memory_disabled(meta, True)
    enabled = chats.set_agent_memory_disabled(disabled, False)

    assert disabled == {
        "tags": ["project-a"],
        "agent_memory": {"note": "keep", "disabled": True},
        "ui": {"expanded": True},
    }
    assert enabled == {
        "tags": ["project-a"],
        "agent_memory": {"note": "keep"},
        "ui": {"expanded": True},
    }
    assert meta["agent_memory"] == {"note": "keep"}


def test_folder_agent_memory_opt_out_helper_preserves_unrelated_meta_keys():
    folders = importlib.import_module("open_webui.models.folders")
    meta = {
        "icon": "folder",
        "agent_memory": {"note": "keep"},
        "layout": {"sort": "updated"},
    }

    disabled = folders.set_agent_memory_disabled(meta, True)
    enabled = folders.set_agent_memory_disabled(disabled, False)

    assert disabled == {
        "icon": "folder",
        "agent_memory": {"note": "keep", "disabled": True},
        "layout": {"sort": "updated"},
    }
    assert enabled == {
        "icon": "folder",
        "agent_memory": {"note": "keep"},
        "layout": {"sort": "updated"},
    }
    assert meta["agent_memory"] == {"note": "keep"}
