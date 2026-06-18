import os
import types
from contextlib import asynccontextmanager
from pathlib import Path

os.environ.setdefault("WEBUI_SECRET_KEY", "test-secret")
os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")
os.environ.setdefault("DATABASE_ENABLE_SESSION_SHARING", "true")

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from open_webui.migrations.versions import d1e2f3a4b5c6_add_multimodal_evidence_schema as evidence_migration
from open_webui.models.evidence import (
    KnowledgeEvidence,
    KnowledgeEvidenceAsset,
    KnowledgeEvidenceAssets,
    KnowledgeEvidenceEmbeddings,
    KnowledgeEvidences,
    KnowledgeVectorSpaces,
)
from open_webui.models.files import File
from open_webui.models.knowledge import Knowledge
from open_webui.retrieval.evidence import (
    EvidenceToolError,
    normalize_query_knowledge_evidence_args,
    search_multimodal_evidence,
)
from open_webui.retrieval.vector import multimodal as multimodal_mod
from open_webui.retrieval.vector.multimodal import MultimodalVectorSpaceError, upsert_multimodal_evidence_embedding


class _FakeVectorClient:
    def __init__(self) -> None:
        self.search_calls: list[dict[str, object]] = []
        self.upsert_calls: list[dict[str, object]] = []

    async def search(self, collection_name, vectors, filter=None, limit=10):
        vector = [round(float(value), 3) for value in vectors[0][:3]]
        self.search_calls.append(
            {
                "collection_name": collection_name,
                "vector": vector,
                "filter": filter,
                "limit": limit,
            }
        )
        if vector == [1.0, 0.0, 0.0]:
            return _filtered_vector_result(
                [
                    (
                        "vec-text",
                        {
                            "evidence_ref": "ke:kb-1:file-text:text_chunk:1:txt",
                            "vector_space_id": "vs-1",
                            "modality": "text",
                        },
                        0.11,
                    )
                ],
                filter,
            )
        if vector == [0.0, 1.0, 0.0]:
            return _filtered_vector_result(
                [
                    (
                        "vec-image",
                        {
                            "evidence_ref": "ke:kb-1:file-img:standalone_image:1:img",
                            "vector_space_id": "vs-1",
                            "modality": "image",
                        },
                        0.21,
                    )
                ],
                filter,
            )
        return _filtered_vector_result(
            [
                (
                    "vec-mixed-image",
                    {
                        "evidence_ref": "ke:kb-1:file-img:standalone_image:1:img",
                        "vector_space_id": "vs-1",
                        "modality": "image",
                    },
                    0.08,
                ),
                (
                    "vec-mixed-text",
                    {
                        "evidence_ref": "ke:kb-1:file-text:text_chunk:1:txt",
                        "vector_space_id": "vs-1",
                        "modality": "text",
                    },
                    0.19,
                ),
            ],
            filter,
        )

    async def upsert(self, collection_name, items):
        self.upsert_calls.append(
            {
                "collection_name": collection_name,
                "items": items,
            }
        )
        return None


class _FakeRequest:
    def __init__(self, **state) -> None:
        self.app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                config=types.SimpleNamespace(TOP_K_RERANKER=2),
                **state,
            )
        )


def _filtered_vector_result(rows, filter=None):
    requested = None
    if isinstance(filter, dict):
        modality_filter = filter.get("modality")
        if isinstance(modality_filter, dict) and isinstance(modality_filter.get("$in"), list):
            requested = set(modality_filter["$in"])
        elif isinstance(modality_filter, str):
            requested = {modality_filter}
    filtered = [row for row in rows if requested is None or row[1].get("modality") in requested]
    return types.SimpleNamespace(
        ids=[[row[0] for row in filtered]],
        metadatas=[[row[1] for row in filtered]],
        distances=[[row[2] for row in filtered]],
    )


def _run_migration(engine, direction):
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(evidence_migration, "op", operations)
            getattr(evidence_migration, direction)()


@asynccontextmanager
async def _db_session_ctx(tmp_path: Path):
    db_path = tmp_path / "evidence.db"
    sync_engine = create_engine(f"sqlite:///{db_path}")
    _run_migration(sync_engine, "upgrade")

    with sync_engine.begin() as connection:
        Knowledge.__table__.create(connection, checkfirst=True)
        File.__table__.create(connection, checkfirst=True)
        KnowledgeEvidenceAsset.__table__.create(connection, checkfirst=True)
        KnowledgeEvidence.__table__.create(connection, checkfirst=True)

    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = sessionmaker(async_engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with session_factory() as session:
            yield session
    finally:
        await async_engine.dispose()


async def _seed_knowledge_and_file(session: AsyncSession, *, file_id: str, filename: str, content_type: str, path: str):
    session.add(
        Knowledge(
            id="kb-1",
            user_id="user-1",
            name="Knowledge",
            description="",
            meta={"evidence_mode": "evidence"},
            created_at=1,
            updated_at=1,
        )
    )
    session.add(
        File(
            id=file_id,
            user_id="user-1",
            hash=f"{file_id}-hash",
            filename=filename,
            path=path,
            data={"status": "completed"},
            meta={"content_type": content_type, "name": filename},
            created_at=1,
            updated_at=1,
        )
    )
    await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query_kwargs, expected_vectors, expected_refs, expected_filters",
    [
        (
            {
                "query_text": "find the capsid fold",
                "knowledge_ids": ["kb-1"],
                "count": 4,
            },
            [[1.0, 0.0, 0.0]],
            ["ke:kb-1:file-text:text_chunk:1:txt"],
            [{"modality": {"$in": ["text"]}}],
        ),
        (
            {
                "query_image_refs": ["chat:file:query-image"],
                "knowledge_ids": ["kb-1"],
                "count": 4,
            },
            [[0.0, 1.0, 0.0]],
            ["ke:kb-1:file-img:standalone_image:1:img"],
            [{"modality": {"$in": ["image"]}}],
        ),
        (
            {
                "query_text": "find the figure and fold",
                "visual_query": "ring-like capsid particles figure",
                "query_image_refs": ["chat:file:query-image"],
                "knowledge_ids": ["kb-1"],
                "count": 4,
            },
            [[2.0, 2.0, 0.0], [1.0, 0.0, 0.0]],
            [
                "ke:kb-1:file-img:standalone_image:1:img",
                "ke:kb-1:file-text:text_chunk:1:txt",
            ],
            [{"modality": {"$in": ["image"]}}, {"modality": {"$in": ["text"]}}],
        ),
        (
            {
                "query_text": "find the figure and fold",
                "query_image_refs": ["chat:file:query-image"],
                "knowledge_ids": ["kb-1"],
                "modalities": ["image"],
                "count": 4,
            },
            [[0.0, 1.0, 0.0]],
            [
                "ke:kb-1:file-img:standalone_image:1:img",
            ],
            [{"modality": {"$in": ["image"]}}],
        ),
    ],
)
async def test_search_multimodal_evidence_uses_query_embeddings_and_hydrates_evidence_refs(
    tmp_path,
    monkeypatch,
    query_kwargs,
    expected_vectors,
    expected_refs,
    expected_filters,
):
    query_image_path = tmp_path / "query.png"
    query_image_bytes = b"\x89PNG\r\n\x1a\nquery-image"
    query_image_path.write_bytes(query_image_bytes)

    async with _db_session_ctx(tmp_path) as session:
        await _seed_knowledge_and_file(
            session,
            file_id="file-text",
            filename="paper.pdf",
            content_type="application/pdf",
            path="/tmp/paper.pdf",
        )
        session.add(
            File(
                id="file-img",
                user_id="user-1",
                hash="file-img-hash",
                filename="figure.png",
                path="/tmp/figure.png",
                data={"status": "completed"},
                meta={"content_type": "image/png", "name": "figure.png"},
                created_at=1,
                updated_at=1,
            )
        )
        query_file = File(
            id="query-image",
            user_id="user-1",
            hash="query-image-hash",
            filename="query.png",
            path=str(query_image_path),
            data={"status": "completed"},
            meta={"content_type": "image/png", "name": "query.png"},
            created_at=1,
            updated_at=1,
        )
        session.add(query_file)
        await session.commit()

        async def fake_get_file_by_id(file_id, db=None):
            if file_id == "query-image":
                return multimodal_mod.FileModel.model_validate(query_file)
            return None

        monkeypatch.setattr(multimodal_mod.Files, "get_file_by_id", fake_get_file_by_id)
        monkeypatch.setattr(multimodal_mod.Storage, "get_file", lambda storage_uri: storage_uri)

        text_evidence = await KnowledgeEvidences.create_evidence(
            knowledge_id="kb-1",
            file_id="file-text",
            modality="text",
            evidence_kind="text_chunk",
            content_hash="hash-text",
            projection_profile="unified_multimodal_dense",
            projection_config_hash="profile-hash",
            chunk_index=1,
            chunk_total=1,
            source_name="paper.pdf",
            content_text="The capsid shell has a conserved HK97-like fold.",
            preview_text="Conserved HK97-like fold.",
            title="Text finding",
            retrieval_chunk_uid="chunk-1",
            retrieval_chunk_row_id=1,
            evidence_ref="ke:kb-1:file-text:text_chunk:1:txt",
            db=session,
        )
        image_asset = await KnowledgeEvidenceAssets.create_asset(
            knowledge_id="kb-1",
            file_id="file-img",
            asset_kind="standalone_image",
            mime_type="image/png",
            storage_uri="/tmp/figure.png",
            sha256="sha-image",
            status="ready",
            db=session,
        )
        image_evidence = await KnowledgeEvidences.create_evidence(
            knowledge_id="kb-1",
            file_id="file-img",
            asset_id=image_asset.id,
            modality="image",
            evidence_kind="standalone_image",
            content_hash="hash-image",
            projection_profile="unified_multimodal_dense",
            projection_config_hash="profile-hash",
            chunk_index=1,
            chunk_total=1,
            source_name="figure.png",
            content_text="A microscopy panel with ring-like capsid particles.",
            preview_text="Ring-like capsid particles.",
            title="Gel image",
            evidence_ref="ke:kb-1:file-img:standalone_image:1:img",
            db=session,
        )

        vector_space = await KnowledgeVectorSpaces.create_vector_space(
            knowledge_id="kb-1",
            retrieval_profile="unified_multimodal_dense",
            embedding_model="fake-multimodal-embed",
            projection_config_hash="profile-hash",
            embedding_dim=3,
            distance_metric="cosine",
            vector_backend="pgvector",
            supports_text_query=True,
            supports_image_query=True,
            supports_text_evidence=True,
            supports_image_evidence=True,
            active=True,
            db=session,
        )

        embed_calls: list[object] = []

        async def fake_embedding(query, prefix=None, user=None):
            embed_calls.append(query)
            if isinstance(query, dict):
                query_images = query.get("query_images") or []
                if query_images:
                    assert query_images[0]["ref"] == "chat:file:query-image"
                    assert query_images[0]["file_id"] == "query-image"
                    assert query_images[0]["mime_type"] == "image/png"
                    assert query_images[0]["image_bytes"] == query_image_bytes
                assert "query_image_refs" not in query
                if query_images and query.get("query_text"):
                    return [2.0, 2.0, 0.0]
                if query_images:
                    return [0.0, 1.0, 0.0]
            return [1.0, 0.0, 0.0]

        vector_client = _FakeVectorClient()

        query = normalize_query_knowledge_evidence_args(**query_kwargs)
        hits = await search_multimodal_evidence(
            query=query,
            vector_spaces=[vector_space],
            embedding_function=fake_embedding,
            vector_client=vector_client,
            user={"id": "user-1", "role": "user"},
            request=None,
        )

        assert [hit["evidence_ref"] for hit in hits] == expected_refs
        assert [call["vector"] for call in vector_client.search_calls] == expected_vectors
        assert [call["filter"] for call in vector_client.search_calls] == expected_filters
        if query.query_image_refs and query.visual_query:
            assert isinstance(embed_calls[0], dict)
            assert embed_calls[0]["query_text"] == "ring-like capsid particles figure"
            assert embed_calls[0]["query_images"][0]["image_bytes"] == query_image_bytes
            assert embed_calls[1] == "find the figure and fold"
        elif query.query_image_refs:
            assert isinstance(embed_calls[0], dict)
            assert embed_calls[0]["query_images"][0]["image_bytes"] == query_image_bytes
            assert embed_calls[0]["query_text"] is None
        else:
            assert embed_calls[0] == "find the capsid fold"


