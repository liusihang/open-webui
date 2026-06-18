# PR7 Worker C Handoff: Query Image Ref File ACL

## Scope

Fix the security review finding that `query_image_refs` / image-to-image retrieval can reach `Storage.get_file()` through the default multimodal resolver without enforcing file read ACL.

## Requirements

- Work on branch `codex/pr7-review-security-fixes` in `/Users/liusihang/.codex/worktrees/pr7-review-security-fixes/openwebui`.
- Use TDD: add focused failing tests first, run and confirm red, then implement, then rerun green.
- Enforce baseline file read authorization before reading query image bytes:
  - file owner may read;
  - admin may read;
  - otherwise `has_access_to_file(file_id, "read", user)` must allow.
- Custom resolver hooks may only tighten or customize payload after baseline authorization; they must not bypass baseline ACL.
- Preserve safe ref-scheme filtering; do not add arbitrary URL, `data:`, `file://`, or path image query support.
- Return a unified unsupported/forbidden shape for nonexistent and unauthorized files, without disclosing which case occurred.
- Do not print secrets or submit `uv.lock`.

## Current Checkpoint

- Branch verified: `codex/pr7-review-security-fixes`.
- Current target files inspected:
  - `backend/open_webui/tools/builtin.py`
  - `backend/open_webui/retrieval/vector/multimodal.py`
  - `backend/open_webui/retrieval/evidence.py`
  - `backend/open_webui/test/util/test_evidence_vector_search.py`
  - `backend/open_webui/test/util/test_query_knowledge_evidence_runtime.py`
- Relevant existing behavior:
  - `query_knowledge_evidence()` already validates query image ref shape and metadata allowlist through `collect_allowlisted_query_image_refs()` / `resolve_query_image_refs()`.
  - `resolve_query_image_ref_for_embedding(ref)` currently accepts only `chat:file:` but fetches `Files.get_file_by_id()` and then `Storage.get_file()` without a user/ACL check.
  - `resolve_query_images_for_embedding()` currently lets `EVIDENCE_QUERY_IMAGE_RESOLVER` replace default resolution, which can bypass baseline ACL.
- Planned RED tests:
  - default resolver denies unauthorized `chat:file:{id}` before storage read;
  - owner/admin/granted users can resolve image bytes;
  - missing file and unauthorized file use the same `unsupported_image_query` code/message shape;
  - custom resolver is invoked only after baseline ACL and cannot bypass it.
- RED checkpoint completed:
  - Command:
    `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false DATABASE_ENABLE_SESSION_SHARING=true uv run pytest -q backend/open_webui/test/util/test_evidence_vector_search.py::test_search_multimodal_evidence_denies_query_image_ref_without_file_acl_before_storage_read backend/open_webui/test/util/test_evidence_vector_search.py::test_query_image_ref_missing_and_unauthorized_use_same_error_shape backend/open_webui/test/util/test_evidence_vector_search.py::test_search_multimodal_evidence_allows_query_image_ref_with_granted_file_acl backend/open_webui/test/util/test_evidence_vector_search.py::test_custom_query_image_resolver_cannot_bypass_baseline_file_acl`
  - Result: 4 failed as expected.
  - Observed failures:
    - unauthorized query image ref reached `Storage.get_file()`;
    - `resolve_query_image_ref_for_embedding()` had no `user` parameter;
    - granted shared-user path did not call `has_access_to_file`;
    - custom `EVIDENCE_QUERY_IMAGE_RESOLVER` bypassed baseline file ACL.
- GREEN checkpoint completed:
  - Implemented baseline query-image ref authorization in `backend/open_webui/retrieval/vector/multimodal.py`.
  - `search_multimodal_evidence()` now passes `user` into query image resolution.
  - Default `chat:file:{file_id}` resolution now checks owner/admin/`has_access_to_file(file_id, "read", user)` before reading `Storage.get_file()`.
  - Missing file and unauthorized file now use the same `unsupported_image_query` code/message shape.
  - `EVIDENCE_QUERY_IMAGE_RESOLVER` now runs only after baseline authorization and validates returned refs/file IDs against the authorized refs.
  - Updated existing custom resolver tests to seed an owner file row, so custom payload tests no longer imply ACL bypass.
- Verification checkpoint:
  - RED command rerun after implementation: 4 passed, 1 warning.
  - Full nearby vector file:
    `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false DATABASE_ENABLE_SESSION_SHARING=true uv run pytest -q backend/open_webui/test/util/test_evidence_vector_search.py`
    Result: 25 passed, 1 warning.
  - Final focused suite:
    `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false DATABASE_ENABLE_SESSION_SHARING=true uv run pytest -q backend/open_webui/test/util/test_evidence_vector_search.py backend/open_webui/test/util/test_query_knowledge_evidence_runtime.py backend/open_webui/test/util/test_multimodal_vector_adapter.py backend/open_webui/test/util/test_query_knowledge_evidence_contract.py`
    Result: 61 passed, 6 warnings.
  - Compile:
    `uv run python -m py_compile backend/open_webui/retrieval/vector/multimodal.py backend/open_webui/test/util/test_evidence_vector_search.py`
    Result: passed.
  - Diff hygiene:
    `git diff --check -- backend/open_webui/retrieval/vector/multimodal.py backend/open_webui/test/util/test_evidence_vector_search.py docs/handoff-pr7-worker-c-query-image-acl.md`
    Result: passed.
  - Note: an initial `python3 -m py_compile ...` invocation hung and was interrupted; the same check through `uv run python` passed immediately.
  - `uv.lock` is dirty in the shared worktree but is not part of this task's intended changes.

## Plan

1. Add focused failing tests in `backend/open_webui/test/util/test_evidence_vector_search.py` around resolver ACL behavior.
2. Run only those tests and confirm expected failures.
3. Implement minimal ACL plumbing in `backend/open_webui/retrieval/vector/multimodal.py`; pass `user` from `search_multimodal_evidence()`.
4. Re-run focused tests and nearby evidence/vector suites.
5. Run `git diff --check`, confirm `uv.lock` is not part of the task diff, and update this handoff with final evidence.
