from langchain_core.documents import Document

from open_webui.routers import retrieval


def _config(**overrides):
    values = {key: None for key in retrieval.RETRIEVAL_CONFIG_KEYS}
    values.update(
        TEXT_SPLITTER="character",
        CHUNK_SIZE=800,
        CHUNK_OVERLAP=120,
        CHUNK_MIN_SIZE_TARGET=0,
        ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER=False,
        TIKTOKEN_ENCODING_NAME="cl100k_base",
        RAG_EMBEDDING_ENGINE="openai",
        RAG_EMBEDDING_MODEL="text-embedding-3-large",
    )
    values.update(overrides)
    return retrieval.RetrievalConfig(values)


def test_vector_metadata_records_deterministic_chunker_and_embedding_signatures():
    docs = [
        Document(page_content="alpha", metadata={"source": "a.md", "start_index": 0}),
        Document(page_content="beta", metadata={"source": "a.md", "start_index": 100}),
    ]

    metadatas = retrieval._build_vector_metadatas(
        docs,
        _config(),
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

    baseline = retrieval._build_vector_metadatas(docs, _config())[0]
    changed_chunker = retrieval._build_vector_metadatas(docs, _config(CHUNK_SIZE=1200))[0]
    changed_embedding = retrieval._build_vector_metadatas(
        docs,
        _config(RAG_EMBEDDING_MODEL="new-2048d-embedding"),
    )[0]

    assert baseline["chunker_config_hash"] != changed_chunker["chunker_config_hash"]
    assert baseline["embedding_config_hash"] != changed_embedding["embedding_config_hash"]
    assert baseline["chunker_config_hash"] == changed_embedding["chunker_config_hash"]
    assert baseline["embedding_config_hash"] == changed_chunker["embedding_config_hash"]