@pytest.mark.asyncio
async def test_search_multimodal_evidence_fuses_ranked_branch_hits_with_rrf():
    vector_space = types.SimpleNamespace(
        id="vs-1",
        knowledge_id="kb-1",
        retrieval_profile="unified_multimodal_dense",
        embedding_model="fake-multimodal-embed",
        supports_text_query=True,
        supports_image_query=True,
        supports_text_evidence=True,
        supports_image_evidence=True,
    )

    class _BranchQuotaVectorClient:
        def __init__(self) -> None:
            self.search_calls: list[dict[str, object]] = []

        async def search(self, collection_name, vectors, filter=None, limit=10):
            self.search_calls.append(
                {
                    "collection_name": collection_name,
                    "vector": [round(float(value), 3) for value in vectors[0][:3]],
                    "filter": filter,
                    "limit": limit,
                }
            )
            modality = (filter or {}).get("modality", {}).get("$in", [None])[0]
            if modality == "text":
                return _filtered_vector_result(
                    [
                        ("vec-text-1", {"evidence_ref": "ke:text:1", "modality": "text"}, 0.01),
                        ("vec-shared-text", {"evidence_ref": "ke:shared", "modality": "text"}, 0.02),
                        ("vec-text-3", {"evidence_ref": "ke:text:3", "modality": "text"}, 0.03),
                    ],
                    filter,
                )
            return _filtered_vector_result(
                [
                    ("vec-shared-image", {"evidence_ref": "ke:shared", "modality": "image"}, 0.01),
                    ("vec-image-1", {"evidence_ref": "ke:image:1", "modality": "image"}, 0.02),
                    ("vec-image-2", {"evidence_ref": "ke:image:2", "modality": "image"}, 0.03),
                ],
                filter,
            )

    async def fake_embedding(query, prefix=None, user=None):
        assert query in {"find matching figures", "matching figure panels"}
        return [1.0, 0.0, 0.0]

    query = normalize_query_knowledge_evidence_args(
        query_text="find matching figures",
        visual_query="matching figure panels",
        knowledge_ids=["kb-1"],
        count=3,
    )
    vector_client = _BranchQuotaVectorClient()

    hits = await search_multimodal_evidence(
        query=query,
        vector_spaces=[vector_space],
        embedding_function=fake_embedding,
        vector_client=vector_client,
        user={"id": "user-1", "role": "user"},
        request=None,
    )

    assert [call["filter"] for call in vector_client.search_calls] == [
        {"modality": {"$in": ["text"]}},
        {"modality": {"$in": ["image"]}},
    ]
    assert all(call["limit"] > query.top_k for call in vector_client.search_calls)
    assert [hit["evidence_ref"] for hit in hits] == ["ke:shared", "ke:text:1", "ke:image:1"]
    assert hits[0]["fusion_score"] > hits[1]["fusion_score"] > hits[2]["fusion_score"]
    assert hits[0]["branch_ranks"] == {"text_dense": 2, "image_dense": 1}


@pytest.mark.asyncio
async def test_search_multimodal_evidence_does_not_send_raw_question_to_image_dense_branch():
    vector_space = types.SimpleNamespace(
        id="vs-1",
        knowledge_id="kb-1",
        retrieval_profile="unified_multimodal_dense",
        embedding_model="fake-multimodal-embed",
        supports_text_query=True,
        supports_image_query=True,
        supports_text_evidence=True,
        supports_image_evidence=True,
    )

    class _TracingVectorClient:
        def __init__(self) -> None:
            self.search_calls: list[dict[str, object]] = []

        async def search(self, collection_name, vectors, filter=None, limit=10):
            self.search_calls.append({"vector": vectors[0], "filter": filter, "limit": limit})
            return _filtered_vector_result(
                [("vec-text", {"evidence_ref": "ke:text:rule", "modality": "text"}, 0.01)],
                filter,
            )

    embed_calls: list[object] = []

    async def fake_embedding(query, prefix=None, user=None):
        embed_calls.append(query)
        assert query == "where should the red sample go?"
        return [1.0, 0.0, 0.0]

    query = normalize_query_knowledge_evidence_args(
        query_text="where should the red sample go?",
        knowledge_ids=["kb-1"],
        count=3,
    )
    vector_client = _TracingVectorClient()

    hits = await search_multimodal_evidence(
        query=query,
        vector_spaces=[vector_space],
        embedding_function=fake_embedding,
        vector_client=vector_client,
        user={"id": "user-1", "role": "user"},
        request=None,
    )

    assert embed_calls == ["where should the red sample go?"]
    assert [call["filter"] for call in vector_client.search_calls] == [{"modality": {"$in": ["text"]}}]
    assert [hit["evidence_ref"] for hit in hits] == ["ke:text:rule"]


