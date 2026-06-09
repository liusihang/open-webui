import os
from unittest.mock import patch

os.environ.setdefault("WEBUI_SECRET_KEY", "test-secret")
os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")
os.environ.setdefault("DATABASE_ENABLE_SESSION_SHARING", "true")

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.migrations.versions import d1e2f3a4b5c6_add_multimodal_evidence_schema as evidence_migration
from open_webui.models.evidence import KnowledgeVectorSpaces
from open_webui.models.knowledge import Knowledge  # noqa: F401
from open_webui.retrieval.vector import multimodal as multimodal_mod
from open_webui.retrieval.vector.multimodal import (
    MultimodalVectorSpaceError,
    build_multimodal_vector_item,
    normalize_multimodal_evidence_input,
    search_multimodal_evidence,
    resolve_multimodal_vector_space,
)
from open_webui.retrieval.vector.embedding_adapter import (
    OpenAICompatibleMultimodalEvidenceEmbeddingAdapter,
)
from open_webui.retrieval.vector.main import VectorItem


def _run_migration(engine, direction):
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE IF NOT EXISTS knowledge (id TEXT PRIMARY KEY)")
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        with patch.object(evidence_migration, "op", operations):
            getattr(evidence_migration, direction)()


def test_vector_item_supports_backend_dict_style_access():
    item = VectorItem(
        id="vec-1",
        text="alpha",
        vector=[0.1, 0.2, 0.3],
        metadata={"evidence_ref": "ke:test"},
    )

    assert item["id"] == "vec-1"
    assert item["text"] == "alpha"
    assert item["vector"] == [0.1, 0.2, 0.3]
    assert item["metadata"] == {"evidence_ref": "ke:test"}

    with pytest.raises(KeyError):
        _ = item["missing"]


@pytest.mark.asyncio
async def test_resolve_multimodal_vector_space_prefers_explicit_profile_and_respects_capabilities(tmp_path):
    db_path = tmp_path / "multimodal_vector_space.db"
    sync_engine = create_engine(f"sqlite:///{db_path}")
    _run_migration(sync_engine, "upgrade")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        text_space = await KnowledgeVectorSpaces.create_vector_space(
            knowledge_id="kb-1",
            retrieval_profile="text_surrogate_only",
            embedding_model="text-embed-v1",
            projection_config_hash="proj-text",
            embedding_dim=1024,
            distance_metric="cosine",
            vector_backend="pgvector",
            supports_text_query=True,
            supports_image_query=False,
            supports_text_evidence=True,
            supports_image_evidence=False,
            active=True,
            db=session,
        )
        multimodal_space = await KnowledgeVectorSpaces.create_vector_space(
            knowledge_id="kb-1",
            retrieval_profile="unified_multimodal_dense",
            embedding_model="multimodal-embed-v1",
            projection_config_hash="proj-mm",
            embedding_dim=2048,
            distance_metric="cosine",
            vector_backend="pgvector",
            supports_text_query=True,
            supports_image_query=True,
            supports_text_evidence=True,
            supports_image_evidence=True,
            active=True,
            db=session,
        )

        text_selection = await resolve_multimodal_vector_space(
            knowledge_id="kb-1",
            retrieval_profile="text_surrogate_only",
            query_modality="text",
            evidence_modality="text",
            db=session,
        )
        image_selection = await resolve_multimodal_vector_space(
            knowledge_id="kb-1",
            vector_space_id=multimodal_space.id,
            query_modality="image",
            evidence_modality="image",
            db=session,
        )

        assert text_selection.vector_space.id == text_space.id
        assert text_selection.collection_name == f"kb-1:{text_space.id}"
        assert image_selection.vector_space.id == multimodal_space.id
        assert image_selection.collection_name == f"kb-1:{multimodal_space.id}"

    await engine.dispose()


