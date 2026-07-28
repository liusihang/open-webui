import os
import sqlite3
import tempfile
from types import SimpleNamespace

os.environ.setdefault("WEBUI_SECRET_KEY", "test-secret")
os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")
_db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_db_file.close()
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db_file.name}")

with sqlite3.connect(_db_file.name) as conn:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS config (
            id INTEGER PRIMARY KEY,
            data JSON NOT NULL,
            version INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME
        )
        """
    )

from langchain_core.documents import Document

from open_webui.routers import retrieval


def _request(**overrides):
    config = SimpleNamespace(
        TEXT_SPLITTER="character",
        CHUNK_SIZE=800,
        CHUNK_OVERLAP=120,
        CHUNK_MIN_SIZE_TARGET=0,
        ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER=False,
        TIKTOKEN_ENCODING_NAME="cl100k_base",
        RAG_EMBEDDING_ENGINE="openai",
        RAG_EMBEDDING_MODEL="text-embedding-3-large",
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config)))


def test_vector_metadata_records_deterministic_chunker_and_embedding_signatures():
    docs = [
        Document(page_content="alpha", metadata={"source": "a.md", "start_index": 0}),
        Document(page_content="beta", metadata={"source": "a.md", "start_index": 100}),
    ]

    metadatas = retrieval._build_vector_metadatas(
        _request(),
        docs,
        metadata={"file_id": "file-1", "hash": "hash-1"},
    )

    assert [metadata["chunk_index"] for metadata in metadatas] == [0, 1]
    assert metadatas[0]["chunk_version"] == 1
    assert metadatas[0]["file_id"] == "file-1"
    assert metadatas[0]["chunker_config"] == {
        "version": 1,
        "text_splitter": "character",
        "chunk_size": 800,
        "chunk_overlap": 120,
        "chunk_min_size_target": 0,
        "markdown_header_text_splitter": False,
        "tiktoken_encoding_name": "cl100k_base",
    }
    assert metadatas[0]["chunker_config_hash"] == metadatas[1]["chunker_config_hash"]
    assert metadatas[0]["embedding_config"] == {
        "engine": "openai",
        "model": "text-embedding-3-large",
    }
    assert metadatas[0]["embedding_config_hash"] == metadatas[1]["embedding_config_hash"]


def test_vector_metadata_hashes_change_when_chunker_or_embedding_config_changes():
    docs = [Document(page_content="alpha", metadata={})]

    baseline = retrieval._build_vector_metadatas(_request(), docs)[0]
    changed_chunker = retrieval._build_vector_metadatas(_request(CHUNK_SIZE=1200), docs)[0]
    changed_embedding = retrieval._build_vector_metadatas(
        _request(RAG_EMBEDDING_MODEL="new-2048d-embedding"),
        docs,
    )[0]

    assert baseline["chunker_config_hash"] != changed_chunker["chunker_config_hash"]
    assert baseline["embedding_config_hash"] != changed_embedding["embedding_config_hash"]
    assert baseline["chunker_config_hash"] == changed_embedding["chunker_config_hash"]
    assert baseline["embedding_config_hash"] == changed_chunker["embedding_config_hash"]
