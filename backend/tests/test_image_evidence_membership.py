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