@pytest.mark.asyncio
async def test_search_multimodal_evidence_uses_visual_query_for_text_to_image_branch():
    vector_space = types.SimpleNamespace(
        id="vs-1",
        knowledge_id="kb-1",
        retrieval_profile="unified_multimodal_dense",
        embedding_model="fake-multimodal-embed",
        supports_text_query=True,
        supports_image_query=True,
        supports_text_evidence=True,
        supports_image_evidence=True,
    )

    class _VisualQueryVectorClient:
        def __init__(self) -> None:
            self.search_calls: list[dict[str, object]] = []

        async def search(self, collection_name, vectors, filter=None, limit=10):
            self.search_calls.append({"vector": vectors[0], "filter": filter, "limit": limit})
            modality = (filter or {}).get("modality", {}).get("$in", [None])[0]
            if modality == "text":
                return _filtered_vector_result(
                    [("vec-text", {"evidence_ref": "ke:text:rule", "modality": "text"}, 0.99)],
                    filter,
                )
            return _filtered_vector_result(
                [("vec-image", {"evidence_ref": "ke:image:red-box", "modality": "image"}, 0.98)],
                filter,
            )

    embed_calls: list[object] = []

    async def fake_embedding(query, prefix=None, user=None):
        embed_calls.append(query)
        if query == "where should the red sample go?":
            return [1.0, 0.0, 0.0]
        if query == "red-labeled destination box photo":
            return [0.0, 1.0, 0.0]
        raise AssertionError(f"unexpected embedding query: {query!r}")

    query = normalize_query_knowledge_evidence_args(
        query_text="where should the red sample go?",
        visual_query="red-labeled destination box photo",
        knowledge_ids=["kb-1"],
        count=3,
    )
    vector_client = _VisualQueryVectorClient()

    hits = await search_multimodal_evidence(
        query=query,
        vector_spaces=[vector_space],
        embedding_function=fake_embedding,
        vector_client=vector_client,
        user={"id": "user-1", "role": "user"},
        request=None,
    )

    assert embed_calls == [
        "where should the red sample go?",
        "red-labeled destination box photo",
    ]
    assert [call["filter"] for call in vector_client.search_calls] == [
        {"modality": {"$in": ["text"]}},
        {"modality": {"$in": ["image"]}},
    ]
    assert [call["vector"] for call in vector_client.search_calls] == [
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
    ]
    assert [hit["evidence_ref"] for hit in hits] == ["ke:text:rule", "ke:image:red-box"]


@pytest.mark.asyncio
async def test_search_multimodal_evidence_text_query_can_be_visual_query_when_image_modality_is_explicit():
    vector_space = types.SimpleNamespace(
        id="vs-1",
        knowledge_id="kb-1",
        retrieval_profile="unified_multimodal_dense",
        embedding_model="fake-multimodal-embed",
        supports_text_query=True,
        supports_image_query=True,
        supports_text_evidence=True,
        supports_image_evidence=True,
    )

    class _ImageOnlyVectorClient:
        def __init__(self) -> None:
            self.search_calls: list[dict[str, object]] = []

        async def search(self, collection_name, vectors, filter=None, limit=10):
            self.search_calls.append({"vector": vectors[0], "filter": filter, "limit": limit})
            return _filtered_vector_result(
                [("vec-image", {"evidence_ref": "ke:image:red-box", "modality": "image"}, 0.02)],
                filter,
            )

    embed_calls: list[object] = []

    async def fake_embedding(query, prefix=None, user=None):
        embed_calls.append(query)
        assert query == "red-labeled destination box photo"
        return [0.0, 1.0, 0.0]

    query = normalize_query_knowledge_evidence_args(
        query_text="red-labeled destination box photo",
        knowledge_ids=["kb-1"],
        modalities=["image"],
        count=3,
    )
    vector_client = _ImageOnlyVectorClient()

    hits = await search_multimodal_evidence(
        query=query,
        vector_spaces=[vector_space],
        embedding_function=fake_embedding,
        vector_client=vector_client,
        user={"id": "user-1", "role": "user"},
        request=None,
    )

    assert embed_calls == ["red-labeled destination box photo"]
    assert [call["filter"] for call in vector_client.search_calls] == [{"modality": {"$in": ["image"]}}]
    assert [hit["evidence_ref"] for hit in hits] == ["ke:image:red-box"]


@pytest.mark.asyncio
async def test_search_multimodal_evidence_mixed_query_dedupes_after_branch_fusion(monkeypatch):
    vector_space = types.SimpleNamespace(
        id="vs-1",
        knowledge_id="kb-1",
        retrieval_profile="unified_multimodal_dense",
        embedding_model="fake-multimodal-embed",
        supports_text_query=True,
        supports_image_query=True,
        supports_text_evidence=True,
        supports_image_evidence=True,
    )

    class _MixedBranchVectorClient:
        async def search(self, collection_name, vectors, filter=None, limit=10):
            modality = (filter or {}).get("modality", {}).get("$in", [None])[0]
            if modality == "text":
                return _filtered_vector_result(
                    [
                        ("vec-shared-text", {"evidence_ref": "ke:shared", "modality": "text"}, 0.08),
                        ("vec-text-1", {"evidence_ref": "ke:text:1", "modality": "text"}, 0.10),
                    ],
                    filter,
                )
            return _filtered_vector_result(
                [
                    ("vec-shared-image", {"evidence_ref": "ke:shared", "modality": "image"}, 0.06),
                    ("vec-image-1", {"evidence_ref": "ke:image:1", "modality": "image"}, 0.20),
                ],
                filter,
            )

    query_file = multimodal_mod.FileModel(
        id="query-image",
        user_id="user-1",
        hash="query-image-hash",
        filename="query.png",
        path="/tmp/query.png",
        data={"status": "completed"},
        meta={"content_type": "image/png", "name": "query.png"},
        created_at=1,
        updated_at=1,
    )

    async def fake_get_file_by_id(file_id, db=None):
        return query_file if file_id == "query-image" else None

    monkeypatch.setattr(multimodal_mod.Files, "get_file_by_id", fake_get_file_by_id)

    async def fake_embedding(query, prefix=None, user=None):
        if query == "find the figure and fold":
            return [1.0, 0.0, 0.0]
        assert isinstance(query, dict)
        assert query["query_text"] == "ring-like capsid particles figure"
        assert query["query_images"] == [
            {
                "ref": "chat:file:query-image",
                "file_id": "query-image",
                "mime_type": "image/png",
                "image_bytes": b"query-image-bytes",
            }
        ]
        return [2.0, 2.0, 0.0]

    request = _FakeRequest(
        EVIDENCE_QUERY_IMAGE_RESOLVER=lambda refs, request=None: [
            {
                "ref": refs[0],
                "file_id": "query-image",
                "mime_type": "image/png",
                "image_bytes": b"query-image-bytes",
            }
        ]
    )
    query = normalize_query_knowledge_evidence_args(
        query_text="find the figure and fold",
        visual_query="ring-like capsid particles figure",
        query_image_refs=["chat:file:query-image"],
        knowledge_ids=["kb-1"],
        count=3,
    )

    hits = await search_multimodal_evidence(
        query=query,
        vector_spaces=[vector_space],
        embedding_function=fake_embedding,
        vector_client=_MixedBranchVectorClient(),
        user={"id": "user-1", "role": "user"},
        request=request,
    )

    assert [hit["evidence_ref"] for hit in hits] == ["ke:shared", "ke:image:1", "ke:text:1"]
    assert len({hit["evidence_ref"] for hit in hits}) == 3


