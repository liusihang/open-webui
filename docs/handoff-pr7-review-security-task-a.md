# PR7 Review Security Fixes - Task A Handoff

## Scope

- Worktree: `/Users/liusihang/.codex/worktrees/pr7-review-security-fixes/openwebui`
- Branch: `codex/pr7-review-security-fixes`
- Task: tighten PaddleOCR/document image asset path and remote download security boundaries.

## Checkpoints

1. Confirm branch/worktree and inspect existing resolver/materializer/loader behavior. Status: completed.
2. Add focused red tests for local path containment, symlink escape, remote redirect/oversize blocking, and PaddleOCR JSONL limits. Status: completed.
   - Red command: `.venv/bin/pytest backend/open_webui/test/util/test_document_image_assets.py backend/open_webui/test/util/test_paddleocr_vl_loader.py`
   - Result: 8 failed, 8 passed. Failures covered absolute path acceptance, symlink escape, redirect/oversize remote image handling, and JSONL redirect/size limits.
3. Implement narrow security checks in `document_image_assets.py` and `paddleocr_vl.py`. Status: completed.
   - Local paths from OCR/markdown now must be relative and resolve inside the source directory or explicit `image_asset_roots`.
   - Remote image downloads now require allowed origins, public DNS/IP resolution, redirect revalidation, and per-image/document byte/count limits.
   - PaddleOCR JSONL downloads now use manual redirect handling, jobs-origin/default allowlist checks, and chunk-level response/line/line-count limits.
4. Run focused green tests and lightweight hygiene checks. Status: completed.
   - Green command: `.venv/bin/pytest backend/open_webui/test/util/test_document_image_assets.py backend/open_webui/test/util/test_paddleocr_vl_loader.py`
   - Result: 16 passed, 4 warnings from existing dependencies/config.
   - Ruff command: `.venv/bin/ruff check backend/open_webui/retrieval/document_image_assets.py backend/open_webui/retrieval/loaders/paddleocr_vl.py backend/open_webui/test/util/test_document_image_assets.py backend/open_webui/test/util/test_paddleocr_vl_loader.py`
   - Result: all checks passed.
   - Syntax command: `.venv/bin/python -m py_compile backend/open_webui/retrieval/document_image_assets.py backend/open_webui/retrieval/loaders/paddleocr_vl.py backend/open_webui/test/util/test_document_image_assets.py backend/open_webui/test/util/test_paddleocr_vl_loader.py`
   - Result: exit 0.
   - Diff hygiene command: `git diff --check -- backend/open_webui/retrieval/document_image_assets.py backend/open_webui/retrieval/loaders/paddleocr_vl.py backend/open_webui/test/util/test_document_image_assets.py backend/open_webui/test/util/test_paddleocr_vl_loader.py docs/handoff-pr7-review-security-task-a.md`
   - Result: exit 0.
5. Stage/commit only task-owned files if verification passes and no unrelated changes are included. Status: completed.
   - Staged files: `document_image_assets.py`, `paddleocr_vl.py`, their two focused pytest files, and this handoff.
   - Unrelated worker changes in evidence/indexing/review handoff files were left unstaged.

## Notes

- Do not touch live/remote systems.
- Do not stage `uv.lock`.
- Preserve unrelated worker changes if the branch becomes dirty during the task.

## 2026-06-18 Continuation: Evidence Content And Model Image Hydration Security

Scope for this continuation:
- `backend/open_webui/retrieval/evidence.py`
- `backend/open_webui/routers/knowledge.py`
- `backend/open_webui/test/util/test_evidence_content_security.py`

Checkpoints:
1. Confirm target worktree and branch. Status: completed.
   - Worktree: `/Users/liusihang/.codex/worktrees/pr7-review-security-fixes/openwebui`
   - Branch: `codex/pr7-review-security-fixes...origin/pr/7/head`
   - Existing unrelated worker changes observed and left untouched: `backend/open_webui/retrieval/loaders/office_image_assets.py`, `backend/open_webui/retrieval/loaders/pdf_image_assets.py`, `backend/open_webui/test/util/test_document_image_asset_loader_budgets.py`, `docs/handoff-pr7-review-document-image-asset-budgets.md`.
2. Add focused red tests for model image hydration budgets and evidence content response security. Status: completed.
   - Red command: `.venv/bin/pytest backend/open_webui/test/util/test_evidence_content_security.py`
   - Result: 4 failed. Failures confirmed unbounded model image base64 hydration, missing `X-Content-Type-Options: nosniff`, and unsafe MIME thumbnail serving.
3. Implement narrow fixes. Status: completed.
   - Model image hydration now enforces per-image byte, total byte, and pixel budgets before base64 hydration, and uses a bounded file read instead of `Path.read_bytes()`.
   - Budget-exceeded images are skipped from `model_only_files`, with `metadata.model_image.code == "image_budget_exceeded"` on the corresponding result so text evidence remains intact.
   - Evidence file responses now include `X-Content-Type-Options: nosniff`.
   - Evidence image content/thumbnail inline serving is restricted to safe image MIME types; unsafe MIME types are rejected before FileResponse.
4. Focused verification. Status: completed.
   - Green command: `.venv/bin/pytest backend/open_webui/test/util/test_evidence_content_security.py`
   - Result: 4 passed, 6 existing warnings.
   - Regression command: `.venv/bin/pytest backend/open_webui/test/util/test_query_knowledge_evidence_runtime.py backend/open_webui/test/apps/webui/routers/test_knowledge_evidence_api.py backend/open_webui/test/util/test_evidence_content_security.py`
   - Result: 22 passed, 6 existing warnings.
5. Remaining checks. Status: completed.
   - Ruff command: `.venv/bin/ruff check backend/open_webui/test/util/test_evidence_content_security.py`
   - Result: all checks passed.
   - Syntax command: `.venv/bin/python -m py_compile backend/open_webui/retrieval/evidence.py backend/open_webui/routers/knowledge.py backend/open_webui/test/util/test_evidence_content_security.py`
   - Result: exit 0.
   - Diff hygiene command: `git diff --check -- backend/open_webui/retrieval/evidence.py backend/open_webui/routers/knowledge.py backend/open_webui/test/util/test_evidence_content_security.py docs/handoff-pr7-review-security-task-a.md`
   - Result: exit 0.
   - Note: full-file ruff on `evidence.py`/`knowledge.py` still reports pre-existing unrelated import/complexity/line-length debt, so this continuation kept lint cleanup to the new focused test file and verified production files with py_compile plus focused tests.
