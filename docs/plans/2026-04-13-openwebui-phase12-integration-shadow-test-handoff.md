# OpenWebUI Phase1+2 Integration Test Handoff

## Goal

Add one higher-fidelity integration-style backend test in this worktree to validate Phase 1+2 stateful anchor shadow-mode behavior through a realistic middleware-facing orchestration path, without implementing Phase 3.

## Checkpoints

### Checkpoint 1 (context + target selection)
- Action: inspected existing Phase1+2 helper-level tests and middleware orchestration path.
- Findings:
  - Existing tests validate `_compute_stateful_anchor_shadow_decision` and `_build_stateful_shadow_messages` in isolation.
  - `_apply_stateful_anchor_shadow_mode` is the middleware-facing orchestration function that wires provider route resolution, decision gating, payload rewrite, and fallback behavior.
- Decision:
  - Add one focused integration-style test file under `backend/open_webui/test/util/` that exercises `_apply_stateful_anchor_shadow_mode` with realistic `form_data + metadata + model + chat history` inputs.

### Checkpoint 2 (test design)
- Action: finalized integration-test design against middleware orchestration function.
- Scope:
  - drive `_apply_stateful_anchor_shadow_mode` with realistic request/config/model/chat-history/form-data/metadata inputs.
  - assert eligible linear-append path rewrites payload to `[system?, latest user] + previous_response_id`.
  - assert non-eligible path keeps full replay `messages` unchanged and removes `previous_response_id`.
- Rationale:
  - this validates the composed behavior of route resolution + eligibility gate + payload rewrite/fallback, reducing false confidence from helper-only unit tests.

### Checkpoint 3 (implementation)
- Action: added new integration-style test file `backend/open_webui/test/util/test_stateful_anchor_shadow_mode_integration.py`.
- What it verifies:
  - eligible linear append route rewrites payload to `[system, latest_user]` and sets `previous_response_id` from anchor message.
  - non-eligible append (non-linear) keeps full replay messages and clears stale `previous_response_id`.
- Notes:
  - no production code edits were needed; coverage was increased through test-only change.

### Checkpoint 4 (verification)
- Command 1:
  - `/Users/liusihang/openwebui/.venv/bin/python -m pytest backend/open_webui/test/util/test_chat_message_anchor_state.py backend/open_webui/test/util/test_openai_responses_anchor_capture.py backend/open_webui/test/util/test_stateful_anchor_shadow_mode.py backend/open_webui/test/util/test_stateful_anchor_fallback.py -q`
  - Result: `13 passed, 1 warning`.
- Command 2:
  - `/Users/liusihang/openwebui/.venv/bin/python -m pytest backend/open_webui/test/util/test_bifrostapi_cache_key.py backend/open_webui/test/util/test_bifrostapi_pipe_function.py -q`
  - Result: failed in this worktree: `file or directory not found: backend/open_webui/test/util/test_bifrostapi_cache_key.py`.
- Command 3:
  - `/Users/liusihang/openwebui/.venv/bin/python -m pytest backend/open_webui/test/util/test_stateful_anchor_shadow_mode_integration.py -q`
  - Result: `1 passed`.