def test_sanitize_embedding_error_redacts_model_bound_image_payloads():
    error = RuntimeError(
        "provider failed with {'image_bytes': b'\\x89PNG\\r\\n\\x1a\\nsecret', "
        "'url': 'data:image/png;base64,QUFBQUFBQUFBQUFBQUFBQUFB'}"
    )

    sanitized = multimodal_mod.sanitize_embedding_error(error)

    assert "image_bytes" not in sanitized
    assert "data:image" not in sanitized
    assert "QUFBQUFB" not in sanitized
    assert "[redacted-image-payload]" in sanitized


@pytest.mark.asyncio
async def test_resolve_multimodal_vector_space_rejects_unsupported_image_query_without_fallback(tmp_path):
    db_path = tmp_path / "unsupported_image_query.db"
    sync_engine = create_engine(f"sqlite:///{db_path}")
    _run_migration(sync_engine, "upgrade")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        text_space = await KnowledgeVectorSpaces.create_vector_space(
            knowledge_id="kb-1",
            retrieval_profile="text_surrogate_only",
            embedding_model="text-embed-v1",
            projection_config_hash="proj-text",
            embedding_dim=1024,
            distance_metric="cosine",
            vector_backend="pgvector",
            supports_text_query=True,
            supports_image_query=False,
            supports_text_evidence=True,
            supports_image_evidence=False,
            active=True,
            db=session,
        )

        with pytest.raises(MultimodalVectorSpaceError) as exc_info:
            await resolve_multimodal_vector_space(
                knowledge_id="kb-1",
                vector_space_id=text_space.id,
                query_modality="image",
                db=session,
            )

        assert exc_info.value.code == "unsupported_image_query"

    await engine.dispose()


def test_normalize_multimodal_evidence_input_rejects_unsafe_image_path_url_and_base64_inputs():
    base = {
        "modality": "image",
        "knowledge_id": "kb-1",
        "file_id": "file-1",
        "content_hash": "sha-1",
        "projection_config_hash": "proj-1",
        "source_name": "figure.png",
    }

    for unsafe_field, unsafe_value in (
        ("image_url", {"url": "https://example.com/image.png"}),
        ("url", "https://example.com/image.png"),
        ("path", "/tmp/image.png"),
        ("file_path", "./relative/image.png"),
        ("data_url", "data:image/png;base64,AAAA"),
        ("base64", "AAAA"),
    ):
        with pytest.raises(MultimodalVectorSpaceError) as exc_info:
            normalize_multimodal_evidence_input({**base, unsafe_field: unsafe_value})

        assert exc_info.value.code == "unsafe_image_descriptor"


@pytest.mark.asyncio
async def test_evidence_embedding_adapter_delegates_text_to_legacy_embedding_function():
    calls = []

    async def text_embedding_function(query, prefix=None, user=None):
        calls.append({"query": query, "prefix": prefix, "user": user})
        return [0.1, 0.2, 0.3]

    adapter = OpenAICompatibleMultimodalEvidenceEmbeddingAdapter(
        text_embedding_function=text_embedding_function,
        model="Qwen3-VL-Embedding-2B",
        url="http://embedding.local/v1",
        key="",
    )

    embedding = await adapter("alpha beta", prefix="query:", user={"id": "user-1"})

    assert embedding == [0.1, 0.2, 0.3]
    assert calls == [
        {
            "query": "alpha beta",
            "prefix": "query:",
            "user": {"id": "user-1"},
        }
    ]


@pytest.mark.asyncio
async def test_evidence_embedding_adapter_sends_image_payload_as_messages_not_stringified_input():
    requests = []

    async def text_embedding_function(query, prefix=None, user=None):
        raise AssertionError(f"image input must not reach text embedding function: {query!r}")

    async def post_json(*, url, headers, payload):
        requests.append({"url": url, "headers": headers, "payload": payload})
        return {"data": [{"embedding": [0.4, 0.5, 0.6]}]}

    adapter = OpenAICompatibleMultimodalEvidenceEmbeddingAdapter(
        text_embedding_function=text_embedding_function,
        model="Qwen3-VL-Embedding-2B",
        url="http://embedding.local/v1",
        key="",
        dimensions=2048,
        post_json=post_json,
    )

    embedding = await adapter(
        {
            "query_text": "find similar plots",
            "query_images": [
                {
                    "ref": "chat:file:file-1",
                    "file_id": "file-1",
                    "mime_type": "image/png",
                    "image_bytes": b"\x89PNG\r\n\x1a\npayload",
                }
            ],
        }
    )

    assert embedding == [0.4, 0.5, 0.6]
    assert requests[0]["url"] == "http://embedding.local/v1/embeddings"
    payload = requests[0]["payload"]
    assert "input" not in payload
    assert payload["model"] == "Qwen3-VL-Embedding-2B"
    assert payload["encoding_format"] == "float"
    assert payload["dimensions"] == 2048
    assert payload["messages"] == [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,iVBORw0KGgpwYXlsb2Fk",
                    },
                },
                {
                    "type": "text",
                    "text": "find similar plots",
                },
            ],
        }
    ]


