from types import SimpleNamespace

import pytest

from open_webui.routers import retrieval as retrieval_mod


def _fake_request():
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    RAG_TEMPLATE="",
                    TOP_K=4,
                    BYPASS_EMBEDDING_AND_RETRIEVAL=False,
                    RAG_FULL_CONTEXT=False,
                    ADAPTIVE_FILE_CONTEXT_ENABLED=False,
                    ADAPTIVE_FILE_CONTEXT_DEFAULT_MODE="full",
                    ADAPTIVE_FILE_CONTEXT_MAX_TOKENS_PER_FILE=8000,
                    ADAPTIVE_FILE_CONTEXT_MAX_TOKENS_PER_REQUEST=16000,
                    ADAPTIVE_FILE_CONTEXT_DEBUG=False,
                    ENABLE_RAG_HYBRID_SEARCH=False,
                    ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS=False,
                    TOP_K_RERANKER=4,
                    RELEVANCE_THRESHOLD=0.0,
                    HYBRID_BM25_WEIGHT=0.5,
                    CONTENT_EXTRACTION_ENGINE="",
                    PDF_EXTRACT_IMAGES=False,
                    PDF_LOADER_MODE="single",
                    DATALAB_MARKER_API_KEY="",
                    DATALAB_MARKER_API_BASE_URL="",
                    DATALAB_MARKER_ADDITIONAL_CONFIG="",
                    DATALAB_MARKER_SKIP_CACHE=False,
                    DATALAB_MARKER_FORCE_OCR=False,
                    DATALAB_MARKER_PAGINATE=False,
                    DATALAB_MARKER_STRIP_EXISTING_OCR=False,
                    DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION=False,
                    DATALAB_MARKER_FORMAT_LINES=False,
                    DATALAB_MARKER_USE_LLM=False,
                    DATALAB_MARKER_OUTPUT_FORMAT="markdown",
                    EXTERNAL_DOCUMENT_LOADER_URL="",
                    EXTERNAL_DOCUMENT_LOADER_API_KEY="",
                    TIKA_SERVER_URL="",
                    DOCLING_SERVER_URL="",
                    DOCLING_API_KEY="",
                    DOCLING_PARAMS={},
                    DOCUMENT_INTELLIGENCE_ENDPOINT="",
                    DOCUMENT_INTELLIGENCE_KEY="",
                    DOCUMENT_INTELLIGENCE_MODEL="",
                    MISTRAL_OCR_API_BASE_URL="",
                    MISTRAL_OCR_API_KEY="",
                    MINERU_API_MODE="local",
                    MINERU_API_URL="",
                    MINERU_API_KEY="",
                    MINERU_API_TIMEOUT="30",
                    MINERU_PARAMS={},
                    RAG_RERANKING_MODEL="",
                    RAG_RERANKING_ENGINE="",
                    RAG_EXTERNAL_RERANKER_URL="",
                    RAG_EXTERNAL_RERANKER_API_KEY="",
                    RAG_EXTERNAL_RERANKER_TIMEOUT="30",
                    TEXT_SPLITTER="character",
                    ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER=False,
                    CHUNK_SIZE=1000,
                    CHUNK_MIN_SIZE_TARGET=0,
                    CHUNK_OVERLAP=100,
                    FILE_MAX_SIZE=100,
                    FILE_MAX_COUNT=10,
                    FILE_IMAGE_COMPRESSION_WIDTH=1920,
                    FILE_IMAGE_COMPRESSION_HEIGHT=1080,
                    ALLOWED_FILE_EXTENSIONS=["pdf"],
                    ENABLE_GOOGLE_DRIVE_INTEGRATION=False,
                    ENABLE_ONEDRIVE_INTEGRATION=False,
                    ENABLE_WEB_SEARCH=False,
                    WEB_SEARCH_ENGINE="",
                    WEB_SEARCH_TRUST_ENV=False,
                    WEB_SEARCH_RESULT_COUNT=3,
                    WEB_SEARCH_CONCURRENT_REQUESTS=1,
                    WEB_LOADER_CONCURRENT_REQUESTS=1,
                    WEB_SEARCH_DOMAIN_FILTER_LIST=[],
                    BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL=False,
                    BYPASS_WEB_SEARCH_WEB_LOADER=False,
                    OLLAMA_CLOUD_WEB_SEARCH_API_KEY="",
                    SEARXNG_QUERY_URL="",
                    SEARXNG_LANGUAGE="",
                    YACY_QUERY_URL="",
                    YACY_USERNAME="",
                    YACY_PASSWORD="",
                    GOOGLE_PSE_API_KEY="",
                    GOOGLE_PSE_ENGINE_ID="",
                    BRAVE_SEARCH_API_KEY="",
                    KAGI_SEARCH_API_KEY="",
                    MOJEEK_SEARCH_API_KEY="",
                    BOCHA_SEARCH_API_KEY="",
                    SERPSTACK_API_KEY="",
                    SERPSTACK_HTTPS=False,
                    SERPER_API_KEY="",
                    SERPLY_API_KEY="",
                    DDGS_BACKEND="",
                    TAVILY_API_KEY="",
                    SEARCHAPI_API_KEY="",
                    SEARCHAPI_ENGINE="",
                    SERPAPI_API_KEY="",
                    SERPAPI_ENGINE="",
                    JINA_API_KEY="",
                    JINA_API_BASE_URL="",
                    BING_SEARCH_V7_ENDPOINT="",
                    BING_SEARCH_V7_SUBSCRIPTION_KEY="",
                    EXA_API_KEY="",
                    PERPLEXITY_API_KEY="",
                    PERPLEXITY_MODEL="",
                    PERPLEXITY_SEARCH_CONTEXT_USAGE="",
                    PERPLEXITY_SEARCH_API_URL="",
                    SOUGOU_API_SID="",
                    SOUGOU_API_SK="",
                    WEB_LOADER_ENGINE="",
                    WEB_LOADER_TIMEOUT="30",
                    ENABLE_WEB_LOADER_SSL_VERIFICATION=True,
                    PLAYWRIGHT_WS_URL="",
                    PLAYWRIGHT_TIMEOUT=30,
                    FIRECRAWL_API_KEY="",
                    FIRECRAWL_API_BASE_URL="",
                    FIRECRAWL_TIMEOUT="30",
                    TAVILY_EXTRACT_DEPTH="basic",
                    EXTERNAL_WEB_SEARCH_URL="",
                    EXTERNAL_WEB_SEARCH_API_KEY="",
                    EXTERNAL_WEB_LOADER_URL="",
                    EXTERNAL_WEB_LOADER_API_KEY="",
                    YOUTUBE_LOADER_LANGUAGE=["en"],
                    YOUTUBE_LOADER_PROXY_URL="",
                    YANDEX_WEB_SEARCH_URL="",
                    YANDEX_WEB_SEARCH_API_KEY="",
                    YANDEX_WEB_SEARCH_CONFIG="",
                    YOUCOM_API_KEY="",
                    OPEN_NOTEBOOK_BASE_URL="https://nb.example.com",
                    OPEN_NOTEBOOK_API_PASSWORD="secret",
                    OPEN_NOTEBOOK_TIMEOUT_SECS=45,
                    OPEN_NOTEBOOK_TRANSFORMATION_ABSTRACT="tr-abstract",
                    OPEN_NOTEBOOK_TRANSFORMATION_KEY_FINDINGS="tr-findings",
                    OPEN_NOTEBOOK_TRANSFORMATION_KEY_DATA="tr-data",
                ),
                YOUTUBE_LOADER_TRANSLATION="en",
            )
        )
    )


