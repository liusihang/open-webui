# Handoff: Make Chat Image Generation Tool-Driven

## Task
- User wants chat image generation to behave as a tool, not as a forced image-generation mode.
- Base branch/worktree:
  - worktree: `/Users/liusihang/.config/superpowers/worktrees/openwebui/codex-retrieval-manifest-opensearch-phase1`
  - branch: `codex/retrieval-manifest-opensearch-phase2-3`
  - base commit: `173c19ede fix(backend): complete evidence tool defaults`

## Scope
- Keep the change backend-focused.
- Change `features.image_generation=true` semantics from "force image generation before the model call" to "allow builtin image tools to be injected".
- Preserve direct image generation endpoints and the existing `chat_image_generation_handler` function for direct/legacy callers unless tests show removal is necessary.
- Do not touch unrelated dirty `uv.lock`.

## Checkpoints
- Checkpoint 1: Baseline verified.
  - Confirmed target worktree and branch are at `173c19ede`.
  - Existing dirty state in this worktree: `uv.lock` only.
  - Relevant current behavior:
    - `backend/open_webui/utils/middleware.py` invokes `chat_image_generation_handler` whenever `features.image_generation` is true.
    - `backend/open_webui/utils/tools.py` already injects `generate_image` / `edit_image` builtin tools when `features.image_generation` remains true and permissions/capabilities allow it.
- Checkpoint 2: Red test added.
  - Added `backend/open_webui/test/util/test_image_generation_tool_mode.py`.
  - Focused command:
    - `env WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=False uv run pytest backend/open_webui/test/util/test_image_generation_tool_mode.py -q`
  - Current failure before implementation:
    - `assert forced_calls == []` fails because `process_chat_payload()` calls `chat_image_generation_handler()` when `features.image_generation=true`.
- Checkpoint 3: Minimal implementation applied.
  - Updated `backend/open_webui/utils/middleware.py`.
  - Removed the automatic `chat_image_generation_handler()` invocation from the `features.image_generation` branch.
  - Left `features.image_generation` intact so native builtin tool resolution can inject `generate_image` / `edit_image`.
  - Kept `chat_image_generation_handler()` itself unchanged for direct/legacy callers.
  - New focused test now passes:
    - `env WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=False uv run pytest backend/open_webui/test/util/test_image_generation_tool_mode.py -q`
- Checkpoint 4: Focused verification completed.
  - Command:
    - `env WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=False uv run pytest backend/open_webui/test/util/test_image_generation_tool_mode.py backend/open_webui/test/util/test_chat_image_generation_handler.py backend/open_webui/test/util/test_bifrostapi_pipe_function.py -q`
  - Result:
    - `14 passed, 6 warnings`
  - Scope check:
    - `uv.lock` remains dirty from before and was not touched for this task.
    - Intended files for this task are `backend/open_webui/utils/middleware.py`, `backend/open_webui/test/util/test_image_generation_tool_mode.py`, and this handoff.
- Checkpoint 5: Scoped commit created.
  - Commit:
    - `fix(backend): make image generation tool-driven`
  - Post-commit verification repeated with the same focused command:
    - `14 passed, 6 warnings`
  - Post-commit status:
    - only pre-existing `uv.lock` remains modified.

## Planned Next Steps
1. If deploying, rebuild from this branch tip so it includes the scoped image-generation tool-mode commit.
2. In UI QA, verify that enabling image generation exposes the image tool but does not generate an image for a non-image request unless the model calls `generate_image`.