@pytest.mark.asyncio
async def test_evidence_embedding_adapter_sends_image_evidence_payload_as_messages():
    requests = []

    async def text_embedding_function(query, prefix=None, user=None):
        raise AssertionError(f"image evidence input must not reach text embedding function: {query!r}")

    async def post_json(*, url, headers, payload):
        requests.append(payload)
        return {"data": [{"embedding": [0.7, 0.8, 0.9]}]}

    adapter = OpenAICompatibleMultimodalEvidenceEmbeddingAdapter(
        text_embedding_function=text_embedding_function,
        model="Qwen3-VL-Embedding-2B",
        url="http://embedding.local/v1",
        key="",
        post_json=post_json,
    )

    embedding = await adapter(
        {
            "modality": "image",
            "evidence_ref": "ke:kb-1:file-1:standalone_image:0:def456",
            "mime_type": "image/jpeg",
            "image_bytes": b"\xff\xd8\xffpayload",
            "preview_text": "Figure 1. A chart.",
        }
    )

    assert embedding == [0.7, 0.8, 0.9]
    payload = requests[0]
    assert "input" not in payload
    assert payload["messages"][0]["content"] == [
        {
            "type": "image_url",
            "image_url": {
                "url": "data:image/jpeg;base64,/9j/cGF5bG9hZA==",
            },
        },
        {
            "type": "text",
            "text": "Figure 1. A chart.",
        },
    ]


@pytest.mark.asyncio
async def test_evidence_embedding_adapter_rejects_external_image_source_fields():
    async def text_embedding_function(query, prefix=None, user=None):
        raise AssertionError(f"image evidence input must not reach text embedding function: {query!r}")

    async def post_json(*, url, headers, payload):
        raise AssertionError("unsafe image descriptors must fail before remote embedding")

    adapter = OpenAICompatibleMultimodalEvidenceEmbeddingAdapter(
        text_embedding_function=text_embedding_function,
        model="Qwen3-VL-Embedding-2B",
        url="http://embedding.local/v1",
        key="",
        post_json=post_json,
    )

    with pytest.raises(ValueError, match="unsafe image fields"):
        await adapter(
            {
                "modality": "image",
                "evidence_ref": "ke:kb-1:file-1:standalone_image:0:def456",
                "mime_type": "image/jpeg",
                "image_bytes": b"\xff\xd8\xffpayload",
                "path": "/tmp/unsafe.jpg",
                "url": "https://example.test/unsafe.jpg",
                "base64": "/9j/cGF5bG9hZA==",
                "bytes": "not-resolved-bytes",
            }
        )


@pytest.mark.asyncio
async def test_default_evidence_search_rejects_raw_external_image_refs_before_embedding():
    class VectorClient:
        async def search(self, *args, **kwargs):
            raise AssertionError("unsafe image refs must fail before vector search")

    async def embedding_function(query, prefix=None, user=None):
        raise AssertionError("unsafe image refs must fail before embedding")

    query = type(
        "Query",
        (),
        {
            "query_text": "plot",
            "query_image_refs": ["https://example.com/plot.png"],
            "top_k": 3,
        },
    )()
    vector_space = type(
        "VectorSpace",
        (),
        {
            "id": "vs-1",
            "knowledge_id": "kb-1",
            "retrieval_profile": "unified_multimodal_dense",
            "embedding_model": "Qwen3-VL-Embedding-2B",
            "supports_image_query": True,
            "supports_text_query": True,
        },
    )()

    with pytest.raises(MultimodalVectorSpaceError) as exc_info:
        await search_multimodal_evidence(
            query=query,
            vector_spaces=[vector_space],
            embedding_function=embedding_function,
            vector_client=VectorClient(),
        )

    assert exc_info.value.code == "unsupported_image_query"