@pytest.mark.asyncio
async def test_search_multimodal_evidence_rrf_helper_accepts_extensible_branch_names():
    fused_hits = multimodal_mod._rrf_fuse_branch_hits(
        {
            "text_dense": [
                {"evidence_ref": "ke:text:1", "score": 0.99, "modality": "text"},
                {"evidence_ref": "ke:shared", "score": 0.98, "modality": "text"},
            ],
            "image_dense": [
                {"evidence_ref": "ke:shared", "score": 0.97, "modality": "image"},
                {"evidence_ref": "ke:image:1", "score": 0.96, "modality": "image"},
            ],
            "lexical_caption": [
                {"evidence_ref": "ke:shared", "score": 12.0, "modality": "image"},
                {"evidence_ref": "ke:image:1", "score": 11.0, "modality": "image"},
            ],
        },
        limit=3,
        rrf_k=40,
    )

    assert [hit["evidence_ref"] for hit in fused_hits] == ["ke:shared", "ke:image:1", "ke:text:1"]
    assert fused_hits[0]["branch_ranks"] == {"text_dense": 2, "image_dense": 1, "lexical_caption": 1}
    assert fused_hits[1]["branch_ranks"] == {"image_dense": 2, "lexical_caption": 2}
    assert fused_hits[2]["branch_ranks"] == {"text_dense": 1}
    assert fused_hits[0]["fusion_score"] > fused_hits[1]["fusion_score"] > fused_hits[2]["fusion_score"]


def test_search_multimodal_evidence_candidate_limit_never_shrinks_below_top_k():
    assert multimodal_mod._resolve_branch_candidate_limit(3) == 12
    assert multimodal_mod._resolve_branch_candidate_limit(20) == 48
    assert multimodal_mod._resolve_branch_candidate_limit(100) == 100


@pytest.mark.asyncio
async def test_search_multimodal_evidence_preserves_dense_backend_rank_with_similarity_scores():
    vector_space = types.SimpleNamespace(
        id="vs-1",
        knowledge_id="kb-1",
        retrieval_profile="unified_multimodal_dense",
        embedding_model="fake-multimodal-embed",
        supports_text_query=True,
        supports_image_query=True,
        supports_text_evidence=True,
        supports_image_evidence=True,
    )

    class _SimilarityScoreVectorClient:
        async def search(self, collection_name, vectors, filter=None, limit=10):
            return _filtered_vector_result(
                [
                    ("near", {"evidence_ref": "ke:image:near", "modality": "image"}, 0.90),
                    ("middle", {"evidence_ref": "ke:image:middle", "modality": "image"}, 0.80),
                    ("far", {"evidence_ref": "ke:image:far", "modality": "image"}, 0.70),
                ],
                filter,
            )

    async def fake_embedding(query, prefix=None, user=None):
        return [0.0, 1.0, 0.0]

    query = normalize_query_knowledge_evidence_args(
        visual_query="find similar equipment",
        knowledge_ids=["kb-1"],
        modalities=["image"],
        count=2,
    )

    hits = await search_multimodal_evidence(
        query=query,
        vector_spaces=[vector_space],
        embedding_function=fake_embedding,
        vector_client=_SimilarityScoreVectorClient(),
        user={"id": "user-1", "role": "user"},
        request=None,
    )

    assert [hit["evidence_ref"] for hit in hits] == ["ke:image:near", "ke:image:middle"]
    assert hits[0]["score"] == pytest.approx(0.90)
    assert hits[1]["score"] == pytest.approx(0.80)
    assert hits[0]["branch_scores"] == {"image_dense": pytest.approx(0.90)}
    assert hits[1]["branch_scores"] == {"image_dense": pytest.approx(0.80)}


def test_search_multimodal_evidence_lexical_hits_require_evidence_refs():
    branch = multimodal_mod._SearchBranch(name="text_lexical", modality="text")

    with pytest.raises(MultimodalVectorSpaceError, match="without evidence_ref"):
        multimodal_mod._normalize_branch_search_hits(
            [
                {"evidence_ref": "ke:text:1", "score": 1.0, "modality": "text"},
                {"chunk_uid": "legacy-manifest-chunk", "score": 0.9, "modality": "text"},
            ],
            branch=branch,
            source="hook",
        )


@pytest.mark.asyncio
async def test_search_multimodal_evidence_fuses_dense_and_lexical_branch_hits():
    vector_space = types.SimpleNamespace(
        id="vs-1",
        knowledge_id="kb-1",
        retrieval_profile="unified_multimodal_dense",
        embedding_model="fake-multimodal-embed",
        supports_text_query=True,
        supports_image_query=True,
        supports_text_evidence=True,
        supports_image_evidence=True,
    )

    class _DenseBranchVectorClient:
        def __init__(self) -> None:
            self.search_calls: list[dict[str, object]] = []

        async def search(self, collection_name, vectors, filter=None, limit=10):
            self.search_calls.append(
                {
                    "collection_name": collection_name,
                    "filter": filter,
                    "limit": limit,
                }
            )
            modality = (filter or {}).get("modality", {}).get("$in", [None])[0]
            if modality == "text":
                return _filtered_vector_result(
                    [("vec-text-1", {"evidence_ref": "ke:text:1", "modality": "text"}, 0.99)],
                    filter,
                )
            return _filtered_vector_result(
                [("vec-image-1", {"evidence_ref": "ke:image:1", "modality": "image"}, 0.98)],
                filter,
            )

    async def fake_embedding(query, prefix=None, user=None):
        assert query in {"find matching figures", "matching figure panels"}
        return [1.0, 0.0, 0.0]

    lexical_calls: list[dict[str, object]] = []

    async def fake_lexical_search(*, query_text, branch, vector_space, limit, request=None, user=None):
        lexical_calls.append(
            {
                "query_text": query_text,
                "branch_name": branch["name"],
                "modality": branch["modality"],
                "knowledge_id": vector_space.knowledge_id,
                "vector_space_id": vector_space.id,
                "limit": limit,
            }
        )
        return [
            {
                "evidence_ref": "ke:shared",
                "score": 10.0 if branch["modality"] == "text" else 9.0,
                "modality": branch["modality"],
            }
        ]

    query = normalize_query_knowledge_evidence_args(
        query_text="find matching figures",
        visual_query="matching figure panels",
        knowledge_ids=["kb-1"],
        count=3,
    )
    vector_client = _DenseBranchVectorClient()

    hits = await search_multimodal_evidence(
        query=query,
        vector_spaces=[vector_space],
        embedding_function=fake_embedding,
        vector_client=vector_client,
        user={"id": "user-1", "role": "user"},
        request=_FakeRequest(EVIDENCE_RETRIEVAL_LEXICAL_SEARCH=fake_lexical_search),
    )

    assert lexical_calls == [
        {
            "query_text": "find matching figures",
            "branch_name": "text_lexical",
            "modality": "text",
            "knowledge_id": "kb-1",
            "vector_space_id": "vs-1",
            "limit": 12,
        },
        {
            "query_text": "matching figure panels",
            "branch_name": "image_lexical",
            "modality": "image",
            "knowledge_id": "kb-1",
            "vector_space_id": "vs-1",
            "limit": 12,
        },
    ]
    assert [call["filter"] for call in vector_client.search_calls] == [
        {"modality": {"$in": ["text"]}},
        {"modality": {"$in": ["image"]}},
    ]
    assert [hit["evidence_ref"] for hit in hits] == ["ke:shared", "ke:text:1", "ke:image:1"]
    assert hits[0]["branch_ranks"] == {"text_lexical": 1, "image_lexical": 1}


