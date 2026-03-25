import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")

from open_webui.internal.db import Base
from open_webui.models.files import File
from open_webui.models.knowledge import Knowledge, KnowledgeFile
import open_webui.models.knowledge_layers as layers_mod
from open_webui.models.knowledge_layers import (
    KnowledgeFileLayer,
    KnowledgeFileLayerUpsertForm,
    KnowledgeLayers,
)


@contextmanager
def _yield_session(session):
    yield session


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            Knowledge.__table__,
            File.__table__,
            KnowledgeFile.__table__,
            KnowledgeFileLayer.__table__,
        ],
    )
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    return session_factory()


def test_upsert_layer_updates_existing_row(monkeypatch):
    session = _make_session()
    monkeypatch.setattr(
        layers_mod, "get_db_context", lambda db=None: _yield_session(session)
    )

    KnowledgeLayers.upsert_layer(
        KnowledgeFileLayerUpsertForm(
            knowledge_id="kb-1",
            file_id="file-1",
            layer_type="abstract",
            content="first",
            status="pending",
            source_system="open_notebook",
        ),
        db=session,
    )

    KnowledgeLayers.upsert_layer(
        KnowledgeFileLayerUpsertForm(
            knowledge_id="kb-1",
            file_id="file-1",
            layer_type="abstract",
            content="updated",
            status="ready",
            source_system="open_notebook",
        ),
        db=session,
    )

    rows = (
        session.query(KnowledgeFileLayer)
        .filter_by(knowledge_id="kb-1", file_id="file-1", layer_type="abstract")
        .all()
    )
    assert len(rows) == 1
    assert rows[0].status == "ready"
    assert rows[0].content == "updated"


def test_mark_layers_stale_for_file(monkeypatch):
    session = _make_session()
    monkeypatch.setattr(
        layers_mod, "get_db_context", lambda db=None: _yield_session(session)
    )

    KnowledgeLayers.upsert_layer(
        KnowledgeFileLayerUpsertForm(
            knowledge_id="kb-1",
            file_id="file-1",
            layer_type="key_findings",
            content="finding",
            status="ready",
            source_system="open_notebook",
        ),
        db=session,
    )

    KnowledgeLayers.mark_layers_stale_for_file("kb-1", "file-1", db=session)

    row = (
        session.query(KnowledgeFileLayer)
        .filter_by(knowledge_id="kb-1", file_id="file-1", layer_type="key_findings")
        .one()
    )
    assert row.status == "stale"


def test_upsert_layer_supports_chunked_identity_and_metadata(monkeypatch):
    session = _make_session()
    monkeypatch.setattr(
        layers_mod, "get_db_context", lambda db=None: _yield_session(session)
    )

    KnowledgeLayers.upsert_layer(
        KnowledgeFileLayerUpsertForm(
            knowledge_id="kb-1",
            file_id="file-1",
            layer_type="abstract",
            content="part 1",
            status="ready",
            source_system="open_notebook",
            part_index=1,
            part_total=2,
            display_title="Abstract 1/2",
        ),
        db=session,
    )
    KnowledgeLayers.upsert_layer(
        KnowledgeFileLayerUpsertForm(
            knowledge_id="kb-1",
            file_id="file-1",
            layer_type="abstract",
            content="part 2",
            status="ready",
            source_system="open_notebook",
            part_index=2,
            part_total=2,
            display_title="Abstract 2/2",
        ),
        db=session,
    )

    rows = (
        session.query(KnowledgeFileLayer)
        .filter_by(knowledge_id="kb-1", file_id="file-1", layer_type="abstract")
        .order_by(KnowledgeFileLayer.part_index.asc())
        .all()
    )
    assert len(rows) == 2
    assert rows[0].part_index == 1
    assert rows[1].part_index == 2

    returned = KnowledgeLayers.get_layers_by_file("kb-1", "file-1", db=session)
    assert len(returned) == 2
    assert returned[0].part_index == 1
    assert returned[0].part_total == 2
    assert returned[0].display_title == "Abstract 1/2"
