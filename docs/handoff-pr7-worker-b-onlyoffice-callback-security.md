# PR7 Worker B - OnlyOffice Terminal Callback Download Security

## Scope

- Worktree: `/Users/liusihang/.codex/worktrees/pr7-review-security-fixes/openwebui`
- Branch: `codex/pr7-review-security-fixes`
- Main files:
  - `backend/open_webui/routers/onlyoffice.py`
  - `backend/open_webui/test/apps/webui/routers/test_onlyoffice_terminal_writeback.py`

## Goal

Fix terminal OnlyOffice callback blob download risks without changing unrelated preview behavior:

- Reject callback blob redirects instead of following them automatically.
- Keep callback URL allowlist semantics for the terminal callback entrypoint.
- Add bounded, streaming blob reads with both `Content-Length` precheck and cumulative byte enforcement.
- Preserve existing terminal temp upload, backup, move, restore semantics.

## Checkpoints

1. Initial inspection complete:
   - `handle_onlyoffice_terminal_callback()` validates the initial callback URL with `_is_allowed_host()`.
   - `_download_onlyoffice_callback_blob()` currently uses `aiohttp.ClientSession.get(callback_url)` with aiohttp default redirect behavior.
   - `_download_onlyoffice_callback_blob()` currently calls `await upstream.read()`, so a very large or chunked response can be fully buffered.
   - Existing terminal writeback tests have fake aiohttp session/response helpers that can be extended for focused coverage.

2. TDD plan:
   - Add a red test for redirect response to `Location: http://127.0.0.1/...` being rejected before upload.
   - Add a red test for oversized chunked body being rejected by cumulative streaming limit.
   - Add a red test for oversized `Content-Length` being rejected before body streaming.
   - Keep or adjust the existing normal saveback test to prove a small file still writes back.

## Current Status

- Test code changed in `test_onlyoffice_terminal_writeback.py`; production code still unchanged.
- Added tests:
  - `test_terminal_callback_rejects_callback_blob_redirect_to_loopback`
  - `test_download_onlyoffice_callback_blob_rejects_oversized_content_length`
  - `test_download_onlyoffice_callback_blob_rejects_chunked_body_over_limit`
- Red test command:
  - `uv run --frozen pytest backend/open_webui/test/apps/webui/routers/test_onlyoffice_terminal_writeback.py -q`
- Red result:
  - `3 failed, 34 passed, 3 warnings`
  - The 3 new tests failed with `Failed: DID NOT RAISE <class 'fastapi.exceptions.HTTPException'>`, confirming current code accepts 302 callbacks and unbounded callback bodies.
- Implementation checkpoint:
  - Added terminal callback blob URL helper that keeps allowlist semantics and rejects literal `localhost`, loopback, private, link-local, multicast, and unspecified IP hosts for terminal saveback downloads.
  - Changed `_download_onlyoffice_callback_blob()` to call `session.get(..., allow_redirects=False)`.
  - Rejects 3xx callback blob responses before any writeback.
  - Rejects oversized `Content-Length` before streaming.
  - Reads `upstream.content.iter_chunked()` with cumulative byte enforcement instead of unbounded `read()`.
- Green test command:
  - `uv run --frozen pytest backend/open_webui/test/apps/webui/routers/test_onlyoffice_terminal_writeback.py -q`
- Green result:
  - `37 passed, 44 warnings`
  - Remaining warnings are existing pytest-asyncio loop-scope, SQLAlchemy `declarative_base`, and short JWT test-key warnings.
- Current-worktree rerun after unrelated worker changes appeared:
  - `.venv/bin/python -m pytest backend/open_webui/test/apps/webui/routers/test_onlyoffice_terminal_writeback.py -q`
  - `37 passed, 44 warnings`
- Extra checks:
  - `git diff --check -- backend/open_webui/routers/onlyoffice.py backend/open_webui/test/apps/webui/routers/test_onlyoffice_terminal_writeback.py docs/handoff-pr7-worker-b-onlyoffice-callback-security.md` passed.
  - `.venv/bin/python -m py_compile backend/open_webui/routers/onlyoffice.py backend/open_webui/test/apps/webui/routers/test_onlyoffice_terminal_writeback.py` passed.
  - `uv.lock` was modified by `uv run`; restored with `git restore uv.lock` because this task must not submit lockfile churn.
- Next step: inspect final diff/status, then commit only the task files if no unrelated local changes are present.