@pytest.mark.asyncio
async def test_search_multimodal_evidence_applies_reranker_when_enabled(tmp_path, monkeypatch):
    async with _db_session_ctx(tmp_path) as session:
        await _seed_knowledge_and_file(
            session,
            file_id="file-text",
            filename="paper.pdf",
            content_type="application/pdf",
            path="/tmp/paper.pdf",
        )
        session.add(
            File(
                id="file-img",
                user_id="user-1",
                hash="file-img-hash",
                filename="figure.png",
                path="/tmp/figure.png",
                data={"status": "completed"},
                meta={"content_type": "image/png", "name": "figure.png"},
                created_at=1,
                updated_at=1,
            )
        )
        await session.commit()

        await KnowledgeEvidences.create_evidence(
            knowledge_id="kb-1",
            file_id="file-text",
            modality="text",
            evidence_kind="text_chunk",
            content_hash="hash-text",
            projection_profile="unified_multimodal_dense",
            projection_config_hash="profile-hash",
            chunk_index=1,
            chunk_total=1,
            source_name="paper.pdf",
            content_text="The capsid shell has a conserved HK97-like fold.",
            preview_text="Conserved HK97-like fold.",
            title="Text finding",
            retrieval_chunk_uid="chunk-1",
            retrieval_chunk_row_id=1,
            evidence_ref="ke:kb-1:file-text:text_chunk:1:txt",
            db=session,
        )
        image_asset = await KnowledgeEvidenceAssets.create_asset(
            knowledge_id="kb-1",
            file_id="file-img",
            asset_kind="standalone_image",
            mime_type="image/png",
            storage_uri="/tmp/figure.png",
            sha256="sha-image",
            status="ready",
            db=session,
        )
        await KnowledgeEvidences.create_evidence(
            knowledge_id="kb-1",
            file_id="file-img",
            asset_id=image_asset.id,
            modality="image",
            evidence_kind="standalone_image",
            content_hash="hash-image",
            projection_profile="unified_multimodal_dense",
            projection_config_hash="profile-hash",
            chunk_index=1,
            chunk_total=1,
            source_name="figure.png",
            content_text="A microscopy panel with ring-like capsid particles.",
            preview_text="Ring-like capsid particles.",
            title="Gel image",
            evidence_ref="ke:kb-1:file-img:standalone_image:1:img",
            db=session,
        )
        await KnowledgeEvidences.create_evidence(
            knowledge_id="kb-1",
            file_id="file-text",
            modality="text",
            evidence_kind="text_chunk",
            content_hash="hash-text-2",
            projection_profile="unified_multimodal_dense",
            projection_config_hash="profile-hash",
            chunk_index=2,
            chunk_total=2,
            source_name="paper.pdf",
            content_text="An unrelated text chunk.",
            preview_text="Unrelated text chunk.",
            title="Other finding",
            retrieval_chunk_uid="chunk-2",
            retrieval_chunk_row_id=2,
            evidence_ref="ke:kb-1:file-text:text_chunk:2:txt",
            db=session,
        )

        evidence_by_ref = {
            "ke:kb-1:file-text:text_chunk:1:txt": await KnowledgeEvidences.get_evidence_by_ref(
                "ke:kb-1:file-text:text_chunk:1:txt", db=session
            ),
            "ke:kb-1:file-text:text_chunk:2:txt": await KnowledgeEvidences.get_evidence_by_ref(
                "ke:kb-1:file-text:text_chunk:2:txt", db=session
            ),
            "ke:kb-1:file-img:standalone_image:1:img": await KnowledgeEvidences.get_evidence_by_ref(
                "ke:kb-1:file-img:standalone_image:1:img", db=session
            ),
        }

        async def fake_get_evidence_by_ref(ref, db=None):
            return evidence_by_ref.get(ref)

        monkeypatch.setattr(multimodal_mod.KnowledgeEvidences, "get_evidence_by_ref", fake_get_evidence_by_ref)

        vector_space = await KnowledgeVectorSpaces.create_vector_space(
            knowledge_id="kb-1",
            retrieval_profile="unified_multimodal_dense",
            embedding_model="fake-multimodal-embed",
            projection_config_hash="profile-hash",
            embedding_dim=3,
            distance_metric="cosine",
            vector_backend="pgvector",
            supports_text_query=True,
            supports_image_query=True,
            supports_text_evidence=True,
            supports_image_evidence=True,
            active=True,
            db=session,
        )

        class _RerankBranchVectorClient:
            def __init__(self) -> None:
                self.search_calls: list[dict[str, object]] = []

            async def search(self, collection_name, vectors, filter=None, limit=10):
                self.search_calls.append({"collection_name": collection_name, "filter": filter, "limit": limit})
                modality = (filter or {}).get("modality", {}).get("$in", [None])[0]
                if modality == "text":
                    return _filtered_vector_result(
                        [
                            (
                                "vec-text-1",
                                {
                                    "evidence_ref": "ke:kb-1:file-text:text_chunk:1:txt",
                                    "vector_space_id": "vs-1",
                                    "modality": "text",
                                },
                                0.99,
                            ),
                            (
                                "vec-text-2",
                                {
                                    "evidence_ref": "ke:kb-1:file-text:text_chunk:2:txt",
                                    "vector_space_id": "vs-1",
                                    "modality": "text",
                                },
                                0.98,
                            ),
                        ],
                        filter,
                    )
                return _filtered_vector_result(
                    [
                        (
                            "vec-image-1",
                            {
                                "evidence_ref": "ke:kb-1:file-img:standalone_image:1:img",
                                "vector_space_id": "vs-1",
                                "modality": "image",
                            },
                            0.70,
                        )
                    ],
                    filter,
                )

        async def fake_embedding(query, prefix=None, user=None):
            return [2.0, 2.0, 0.0]

        rerank_calls: list[dict[str, object]] = []

        def fake_reranker(query, documents, user=None):
            rerank_calls.append(
                {
                    "query": query,
                    "documents": [doc.page_content for doc in documents],
                    "user": user,
                }
            )
            return [0.1, 0.9]

        query = normalize_query_knowledge_evidence_args(
            query_text="find the figure and fold",
            visual_query="ring-like capsid particles figure",
            knowledge_ids=["kb-1"],
            count=4,
            rerank=True,
        )
        hits = await search_multimodal_evidence(
            query=query,
            vector_spaces=[vector_space],
            embedding_function=fake_embedding,
            vector_client=_RerankBranchVectorClient(),
            user={"id": "user-1", "role": "user"},
            request=_FakeRequest(RERANKING_FUNCTION=fake_reranker),
        )

        assert rerank_calls == [
            {
                "query": "find the figure and fold",
                "documents": [
                    "The capsid shell has a conserved HK97-like fold.",
                    "A microscopy panel with ring-like capsid particles.",
                ],
                "user": {"id": "user-1", "role": "user"},
            }
        ]
        assert [hit["evidence_ref"] for hit in hits] == [
            "ke:kb-1:file-img:standalone_image:1:img",
            "ke:kb-1:file-text:text_chunk:1:txt",
            "ke:kb-1:file-text:text_chunk:2:txt",
        ]
        assert hits[0]["score"] == pytest.approx(0.9)
        assert hits[1]["score"] == pytest.approx(0.1)
        assert hits[2]["score"] == pytest.approx(0.98)