def _fake_admin():
    return SimpleNamespace(id="admin-1", role="admin")


@pytest.mark.asyncio
async def test_get_rag_config_hides_open_notebook_layer_generation_settings():
    response = await retrieval_mod.get_rag_config(
        request=_fake_request(),
        user=_fake_admin(),
    )

    assert "OPEN_NOTEBOOK_BASE_URL" not in response
    assert "OPEN_NOTEBOOK_API_PASSWORD" not in response
    assert "OPEN_NOTEBOOK_TIMEOUT_SECS" not in response
    assert "OPEN_NOTEBOOK_TRANSFORMATION_ABSTRACT" not in response
    assert "OPEN_NOTEBOOK_TRANSFORMATION_KEY_FINDINGS" not in response
    assert "OPEN_NOTEBOOK_TRANSFORMATION_KEY_DATA" not in response


@pytest.mark.asyncio
async def test_update_rag_config_keeps_open_notebook_layer_generation_settings_internal():
    request = _fake_request()

    await retrieval_mod.update_rag_config(
        request=request,
        form_data=retrieval_mod.ConfigForm(),
        user=_fake_admin(),
    )

    assert request.app.state.config.OPEN_NOTEBOOK_BASE_URL == "https://nb.example.com"
    assert request.app.state.config.OPEN_NOTEBOOK_API_PASSWORD == "secret"
    assert request.app.state.config.OPEN_NOTEBOOK_TIMEOUT_SECS == 45
    assert request.app.state.config.OPEN_NOTEBOOK_TRANSFORMATION_ABSTRACT == "tr-abstract"
    assert request.app.state.config.OPEN_NOTEBOOK_TRANSFORMATION_KEY_FINDINGS == "tr-findings"
    assert request.app.state.config.OPEN_NOTEBOOK_TRANSFORMATION_KEY_DATA == "tr-data"
