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
- 2026-06-09 16:33 CST: Follow-up review accepted the unconditional KB evidence projection design and found one useful hardening gap: project-job text evidence must default to `unified_multimodal_dense` instead of inheriting the low-level text-only backfill default.
- 2026-06-09 16:38 CST: Implemented the follow-up in `backend/open_webui/retrieval/evidence_projector.py`: KB file projection and project-job payload projection now pass `projection_profile="unified_multimodal_dense"` unless the job explicitly overrides it. The low-level `backfill_text_evidence_from_active_chunks()` default remains `text_only` for direct legacy/manual use.
- 2026-06-09 16:40 CST: Added regression tests that assert direct text backfill stays `text_only`, KB file projection produces `unified_multimodal_dense` text evidence, project jobs without `file_ids` also produce `unified_multimodal_dense`, and batch custom/non-KB collections do not enqueue evidence projection.
- 2026-06-09 16:42 CST: Explorer subagent Euclid completed read-only review and accepted the implementation: ordinary Knowledge add/update/batch paths now project by default, generic `/files` and arbitrary custom collections fail closed because `_run_evidence_projection_for_knowledge_file()` first checks for a real Knowledge row.
- 2026-06-09 16:44 CST: Verification after follow-up: `test_retrieval_hybrid_query.py + test_evidence_projector.py + test_evidence_vector_search.py` reports `25 passed`; broader PaddleOCR/query/adapter/evidence API/reindex/indexing suite reports `84 passed`; py_compile and `git diff --check` exit 0.
- 2026-06-09 17:02 CST: New delegated slice: align PR7 multimodal evidence retrieval toward AutoEnvReport-style fused recall in the assigned worktree only. Confirmed target files already have in-progress local edits for dense branch RRF + rerank, plus pre-existing `uv.lock`; do not revert any of them.
- 2026-06-09 17:05 CST: Read AutoEnvReport retrieval reference and current OpenWebUI evidence search implementation. Dense text/image branching, RRF(k=40), dedupe by `evidence_ref`, and post-fusion rerank already exist locally; the remaining gaps are a safe lexical branch seam for evidence retrieval and richer rerank text for image evidence using asset metadata (`caption`, `surrounding_text`, `ocr_text`, plus title/name/source fallbacks).
- 2026-06-09 17:07 CST: First-principles decision for this slice: do not fake an end-to-end OpenSearch evidence lexical path because the current repo’s lexical index/runtime is manifest-chunk oriented and does not guarantee `evidence_ref`-addressable evidence rows. Instead, add an explicit evidence lexical branch adapter/hook with focused tests; if a configured backend errors, surface the error rather than silently masking it.
- 2026-06-09 17:08 CST: TDD plan for this slice: add RED tests proving (1) text queries can fuse dense and lexical evidence branches by modality after dedupe/RRF, and (2) rerank documents for image evidence include asset caption/context/OCR metadata rather than only `content_text`/`preview_text`.
- 2026-06-09 17:14 CST: RED focused run: `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/util/test_evidence_vector_search.py -k 'fuses_dense_and_lexical_branch_hits or reranker_uses_image_asset_metadata'` failed for the expected reasons: no lexical branch hook execution, and image rerank text collapsed to `figure.png` instead of using asset metadata.
- 2026-06-09 17:24 CST: Implemented the smallest backend change in `backend/open_webui/retrieval/vector/multimodal.py`: kept existing dense text/image RRF flow, added explicit evidence lexical branch hooks (`EVIDENCE_RETRIEVAL_LEXICAL_SEARCH` and optional `EVIDENCE_RETRIEVAL_LEXICAL_CLIENT` adapter path), and enriched image-only rerank fallback text from `KnowledgeEvidenceAsset` caption/context/OCR when evidence rows do not already have richer text.
- 2026-06-09 17:26 CST: GREEN focused rerun of the new tests reports `2 passed`; note that lexical branch order after fusion still follows existing tie-break rules (`fusion_score`, then `score`, then `evidence_ref`) rather than any extra modality bias.
- 2026-06-09 17:30 CST: Requested minimum verification passed: `test_evidence_vector_search.py + test_query_knowledge_evidence_runtime.py` reports `23 passed`; `py_compile` on `multimodal.py`, `test_evidence_vector_search.py`, and `evidence_retrieval_quality_harness.py` exits 0; `git diff --check` on the requested files exits 0.

## Residual Risk
- There is still no default end-to-end evidence lexical/OpenSearch index wired in this slice. The new lexical branch hook is intentionally explicit because the current built-in OpenSearch lexical surface is manifest-chunk oriented and does not guarantee `evidence_ref`-addressable evidence hits. Real aiserver quality evaluation is still needed once the main thread decides how evidence lexical indexing should be populated and injected.

## First-Principles Design Decision
The defect is not in vector search or the legacy OpenAI embedding helper itself. The evidence path can pass structured image descriptors, while the legacy helper only understands OpenAI text `input`; feeding image dicts into that helper silently creates text embeddings of serialized dicts. The lowest-complexity fix is an in-process evidence-only adapter registered as `EVIDENCE_RETRIEVAL_EMBEDDING`. This avoids a new compose service, avoids extra deployment state, and keeps legacy `EMBEDDING_FUNCTION` behavior unchanged.

## Implementation Plan
- Add focused tests first for text passthrough, image `messages` payload construction, and unsafe raw external image rejection.
- Add a small evidence-only OpenAI-compatible multimodal adapter module.
- Wire `main.py` so `EVIDENCE_RETRIEVAL_EMBEDDING` uses the adapter when the evidence model/config is OpenAI-compatible; otherwise preserve the existing evidence embedding function.
- Run focused pytest commands and inspect git diff.

## Current Status
- Earlier implementation and focused verification are complete for the evidence embedding adapter slice.
- The default ordinary KB ingest switch is implemented: ordinary Knowledge uploads/updates/batch add now run evidence projection unconditionally after vector insert, using the unified multimodal profile for projected text and image evidence.
- Generic file uploads and arbitrary/custom collections are intentionally not projected into `knowledge_evidence`; projection requires a real Knowledge row.
- No PaddleOCR loader files were modified by this task. The worktree still contains the pre-existing `uv.lock` change, which remains intentionally unstaged/uncommitted.
