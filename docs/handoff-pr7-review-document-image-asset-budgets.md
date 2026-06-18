# PR7 Review - Document Image Asset Budgets Handoff

## Scope

- Worktree: `/Users/liusihang/.codex/worktrees/pr7-review-security-fixes/openwebui`
- Branch: `codex/pr7-review-security-fixes`
- Task: fix second-round review items for PDF fallback, Office/OpenDocument zip image budgets, and Storage-backed document image asset persistence.
- Owned code files:
  - `backend/open_webui/retrieval/loaders/pdf_image_assets.py`
  - `backend/open_webui/retrieval/loaders/office_image_assets.py`
  - `backend/open_webui/test/util/test_document_image_asset_loader_budgets.py`

## Checkpoints

1. Confirm target worktree/branch and inspect current loader behavior. Status: completed.
   - Current HEAD: `053dddac5 merge: integrate agentscope agent mode into PR7`
   - Current branch: `codex/pr7-review-security-fixes`
   - Finding: both loader fallbacks materialize local files only; Office reads zip image entries with unbounded `archive.read(entry)`.
2. Add focused red tests for budgets and `Storage.upload_file` usage. Status: completed.
   - Red command: `.venv/bin/pytest backend/open_webui/test/util/test_document_image_asset_loader_budgets.py`
   - Result: 3 failed. Failures showed PDF and Office loaders extracting all fake assets, no skipped budget reasons, and no storage upload-backed `storage_uri`.
3. Implement narrow loader changes without editing `document_image_assets.py` unless unavoidable. Status: completed.
   - `pdf_image_assets.py`: added page/image/single-byte/total-byte budgets; skipped over-budget assets with explicit reasons; persisted extracted images through `Storage.upload_file`; payloads now include stable `storage_uri` and local compatibility path.
   - `office_image_assets.py`: added zip entry/depth/single-entry/total-byte/image-count budgets; prechecks `ZipInfo.file_size` before `archive.read`; persisted extracted images through `Storage.upload_file`; payloads now include stable `storage_uri` and local compatibility path.
   - `document_image_assets.py` was not modified.
4. Run focused tests and hygiene checks. Status: completed.
   - Green command: `.venv/bin/pytest backend/open_webui/test/util/test_document_image_asset_loader_budgets.py backend/open_webui/test/util/test_pdf_image_asset_loader.py backend/open_webui/test/util/test_office_image_asset_loader.py`
   - Result: 10 passed, 3 existing warnings.
   - Ruff command: `.venv/bin/ruff check backend/open_webui/retrieval/loaders/pdf_image_assets.py backend/open_webui/retrieval/loaders/office_image_assets.py backend/open_webui/test/util/test_document_image_asset_loader_budgets.py`
   - Result: all checks passed.
   - Syntax command: `.venv/bin/python -m py_compile backend/open_webui/retrieval/loaders/pdf_image_assets.py backend/open_webui/retrieval/loaders/office_image_assets.py backend/open_webui/test/util/test_document_image_asset_loader_budgets.py`
   - Result: exit 0.
   - Diff hygiene command: `git diff --check -- backend/open_webui/retrieval/loaders/pdf_image_assets.py backend/open_webui/retrieval/loaders/office_image_assets.py backend/open_webui/test/util/test_document_image_asset_loader_budgets.py docs/handoff-pr7-review-document-image-asset-budgets.md`
   - Result: exit 0.
5. Report changed files, test results, and any review item pushback. Status: pending.

## Notes

- Do not touch live services, secrets, or `uv.lock`.
- Preserve unrelated worker changes if the worktree becomes dirty.
- Keep `document_image_assets.py` unchanged unless payload builder changes become strictly necessary.
- Unrelated current worktree changes observed and left alone: `backend/open_webui/retrieval/evidence.py`, `backend/open_webui/routers/knowledge.py`, `docs/handoff-pr7-review-security-task-a.md`, and `backend/open_webui/test/util/test_evidence_content_security.py`.
