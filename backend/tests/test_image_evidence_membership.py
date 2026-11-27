from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from open_webui.models.evidence import KnowledgeEvidence as Evidence
from open_webui.models.files import File
from open_webui.models.knowledge import KnowledgeFile
from open_webui.models.retrieval_indexes import RetrievalIndexState as State
from open_webui.retrieval.evidence import EvidenceService, ResolvedScope


def test_file_image_follows_current_knowledge_membership_without_exposing_old_file_text():
    engine = create_engine('sqlite://')
    for model in (File, KnowledgeFile, State, Evidence):
        model.__table__.create(engine)
    with Session(engine) as db:
        db.add(File(id='doc', user_id='owner', filename='scan.pdf'))
        db.add(State(state_id='file_doc', index_kind='map_text', file_id='doc', file_version=1,
                     status='ready', created_at=1, updated_at=1))
        db.add(KnowledgeFile(id='link', knowledge_id='kb', file_id='doc', user_id='owner', created_at=1, updated_at=1))
        for kind in ('image', 'text'):
            db.add(Evidence(id=kind, evidence_ref=kind, file_id='doc', file_version=1, modality=kind, evidence_kind=kind,
                            source_name='scan.pdf', content_hash=kind, is_active=True, created_at=1, updated_at=1))
        db.flush()
        def visible(kb):
            scope = ResolvedScope((kb,), ())
            return list(db.scalars(select(Evidence.evidence_ref).where(EvidenceService()._visible(scope))))
        assert visible('kb') == ['image']
        assert visible('unrelated') == []
        db.execute(delete(KnowledgeFile).where(KnowledgeFile.id == 'link'))
        assert visible('kb') == []


def test_kb_rebuild_indexes_stored_text_when_legacy_mime_is_null(monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from open_webui.routers import retrieval

    db = AsyncMock()
    owner = SimpleNamespace(id='owner', role='user')
    file = SimpleNamespace(id='doc', user_id='owner', filename='old-upload', meta={'content_type': None},
                           data={'content': 'An archived passage.'})
    monkeypatch.setattr(retrieval.Config, 'get_many', AsyncMock(return_value={'rag.bypass_embedding_and_retrieval': False}))
    monkeypatch.setattr(retrieval.Files, 'get_file_by_id', AsyncMock(return_value=file))
    for method in ('update_file_data_by_id', 'update_file_hash_by_id', 'update_file_metadata_by_id'):
        monkeypatch.setattr(retrieval.Files, method, AsyncMock())
    monkeypatch.setattr(retrieval, '_validate_collection_access', AsyncMock())
    monkeypatch.setattr(retrieval.ASYNC_VECTOR_DB_CLIENT, 'query', AsyncMock(return_value=None))
    monkeypatch.setattr(retrieval, 'publish_event', AsyncMock())
    save = AsyncMock(return_value=True)
    monkeypatch.setattr(retrieval, 'save_docs_to_vector_db', save)

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(retrieval, 'get_async_db', session)
    result = asyncio.run(retrieval.index_file(
        SimpleNamespace(), retrieval.ProcessFileForm(file_id='doc', collection_name='kb'), owner, db, rebuild_text=True
    ))
    assert result['content'] == 'An archived passage.'
    assert {call.kwargs['collection_name'] for call in save.await_args_list} == {'file-doc', 'kb'}
    assert all(call.kwargs['docs'][0].page_content == result['content'] for call in save.await_args_list)
