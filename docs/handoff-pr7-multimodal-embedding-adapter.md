# PR7 Multimodal Embedding Adapter Handoff

## Scope
- Worktree: `/Users/liusihang/.config/superpowers/worktrees/openwebui/codex-retrieval-manifest-opensearch-phase1`
- Do not write code in `/Users/liusihang/openwebui`.
- Do not modify PaddleOCR async loader files; another worker owns that area.
- Fix real multimodal embedding only for the evidence vector path, not legacy RAG embedding.
- Current task: make ordinary knowledge-base upload/ingest default to the evidence-enabled multimodal ingestion/projector path, not the legacy text-only path.

## Checkpoints
- 2026-06-09: Verified git top-level is the requested worktree.
- 2026-06-09: Branch is `codex/retrieval-manifest-opensearch-phase2-3`; pre-existing dirty file is `uv.lock`.
- 2026-06-09: AutoEnvReport reference provider sends image embedding requests to `/embeddings` with `messages` containing an `image_url` data URL block plus a text prompt block, not `input`.
- 2026-06-09: RED test run: `uv run pytest backend/open_webui/test/util/test_multimodal_vector_adapter.py -q` fails during collection with `ModuleNotFoundError: No module named 'open_webui.retrieval.vector.embedding_adapter'`.
- 2026-06-09: Implemented `OpenAICompatibleMultimodalEvidenceEmbeddingAdapter` as an evidence-only adapter. It delegates text to the existing embedding function and converts resolved image bytes into AutoEnvReport-style `messages` payloads.
- 2026-06-09: Wired `EVIDENCE_RETRIEVAL_EMBEDDING` through the adapter at app startup and `/embedding/update`; legacy `EMBEDDING_FUNCTION` remains unchanged.
- 2026-06-09: GREEN focused run: `uv run pytest backend/open_webui/test/util/test_multimodal_vector_adapter.py -q` reports `9 passed, 1 warning`.
- 2026-06-09: Additional verification: `uv run python -m py_compile backend/open_webui/retrieval/vector/embedding_adapter.py backend/open_webui/main.py backend/open_webui/routers/retrieval.py backend/open_webui/test/util/test_multimodal_vector_adapter.py` exits 0.
- 2026-06-09: Additional verification: `uv run pytest backend/open_webui/test/util/test_evidence_vector_search.py backend/open_webui/test/util/test_query_knowledge_evidence_contract.py -q` reports `20 passed, 5 warnings`.
- 2026-06-09 15:37 CST: New checkpoint for default KB ingestion switch. Confirmed requested worktree/branch; only pre-existing dirty file at start was `uv.lock`.
- 2026-06-09 15:37 CST: Read prior handoff and memory summary for multimodal evidence constraints. Plan is TDD-first: add focused test proving ordinary KB `process_file` schedules/runs evidence projection by default, then make the smallest backend control-flow change.
- 2026-06-09 15:45 CST: RED focused run: `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/apps/webui/routers/test_retrieval_hybrid_query.py -q` failed because ordinary KB `process_file`/batch paths did not enqueue/run evidence projection.
- 2026-06-09 15:52 CST: Implemented default KB evidence path in `backend/open_webui/routers/retrieval.py`: legacy vector items now materialize `retrieval_chunk` manifest rows, then KB-scoped single and batch ingest run the existing evidence projection job with `project_document_images=True`.
- 2026-06-09 15:54 CST: GREEN focused run: `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/apps/webui/routers/test_retrieval_hybrid_query.py -q` reports `6 passed`.
- 2026-06-09 15:56 CST: Additional verification: `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run python -m py_compile backend/open_webui/routers/retrieval.py backend/open_webui/test/apps/webui/routers/test_retrieval_hybrid_query.py` exits 0.
- 2026-06-09 15:57 CST: Additional verification: `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/apps/webui/routers/test_retrieval_hybrid_query.py backend/open_webui/test/util/test_evidence_projector.py backend/open_webui/test/util/test_evidence_vector_search.py` reports `20 passed`.
- 2026-06-09 15:57 CST: Additional verification: multimodal adapter/runtime/knowledge reindex suite reports `37 passed`; retrieval indexing/evidence API/contract suite reports `43 passed`; `git diff --check` exits 0.

## First-Principles Design Decision
The defect is not in vector search or the legacy OpenAI embedding helper itself. The evidence path can pass structured image descriptors, while the legacy helper only understands OpenAI text `input`; feeding image dicts into that helper silently creates text embeddings of serialized dicts. The lowest-complexity fix is an in-process evidence-only adapter registered as `EVIDENCE_RETRIEVAL_EMBEDDING`. This avoids a new compose service, avoids extra deployment state, and keeps legacy `EMBEDDING_FUNCTION` behavior unchanged.

## Implementation Plan
- Add focused tests first for text passthrough, image `messages` payload construction, and unsafe raw external image rejection.
- Add a small evidence-only OpenAI-compatible multimodal adapter module.
- Wire `main.py` so `EVIDENCE_RETRIEVAL_EMBEDDING` uses the adapter when the evidence model/config is OpenAI-compatible; otherwise preserve the existing evidence embedding function.
- Run focused pytest commands and inspect git diff.

## Current Status
- Earlier implementation and focused verification are complete for the evidence embedding adapter slice.
- The default ordinary KB ingest switch is implemented and focused verification passed.
- No PaddleOCR loader files were modified by this task. The worktree still contains the pre-existing `uv.lock` change, which remains intentionally unstaged/uncommitted.
