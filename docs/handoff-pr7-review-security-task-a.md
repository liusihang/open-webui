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