@pytest.mark.asyncio
async def test_search_multimodal_evidence_reranker_uses_image_asset_metadata(tmp_path, monkeypatch):
    async with _db_session_ctx(tmp_path) as session:
        await _seed_knowledge_and_file(
            session,
            file_id="file-text",
            filename="paper.pdf",
            content_type="application/pdf",
            path="/tmp/paper.pdf",
        )
        session.add(
            File(
                id="file-img",
                user_id="user-1",
                hash="file-img-hash",
                filename="figure.png",
                path="/tmp/figure.png",
                data={"status": "completed"},
                meta={"content_type": "image/png", "name": "figure.png"},
                created_at=1,
                updated_at=1,
            )
        )
        await session.commit()

        text_evidence = await KnowledgeEvidences.create_evidence(
            knowledge_id="kb-1",
            file_id="file-text",
            modality="text",
            evidence_kind="text_chunk",
            content_hash="hash-text",
            projection_profile="unified_multimodal_dense",
            projection_config_hash="profile-hash",
            chunk_index=1,
            chunk_total=1,
            source_name="paper.pdf",
            content_text="The capsid shell has a conserved HK97-like fold.",
            preview_text="Conserved HK97-like fold.",
            title="Text finding",
            retrieval_chunk_uid="chunk-1",
            retrieval_chunk_row_id=1,
            evidence_ref="ke:kb-1:file-text:text_chunk:1:txt",
            db=session,
        )
        image_asset = await KnowledgeEvidenceAssets.create_asset(
            knowledge_id="kb-1",
            file_id="file-img",
            asset_kind="standalone_image",
            mime_type="image/png",
            storage_uri="/tmp/figure.png",
            sha256="sha-image",
            caption="Ring-like capsid particles.",
            ocr_text="Scale bar 100 nm",
            surrounding_text="Results Figure 2 shows particles in negative stain.",
            status="ready",
            db=session,
        )
        image_evidence = await KnowledgeEvidences.create_evidence(
            knowledge_id="kb-1",
            file_id="file-img",
            asset_id=image_asset.id,
            modality="image",
            evidence_kind="standalone_image",
            content_hash="hash-image",
            projection_profile="unified_multimodal_dense",
            projection_config_hash="profile-hash",
            chunk_index=1,
            chunk_total=1,
            source_name="figure.png",
            content_text=None,
            preview_text=None,
            title=None,
            evidence_ref="ke:kb-1:file-img:standalone_image:1:img",
            db=session,
        )

        evidence_by_ref = {
            text_evidence.evidence_ref: text_evidence,
            image_evidence.evidence_ref: image_evidence,
        }
        asset_by_id = {
            image_asset.id: image_asset,
        }

        async def fake_get_evidence_by_ref(ref, db=None):
            return evidence_by_ref.get(ref)

        async def fake_get_asset_by_id(asset_id, db=None):
            return asset_by_id.get(asset_id)

        monkeypatch.setattr(multimodal_mod.KnowledgeEvidences, "get_evidence_by_ref", fake_get_evidence_by_ref)
        monkeypatch.setattr(multimodal_mod.KnowledgeEvidenceAssets, "get_asset_by_id", fake_get_asset_by_id)

        vector_space = await KnowledgeVectorSpaces.create_vector_space(
            knowledge_id="kb-1",
            retrieval_profile="unified_multimodal_dense",
            embedding_model="fake-multimodal-embed",
            projection_config_hash="profile-hash",
            embedding_dim=3,
            distance_metric="cosine",
            vector_backend="pgvector",
            supports_text_query=True,
            supports_image_query=True,
            supports_text_evidence=True,
            supports_image_evidence=True,
            active=True,
            db=session,
        )

        class _RerankMetadataVectorClient:
            async def search(self, collection_name, vectors, filter=None, limit=10):
                modality = (filter or {}).get("modality", {}).get("$in", [None])[0]
                if modality == "text":
                    return _filtered_vector_result(
                        [
                            (
                                "vec-text-1",
                                {
                                    "evidence_ref": text_evidence.evidence_ref,
                                    "vector_space_id": vector_space.id,
                                    "modality": "text",
                                },
                                0.99,
                            )
                        ],
                        filter,
                    )
                return _filtered_vector_result(
                    [
                        (
                            "vec-image-1",
                            {
                                "evidence_ref": image_evidence.evidence_ref,
                                "vector_space_id": vector_space.id,
                                "modality": "image",
                            },
                            0.98,
                        )
                    ],
                    filter,
                )

        async def fake_embedding(query, prefix=None, user=None):
            return [1.0, 0.0, 0.0]

        rerank_calls: list[dict[str, object]] = []

        def fake_reranker(query, documents, user=None):
            rerank_calls.append(
                {
                    "query": query,
                    "documents": [doc.page_content for doc in documents],
                }
            )
            return [0.1, 0.9]

        query = normalize_query_knowledge_evidence_args(
            query_text="find ring-like capsid particles",
            visual_query="ring-like capsid particles figure",
            knowledge_ids=["kb-1"],
            count=2,
            rerank=True,
        )

        hits = await search_multimodal_evidence(
            query=query,
            vector_spaces=[vector_space],
            embedding_function=fake_embedding,
            vector_client=_RerankMetadataVectorClient(),
            user={"id": "user-1", "role": "user"},
            request=_FakeRequest(RERANKING_FUNCTION=fake_reranker),
        )

        assert rerank_calls[0]["query"] == "find ring-like capsid particles"
        assert rerank_calls[0]["documents"][0] == "The capsid shell has a conserved HK97-like fold."
        assert "Ring-like capsid particles." in rerank_calls[0]["documents"][1]
        assert "Results Figure 2 shows particles in negative stain." in rerank_calls[0]["documents"][1]
        assert "Scale bar 100 nm" in rerank_calls[0]["documents"][1]
        assert "figure.png" in rerank_calls[0]["documents"][1]
        assert [hit["evidence_ref"] for hit in hits] == [
            image_evidence.evidence_ref,
            text_evidence.evidence_ref,
        ]


@pytest.mark.asyncio
async def test_search_multimodal_evidence_skips_reranker_for_image_only_queries(monkeypatch):
    vector_space = types.SimpleNamespace(
        id="vs-1",
        knowledge_id="kb-1",
        retrieval_profile="unified_multimodal_dense",
        embedding_model="fake-multimodal-embed",
        supports_text_query=True,
        supports_image_query=True,
        supports_text_evidence=True,
        supports_image_evidence=True,
    )

    class _ImageOnlyVectorClient:
        async def search(self, collection_name, vectors, filter=None, limit=10):
            modality = (filter or {}).get("modality", {}).get("$in", [None])[0]
            if modality == "image":
                return _filtered_vector_result(
                    [("vec-image-1", {"evidence_ref": "ke:image:1", "modality": "image"}, 0.10)],
                    filter,
                )
            return _filtered_vector_result(
                [("vec-text-1", {"evidence_ref": "ke:text:1", "modality": "text"}, 0.20)],
                filter,
            )

    query_file = multimodal_mod.FileModel(
        id="query-image",
        user_id="user-1",
        hash="query-image-hash",
        filename="query.png",
        path="/tmp/query.png",
        data={"status": "completed"},
        meta={"content_type": "image/png", "name": "query.png"},
        created_at=1,
        updated_at=1,
    )

    async def fake_get_file_by_id(file_id, db=None):
        return query_file if file_id == "query-image" else None

    monkeypatch.setattr(multimodal_mod.Files, "get_file_by_id", fake_get_file_by_id)

    reranker_called = False

    def fake_reranker(query, documents, user=None):
        nonlocal reranker_called
        reranker_called = True
        return [0.9]

    async def fake_embedding(query, prefix=None, user=None):
        assert isinstance(query, dict)
        assert query["query_text"] is None
        return [0.0, 1.0, 0.0]

    request = _FakeRequest(
        RERANKING_FUNCTION=fake_reranker,
        EVIDENCE_QUERY_IMAGE_RESOLVER=lambda refs, request=None: [
            {
                "ref": refs[0],
                "file_id": "query-image",
                "mime_type": "image/png",
                "image_bytes": b"query-image-bytes",
            }
        ],
    )
    query = normalize_query_knowledge_evidence_args(
        query_image_refs=["chat:file:query-image"],
        knowledge_ids=["kb-1"],
        count=2,
        rerank=True,
    )

    hits = await search_multimodal_evidence(
        query=query,
        vector_spaces=[vector_space],
        embedding_function=fake_embedding,
        vector_client=_ImageOnlyVectorClient(),
        user={"id": "user-1", "role": "user"},
        request=request,
    )

    assert reranker_called is False
    assert [hit["evidence_ref"] for hit in hits] == ["ke:image:1"]


@pytest.mark.asyncio
async def test_search_multimodal_evidence_rejects_image_query_when_vector_space_cannot_support_it(tmp_path):
    async with _db_session_ctx(tmp_path) as session:
        await _seed_knowledge_and_file(
            session,
            file_id="file-img",
            filename="figure.png",
            content_type="image/png",
            path="/tmp/figure.png",
        )

        vector_space = await KnowledgeVectorSpaces.create_vector_space(
            knowledge_id="kb-1",
            retrieval_profile="text_surrogate_only",
            embedding_model="fake-text-embed",
            projection_config_hash="profile-hash",
            embedding_dim=3,
            distance_metric="cosine",
            vector_backend="pgvector",
            supports_text_query=True,
            supports_image_query=False,
            supports_text_evidence=True,
            supports_image_evidence=False,
            active=True,
            db=session,
        )

        query = normalize_query_knowledge_evidence_args(
            query_image_refs=["chat:file:query-image"],
            knowledge_ids=["kb-1"],
            count=4,
        )

        with pytest.raises(MultimodalVectorSpaceError) as exc_info:
            await search_multimodal_evidence(
                query=query,
                vector_spaces=[vector_space],
                embedding_function=lambda *_args, **_kwargs: [0.0, 1.0, 0.0],
                vector_client=_FakeVectorClient(),
                user={"id": "user-1", "role": "user"},
                request=None,
            )

        assert exc_info.value.code == "unsupported_image_query"