@pytest.mark.asyncio
async def test_build_multimodal_vector_item_carries_evidence_identity_and_shared_space_metadata(tmp_path):
    db_path = tmp_path / "multimodal_vector_item.db"
    sync_engine = create_engine(f"sqlite:///{db_path}")
    _run_migration(sync_engine, "upgrade")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        vector_space = await KnowledgeVectorSpaces.create_vector_space(
            knowledge_id="kb-1",
            retrieval_profile="unified_multimodal_dense",
            embedding_model="multimodal-embed-v1",
            projection_config_hash="proj-mm",
            embedding_dim=2048,
            distance_metric="cosine",
            vector_backend="pgvector",
            supports_text_query=True,
            supports_image_query=True,
            supports_text_evidence=True,
            supports_image_evidence=True,
            active=True,
            db=session,
        )
        selection = await resolve_multimodal_vector_space(
            knowledge_id="kb-1",
            retrieval_profile="unified_multimodal_dense",
            query_modality="image",
            evidence_modality="image",
            db=session,
        )

        text_descriptor = normalize_multimodal_evidence_input(
            {
                "modality": "text",
                "knowledge_id": "kb-1",
                "file_id": "file-1",
                "evidence_ref": "ke:kb-1:file-1:text_chunk:0:abc123",
                "evidence_kind": "text_chunk",
                "content_hash": "abc123",
                "projection_config_hash": "proj-mm",
                "source_name": "notes.md",
                "text": "alpha beta",
                "chunk_index": 1,
                "chunk_total": 2,
            }
        )
        image_descriptor = normalize_multimodal_evidence_input(
            {
                "modality": "image",
                "knowledge_id": "kb-1",
                "file_id": "file-1",
                "evidence_ref": "ke:kb-1:file-1:standalone_image:0:def456",
                "evidence_kind": "standalone_image",
                "content_hash": "def456",
                "projection_config_hash": "proj-mm",
                "source_name": "figure.png",
                "preview_text": "Figure A: comparison chart",
                "asset_ref": "ka:kb-1:file-1:standalone_image:def456",
                "chunk_index": 1,
                "chunk_total": 1,
            }
        )

        text_item = build_multimodal_vector_item(
            vector=[0.1, 0.2, 0.3],
            descriptor=text_descriptor,
            selection=selection,
        )
        image_item = build_multimodal_vector_item(
            vector=[0.4, 0.5, 0.6],
            descriptor=image_descriptor,
            selection=selection,
        )

        assert text_item.text == "alpha beta"
        assert image_item.text == "Figure A: comparison chart"
        assert text_item.id != image_item.id
        assert text_item.metadata["evidence_ref"] == "ke:kb-1:file-1:text_chunk:0:abc123"
        assert text_item.metadata["modality"] == "text"
        assert text_item.metadata["vector_space_id"] == vector_space.id
        assert text_item.metadata["vector_backend_collection"] == f"kb-1:{vector_space.id}"
        assert text_item.metadata["vector_role"] == "text_chunk_dense"
        assert image_item.metadata["evidence_ref"] == "ke:kb-1:file-1:standalone_image:0:def456"
        assert image_item.metadata["modality"] == "image"
        assert image_item.metadata["vector_space_id"] == vector_space.id
        assert image_item.metadata["vector_backend_collection"] == f"kb-1:{vector_space.id}"
        assert image_item.metadata["vector_role"] == "image_dense"
        assert image_item.metadata["retrieval_profile"] == "unified_multimodal_dense"
        assert image_item.metadata["projection_config_hash"] == "proj-mm"

    await engine.dispose()
