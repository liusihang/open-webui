# v0.12.0 fork

This main line uses official Open WebUI v0.12.0, commit
`15818d88fd7bde1b73de33590f50d15d21e8d7c1`, as its product base. Upstream license,
notices and attribution remain in place. It replaces the older custom main's
product tree while retaining that main's Git history.

Publishing this source does not deploy a running instance or migrate a database.
Configure providers, credentials, tools, models and user data separately. Private
runtime exports and operator-only migration scripts are deliberately not included.

## Retained changes

- Native Responses history and tool continuation, including stateless cumulative
  input with `store=false`, without silently falling back to Chat Completions.
- Native PDF handling, OnlyOffice preview/edit integration, and capability-aware
  reasoning controls for retained Pipes.
- Optional background memory review follows the existing external task-model
  setting. When no task model is selected, it retains the normal model-selection
  fallback. `ENABLE_MEMORY_BACKGROUND_REVIEW` defaults to false.
- Unified text/image evidence search: text-to-text, text-to-image, image-to-text
  and image-to-image. Native knowledge tools and automatic RAG use the same
  backend; Native mode avoids a duplicate automatic knowledge retrieval pass.
- Versioned evidence, file/knowledge-base permission checks, authorized image
  previews, source citations, durable image indexing and image-only retries.
  Text can become ready while image processing remains pending or failed.
- Office embedded-image extraction and PDF assets from an external parser or
  explicitly selected local extraction. Optional OpenSearch/RRF support remains
  available but is not required when hybrid search is disabled.

Layered knowledge summaries, the previous separate AgentRuns/AgentScope system,
Chatfile synchronization, and the retired Anthropic Pipe were not ported. Skills
and third-party Tool installations are runtime data, not bundled presets. The
retired SearXNG Tool and removed research/council Tools are not restored by this
source publication; upstream-supported features are otherwise retained.

## Deployment conditions

Use the upstream installation instructions together with the following fork
conditions. Existing persistent settings take precedence over environment
defaults; changing an environment variable alone may not update a saved setting.

- The unified evidence path currently requires official pgvector, 2048-dimensional
  vectors and compatible halfvec storage. Set
  `PGVECTOR_INITIALIZE_MAX_VECTOR_LENGTH=2048` and `PGVECTOR_USE_HALFVEC=true` for
  a new installation, and verify the actual database schema. This does not change
  an existing column's dimension or convert an existing corpus.
- Use one indexing/write owner, `UVICORN_WORKERS=1`, with a persistent private
  document-image asset root. The process/file lock is not a distributed lock for
  multiple hosts sharing a database.
- Configure an external OpenAI-compatible text/image embedding service. Register
  and explicitly select an evidence vector space with verified model revision,
  dimensions, metric, normalization, text prefixes, image preprocessing and
  encoding protocol. Matching a model name or dimension alone is insufficient.
  Qwen-style endpoints must implement compatible chat-template, assistant-marker
  and special-token/EOS semantics for both modalities; raw text embeddings from
  an arbitrary endpoint are not automatically compatible.
- The evidence settings are in the administrator's Documents settings. Document
  image extraction defaults to disabled. The PDF asset source defaults to parser;
  local extraction is an explicit alternative, not a silent fallback.
- PaddleOCR supports `layout` and `jobs` protocols. For a jobs endpoint, select
  `jobs`, supply its URL, token and model (for example `PaddleOCR-VL-1.6`), and
  explicitly allow only the required result/image origins. Credentials are not
  sent to result-storage origins. Selecting a protocol does not switch the global
  parser. The default global parser is not changed by this fork.
- Slim images include lightweight DOCX text extraction via `docx2txt==0.9`.
  DOCX/PPTX/XLSX embedded-image extraction does not imply all three formats have
  identical text-parser requirements; choose and configure the appropriate parser.
- OnlyOffice is optional. Configure `ENABLE_ONLYOFFICE_PREVIEW`,
  `ONLYOFFICE_DOCUMENT_SERVER_URL`, `ONLYOFFICE_PUBLIC_BASE_URL`,
  `ONLYOFFICE_JWT_SECRET` and `ONLYOFFICE_CALLBACK_ALLOWED_HOSTS` for your own
  deployment. Keep secrets outside Git. Office citations retain real document
  anchors rather than inventing page numbers.
- Optional OpenSearch uses `rag.enable_hybrid_search` and the `rag.opensearch_*`
  settings. When disabled, the keyword backend is not initialized or contacted.
- Select `api_type=responses` for a compatible native upstream connection. This
  repository does not bundle provider keys, routing rules or saved model aliases.

Apply the normal upstream migration chain including
`c92e41b7d630_add_unified_evidence_indexes.py` before use. That migration refuses
conflicting legacy custom tables; it is not a direct in-place migration procedure
for every older custom database. Back up and migrate legacy data separately.

## Existing vectors and limits

Existing vectors can be reused only when their identity and encoding provenance
are established. Unknown spaces remain quarantined and are excluded from unified
retrieval. Registering a new verified space does not promote old vectors or
re-encode them. There is no promise that an unknown legacy corpus becomes
immediately searchable with zero recomputation.

The accepted candidate passed 93 isolated core/integration/asset/Paddle contract
tests. A small synthetic corpus also passed real quantized 2048-dimensional
encoding, pgvector writes, four-way retrieval, native Responses knowledge-tool
continuation and authorized browser citation/image checks. PDF parser output was
reused from one separately successful Paddle job; the downstream indexing/search
was real. Existing Responses, OnlyOffice and frontend checks were also run.

These are bounded acceptance results, not a corpus-quality benchmark. Image
queries included self-matches. Full-library migration, unseen-image generalization,
multi-image PDF caption association, large-scale throughput, OpenSearch enabled,
reranker image capability and GPU sleep/wake contention are not established by
those tests. No production cutover is implied.

## Offline regression tests

Focused tests live in `backend/tests`, `tests/migration`, `tests/onlyoffice` and
the adjacent frontend `*.test.ts` files. Install development/test dependencies
(including pytest, pytest-asyncio and Vitest) in your own environment. Network
services are mocked in these regressions; they do not perform paid OCR, encoding
or model calls.

For RAG tests, use a disposable SQLite database and private temporary directories.
Set `ENABLE_DB_MIGRATIONS=false`, `DATABASE_ENABLE_SESSION_SHARING=true`,
`RAG_CORE_TEST_ISOLATED=1`, `ENABLE_PERSISTENT_CONFIG=false`, `OFFLINE_MODE=true`,
`WEBUI_SECRET_KEY` to a test-only value, `DATABASE_URL=sqlite:////tmp/owui-test.sqlite`,
`DATA_DIR` and `STATIC_DIR` to disposable locations, and `PYTHONPATH=backend`.
Then run:

```sh
python -m pytest -q backend/tests/test_rag_core.py backend/tests/test_rag_integration.py backend/tests/test_document_image_assets.py backend/tests/test_paddle_revalidation_contract.py
```

Run the small Responses/preview/memory checks separately from the full-product
integration tests, whose module initialization needs the backend dependency set:

```sh
python -m pytest -q backend/tests/test_native_pdf.py tests/onlyoffice/test_onlyoffice_port.py tests/migration/test_responses_native_history.py tests/migration/test_responses_cumulative_history.py tests/migration/test_memory_review_task_model.py
```