@pytest.mark.asyncio
async def test_search_multimodal_evidence_fails_closed_when_query_image_ref_cannot_be_resolved(
    tmp_path, monkeypatch
):
    async with _db_session_ctx(tmp_path) as session:
        await _seed_knowledge_and_file(
            session,
            file_id="file-img",
            filename="figure.png",
            content_type="image/png",
            path="/tmp/figure.png",
        )

        vector_space = await KnowledgeVectorSpaces.create_vector_space(
            knowledge_id="kb-1",
            retrieval_profile="unified_multimodal_dense",
            embedding_model="fake-multimodal-embed",
            projection_config_hash="profile-hash",
            embedding_dim=3,
            distance_metric="cosine",
            vector_backend="pgvector",
            supports_text_query=True,
            supports_image_query=True,
            supports_text_evidence=True,
            supports_image_evidence=True,
            active=True,
            db=session,
        )

        async def fake_get_file_by_id(file_id, db=None):
            return None

        monkeypatch.setattr(multimodal_mod.Files, "get_file_by_id", fake_get_file_by_id)

        query = normalize_query_knowledge_evidence_args(
            query_image_refs=["chat:file:missing-query-image"],
            knowledge_ids=["kb-1"],
            count=4,
        )

        with pytest.raises(MultimodalVectorSpaceError) as exc_info:
            await search_multimodal_evidence(
                query=query,
                vector_spaces=[vector_space],
                embedding_function=lambda *_args, **_kwargs: [0.0, 1.0, 0.0],
                vector_client=_FakeVectorClient(),
                user={"id": "user-1", "role": "user"},
                request=None,
            )

        assert exc_info.value.code == "unsupported_image_query"


@pytest.mark.asyncio
async def test_search_multimodal_evidence_denies_query_image_ref_without_file_acl_before_storage_read(
    tmp_path, monkeypatch
):
    query_image_path = tmp_path / "query.png"
    query_image_path.write_bytes(b"\x89PNG\r\n\x1a\nquery-image")

    query_file = multimodal_mod.FileModel(
        id="query-image",
        user_id="owner-user",
        hash="query-image-hash",
        filename="query.png",
        path=str(query_image_path),
        data={"status": "completed"},
        meta={"content_type": "image/png", "name": "query.png"},
        created_at=1,
        updated_at=1,
    )

    async def fake_get_file_by_id(file_id, db=None):
        return query_file if file_id == "query-image" else None

    async def fake_has_access_to_file(file_id, access_type, user, db=None):
        return False

    def forbidden_storage_read(storage_uri):
        raise AssertionError("unauthorized query image ref reached storage")

    monkeypatch.setattr(multimodal_mod.Files, "get_file_by_id", fake_get_file_by_id)
    monkeypatch.setattr(multimodal_mod, "has_access_to_file", fake_has_access_to_file, raising=False)
    monkeypatch.setattr(multimodal_mod.Storage, "get_file", forbidden_storage_read)

    vector_space = types.SimpleNamespace(
        id="vs-1",
        knowledge_id="kb-1",
        retrieval_profile="unified_multimodal_dense",
        embedding_model="fake-multimodal-embed",
        supports_text_query=True,
        supports_image_query=True,
        supports_text_evidence=True,
        supports_image_evidence=True,
    )
    query = normalize_query_knowledge_evidence_args(
        query_image_refs=["chat:file:query-image"],
        knowledge_ids=["kb-1"],
        count=4,
    )

    with pytest.raises(MultimodalVectorSpaceError) as exc_info:
        await search_multimodal_evidence(
            query=query,
            vector_spaces=[vector_space],
            embedding_function=lambda *_args, **_kwargs: [0.0, 1.0, 0.0],
            vector_client=_FakeVectorClient(),
            user={"id": "other-user", "role": "user"},
            request=None,
        )

    assert exc_info.value.code == "unsupported_image_query"


@pytest.mark.asyncio
async def test_query_image_ref_missing_and_unauthorized_use_same_error_shape(tmp_path, monkeypatch):
    query_image_path = tmp_path / "query.png"
    query_image_path.write_bytes(b"\x89PNG\r\n\x1a\nquery-image")
    query_file = multimodal_mod.FileModel(
        id="query-image",
        user_id="owner-user",
        hash="query-image-hash",
        filename="query.png",
        path=str(query_image_path),
        data={"status": "completed"},
        meta={"content_type": "image/png", "name": "query.png"},
        created_at=1,
        updated_at=1,
    )

    async def fake_has_access_to_file(file_id, access_type, user, db=None):
        return False

    monkeypatch.setattr(multimodal_mod, "has_access_to_file", fake_has_access_to_file, raising=False)
    monkeypatch.setattr(multimodal_mod.Storage, "get_file", lambda storage_uri: storage_uri)

    async def resolve_with_file(file):
        async def fake_get_file_by_id(file_id, db=None):
            return file

        monkeypatch.setattr(multimodal_mod.Files, "get_file_by_id", fake_get_file_by_id)
        with pytest.raises(MultimodalVectorSpaceError) as exc_info:
            await multimodal_mod.resolve_query_image_ref_for_embedding(
                "chat:file:query-image",
                user={"id": "other-user", "role": "user"},
            )
        return exc_info.value

    missing_error = await resolve_with_file(None)
    unauthorized_error = await resolve_with_file(query_file)

    assert unauthorized_error.code == missing_error.code == "unsupported_image_query"
    assert unauthorized_error.message == missing_error.message
    assert set(unauthorized_error.details) == set(missing_error.details)


@pytest.mark.asyncio
async def test_search_multimodal_evidence_allows_query_image_ref_with_granted_file_acl(tmp_path, monkeypatch):
    query_image_path = tmp_path / "query.png"
    query_image_bytes = b"\x89PNG\r\n\x1a\nquery-image"
    query_image_path.write_bytes(query_image_bytes)
    query_file = multimodal_mod.FileModel(
        id="query-image",
        user_id="owner-user",
        hash="query-image-hash",
        filename="query.png",
        path=str(query_image_path),
        data={"status": "completed"},
        meta={"content_type": "image/png", "name": "query.png"},
        created_at=1,
        updated_at=1,
    )
    access_calls = []

    async def fake_get_file_by_id(file_id, db=None):
        return query_file if file_id == "query-image" else None

    async def fake_has_access_to_file(file_id, access_type, user, db=None):
        access_calls.append((file_id, access_type, user.id))
        return True

    monkeypatch.setattr(multimodal_mod.Files, "get_file_by_id", fake_get_file_by_id)
    monkeypatch.setattr(multimodal_mod, "has_access_to_file", fake_has_access_to_file, raising=False)
    monkeypatch.setattr(multimodal_mod.Storage, "get_file", lambda storage_uri: storage_uri)

    vector_space = types.SimpleNamespace(
        id="vs-1",
        knowledge_id="kb-1",
        retrieval_profile="unified_multimodal_dense",
        embedding_model="fake-multimodal-embed",
        supports_text_query=True,
        supports_image_query=True,
        supports_text_evidence=True,
        supports_image_evidence=True,
    )

    async def fake_embedding(query, prefix=None, user=None):
        assert isinstance(query, dict)
        assert query["query_images"][0]["image_bytes"] == query_image_bytes
        return [0.0, 1.0, 0.0]

    query = normalize_query_knowledge_evidence_args(
        query_image_refs=["chat:file:query-image"],
        knowledge_ids=["kb-1"],
        count=4,
    )
    hits = await search_multimodal_evidence(
        query=query,
        vector_spaces=[vector_space],
        embedding_function=fake_embedding,
        vector_client=_FakeVectorClient(),
        user={"id": "shared-user", "role": "user"},
        request=None,
    )

    assert access_calls == [("query-image", "read", "shared-user")]
    assert [hit["evidence_ref"] for hit in hits] == ["ke:kb-1:file-img:standalone_image:1:img"]


