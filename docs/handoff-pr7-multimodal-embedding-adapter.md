# PR7 Multimodal Embedding Adapter Handoff

## Scope
- Worktree: `/Users/liusihang/.config/superpowers/worktrees/openwebui/codex-retrieval-manifest-opensearch-phase1`
- Do not write code in `/Users/liusihang/openwebui`.
- Do not modify PaddleOCR async loader files; another worker owns that area.
- Fix real multimodal embedding only for the evidence vector path, not legacy RAG embedding.

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

## First-Principles Design Decision
The defect is not in vector search or the legacy OpenAI embedding helper itself. The evidence path can pass structured image descriptors, while the legacy helper only understands OpenAI text `input`; feeding image dicts into that helper silently creates text embeddings of serialized dicts. The lowest-complexity fix is an in-process evidence-only adapter registered as `EVIDENCE_RETRIEVAL_EMBEDDING`. This avoids a new compose service, avoids extra deployment state, and keeps legacy `EMBEDDING_FUNCTION` behavior unchanged.

## Implementation Plan
- Add focused tests first for text passthrough, image `messages` payload construction, and unsafe raw external image rejection.
- Add a small evidence-only OpenAI-compatible multimodal adapter module.
- Wire `main.py` so `EVIDENCE_RETRIEVAL_EMBEDDING` uses the adapter when the evidence model/config is OpenAI-compatible; otherwise preserve the existing evidence embedding function.
- Run focused pytest commands and inspect git diff.

## Current Status
- Implementation and focused verification are complete for this slice.
- No PaddleOCR loader files were modified by this task. The worktree still contains pre-existing/other-worker dirty loader and `uv.lock` changes.