@pytest.mark.asyncio
async def test_search_multimodal_evidence_allows_query_image_ref_for_admin_without_grant(tmp_path, monkeypatch):
    query_image_path = tmp_path / "query.png"
    query_image_bytes = b"\x89PNG\r\n\x1a\nquery-image"
    query_image_path.write_bytes(query_image_bytes)
    query_file = multimodal_mod.FileModel(
        id="query-image",
        user_id="owner-user",
        hash="query-image-hash",
        filename="query.png",
        path=str(query_image_path),
        data={"status": "completed"},
        meta={"content_type": "image/png", "name": "query.png"},
        created_at=1,
        updated_at=1,
    )

    async def fake_get_file_by_id(file_id, db=None):
        return query_file if file_id == "query-image" else None

    async def unexpected_has_access_to_file(file_id, access_type, user, db=None):
        raise AssertionError("admin query image ref should not need shared file grant")

    monkeypatch.setattr(multimodal_mod.Files, "get_file_by_id", fake_get_file_by_id)
    monkeypatch.setattr(multimodal_mod, "has_access_to_file", unexpected_has_access_to_file, raising=False)
    monkeypatch.setattr(multimodal_mod.Storage, "get_file", lambda storage_uri: storage_uri)

    vector_space = types.SimpleNamespace(
        id="vs-1",
        knowledge_id="kb-1",
        retrieval_profile="unified_multimodal_dense",
        embedding_model="fake-multimodal-embed",
        supports_text_query=True,
        supports_image_query=True,
        supports_text_evidence=True,
        supports_image_evidence=True,
    )

    async def fake_embedding(query, prefix=None, user=None):
        assert isinstance(query, dict)
        assert query["query_images"][0]["image_bytes"] == query_image_bytes
        return [0.0, 1.0, 0.0]

    query = normalize_query_knowledge_evidence_args(
        query_image_refs=["chat:file:query-image"],
        knowledge_ids=["kb-1"],
        count=4,
    )
    hits = await search_multimodal_evidence(
        query=query,
        vector_spaces=[vector_space],
        embedding_function=fake_embedding,
        vector_client=_FakeVectorClient(),
        user={"id": "admin-user", "role": "admin"},
        request=None,
    )

    assert [hit["evidence_ref"] for hit in hits] == ["ke:kb-1:file-img:standalone_image:1:img"]


@pytest.mark.asyncio
async def test_custom_query_image_resolver_cannot_bypass_baseline_file_acl(tmp_path, monkeypatch):
    query_image_path = tmp_path / "query.png"
    query_image_path.write_bytes(b"\x89PNG\r\n\x1a\nquery-image")
    query_file = multimodal_mod.FileModel(
        id="query-image",
        user_id="owner-user",
        hash="query-image-hash",
        filename="query.png",
        path=str(query_image_path),
        data={"status": "completed"},
        meta={"content_type": "image/png", "name": "query.png"},
        created_at=1,
        updated_at=1,
    )
    resolver_calls = []

    async def fake_get_file_by_id(file_id, db=None):
        return query_file if file_id == "query-image" else None

    async def fake_has_access_to_file(file_id, access_type, user, db=None):
        return False

    async def custom_resolver(refs, request=None):
        resolver_calls.append(list(refs))
        return [
            {
                "ref": refs[0],
                "file_id": "query-image",
                "mime_type": "image/png",
                "image_bytes": b"bypass",
            }
        ]

    monkeypatch.setattr(multimodal_mod.Files, "get_file_by_id", fake_get_file_by_id)
    monkeypatch.setattr(multimodal_mod, "has_access_to_file", fake_has_access_to_file, raising=False)
    monkeypatch.setattr(
        multimodal_mod.Storage,
        "get_file",
        lambda storage_uri: (_ for _ in ()).throw(AssertionError("unauthorized query image ref reached storage")),
    )

    vector_space = types.SimpleNamespace(
        id="vs-1",
        knowledge_id="kb-1",
        retrieval_profile="unified_multimodal_dense",
        embedding_model="fake-multimodal-embed",
        supports_text_query=True,
        supports_image_query=True,
        supports_text_evidence=True,
        supports_image_evidence=True,
    )
    query = normalize_query_knowledge_evidence_args(
        query_image_refs=["chat:file:query-image"],
        knowledge_ids=["kb-1"],
        count=4,
    )

    with pytest.raises(MultimodalVectorSpaceError) as exc_info:
        await search_multimodal_evidence(
            query=query,
            vector_spaces=[vector_space],
            embedding_function=lambda *_args, **_kwargs: [0.0, 1.0, 0.0],
            vector_client=_FakeVectorClient(),
            user={"id": "other-user", "role": "user"},
            request=_FakeRequest(EVIDENCE_QUERY_IMAGE_RESOLVER=custom_resolver),
        )

    assert exc_info.value.code == "unsupported_image_query"
    assert resolver_calls == []


@pytest.mark.asyncio
async def test_upsert_multimodal_evidence_embedding_links_truth_rows_to_vector_backend_ids(tmp_path):
    image_path = tmp_path / "figure.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake-image")

    async with _db_session_ctx(tmp_path) as session:
        await _seed_knowledge_and_file(
            session,
            file_id="file-img",
            filename="figure.png",
            content_type="image/png",
            path=str(image_path),
        )

        asset = await KnowledgeEvidenceAssets.create_asset(
            knowledge_id="kb-1",
            file_id="file-img",
            asset_kind="standalone_image",
            mime_type="image/png",
            storage_uri=str(image_path),
            sha256="sha-image",
            status="ready",
            db=session,
        )
        evidence = await KnowledgeEvidences.create_evidence(
            knowledge_id="kb-1",
            file_id="file-img",
            asset_id=asset.id,
            modality="image",
            evidence_kind="standalone_image",
            content_hash="hash-image",
            projection_profile="unified_multimodal_dense",
            projection_config_hash="profile-hash",
            chunk_index=1,
            chunk_total=1,
            source_name="figure.png",
            content_text="A microscopy panel with ring-like capsid particles.",
            preview_text="Ring-like capsid particles.",
            title="Gel image",
            evidence_ref="ke:kb-1:file-img:standalone_image:1:img",
            db=session,
        )
        vector_space = await KnowledgeVectorSpaces.create_vector_space(
            knowledge_id="kb-1",
            retrieval_profile="unified_multimodal_dense",
            embedding_model="fake-multimodal-embed",
            projection_config_hash="profile-hash",
            embedding_dim=3,
            distance_metric="cosine",
            vector_backend="pgvector",
            supports_text_query=True,
            supports_image_query=True,
            supports_text_evidence=True,
            supports_image_evidence=True,
            active=True,
            db=session,
        )

        embed_calls: list[object] = []

        async def fake_embedding(payload, prefix=None, user=None):
            embed_calls.append(payload)
            assert isinstance(payload, dict)
            assert payload["evidence_ref"] == evidence.evidence_ref
            assert payload["modality"] == "image"
            assert payload["image_bytes"] == image_path.read_bytes()
            return [0.1, 0.2, 0.3]

        vector_client = _FakeVectorClient()

        result = await upsert_multimodal_evidence_embedding(
            evidence=evidence,
            vector_space=vector_space,
            embedding_function=fake_embedding,
            vector_client=vector_client,
            db=session,
        )

        embeddings = await KnowledgeEvidenceEmbeddings.list_embeddings(
            evidence_ref=evidence.evidence_ref,
            vector_space_id=vector_space.id,
            db=session,
        )

        assert result.embedding.embedding_status == "ready"
        assert result.embedding.vector_backend_collection == f"kb-1:{vector_space.id}"
        assert result.embedding.vector_backend_id == result.vector_item.id
        assert embed_calls and isinstance(embed_calls[0], dict)
        assert vector_client.upsert_calls[0]["collection_name"] == f"kb-1:{vector_space.id}"
        assert vector_client.upsert_calls[0]["items"][0].metadata["evidence_ref"] == evidence.evidence_ref
        assert vector_client.upsert_calls[0]["items"][0].metadata["knowledge_id"] == "kb-1"
        assert vector_client.upsert_calls[0]["items"][0].metadata["file_id"] == "file-img"
        assert vector_client.upsert_calls[0]["items"][0].metadata["vector_space_id"] == vector_space.id
        assert len(embeddings) == 1
        assert embeddings[0].vector_backend_id == result.vector_item.id
        assert embeddings[0].embedding_status == "ready"
