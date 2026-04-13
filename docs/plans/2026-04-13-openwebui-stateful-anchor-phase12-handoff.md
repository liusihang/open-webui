# OpenWebUI Stateful Anchor Phase1+2 Handoff

## Goal

Implement Phase 1 and Phase 2 from `docs/plans/2026-04-13-openwebui-stateful-anchor-implementation-plan.md` in this worktree only:
- Phase 1: server-side explicit-anchor capture + safest-slice guarded reuse prep
- Phase 2: lineage rules + durable metadata for anchors
- Exclude Phase 3 frontend slim-payload send mode

## Checkpoints

### Checkpoint 1 (started)
- Action: loaded authoritative plan, inspected branch status, and mapped existing backend paths for Responses streaming/non-stream flows.
- Findings:
  - `backend/open_webui/models/chat_messages.py` currently has no durable anchor fields.
  - `backend/open_webui/routers/openai.py` already forwards `previous_response_id` only if present, but no server logic sets it.
  - `backend/open_webui/utils/middleware.py` processes `response.*` stream events but does not persist upstream `response.id` anchor metadata.
  - `backend/open_webui/env.py` already defines `ENABLE_RESPONSES_API_STATEFUL` (default false), currently unused in middleware/routers for eligibility decisions.
- Next: inspect targeted tests and add failing Phase1/2 tests first (TDD).


### Checkpoint 2 (context/modeling)
- Action: traced request lifecycle `main.py -> process_chat_payload -> generate_chat_completion -> process_chat_response` and identified exact persistence points.
- Findings:
  - final assistant save happens in `non_streaming_chat_response_handler` and stream tail in `streaming_chat_response_handler`.
  - Responses streaming path centralizes event parsing in `handle_responses_streaming_event`, ideal place to extract `response.id` on `response.completed`.
  - server has enough metadata to gate safest-slice mode (`chat_id`, `parent_message_id`, `model`, tools/features flags, current branch state in chat history).
- Decision:
  - implement Phase 2 as strict opt-in shadow mode behind existing `ENABLE_RESPONSES_API_STATEFUL` flag, with fallback default path unchanged.
  - keep Phase 3 frontend send-path untouched.

### Checkpoint 3 (TDD red)
- Action: added focused backend tests for Phase 1+2 and executed them before implementation.
- Command:
  - `/Users/liusihang/openwebui/.venv/bin/python -m pytest backend/open_webui/test/util/test_chat_message_anchor_state.py backend/open_webui/test/util/test_openai_responses_anchor_capture.py backend/open_webui/test/util/test_stateful_anchor_shadow_mode.py backend/open_webui/test/util/test_stateful_anchor_fallback.py -q`
- Result: expected RED (13 failed).
- Failure classes matched intent:
  - missing anchor fields in `ChatMessageModel`
  - missing non-stream anchor extraction helper
  - missing Phase 2 stateful shadow decision/build helpers

### Checkpoint 4 (implementation)
- Action: implemented Phase1+2 core backend wiring.
- Code changes in progress:
  - `chat_messages.py`: persisted anchor metadata into durable usage payload (`_openwebui_anchor_state`) and exposed computed fields (`provider_response_id`, `provider_route`, `anchor_valid`, `anchor_model_id`).
  - `middleware.py`: added non-stream/stream anchor extraction, safe-path shadow eligibility helpers, strict shadow-mode gate, and recursive-call fallback by dropping `previous_response_id` during tool/code-interpreter loops.
- Next: run focused tests and fix any regressions.

### Checkpoint 5 (verification)
- Focused tests (green):
  - `/Users/liusihang/openwebui/.venv/bin/python -m pytest backend/open_webui/test/util/test_chat_message_anchor_state.py backend/open_webui/test/util/test_openai_responses_anchor_capture.py backend/open_webui/test/util/test_stateful_anchor_shadow_mode.py backend/open_webui/test/util/test_stateful_anchor_fallback.py -q`
  - Result: `13 passed`.
- Required bifrost regression (green):
  - `/Users/liusihang/openwebui/.venv/bin/python -m pytest backend/open_webui/test/util/test_bifrostapi_cache_key.py backend/open_webui/test/util/test_bifrostapi_pipe_function.py -q`
  - Result: `13 passed`.

### Checkpoint 6 (Phase1+2 closeout audit)
- Action: reviewed current worktree deltas against Phase 1 and Phase 2 requirements in `docs/plans/2026-04-13-openwebui-stateful-anchor-implementation-plan.md`.
- Scope checked:
  - Phase 1 anchor capture/persistence path (`chat_messages.py`, `middleware.py`, Responses stream/non-stream handling).
  - Phase 2 guarded shadow-mode eligibility and fallback behavior (`_compute_stateful_anchor_shadow_decision`, `_build_stateful_shadow_messages`, `_apply_stateful_anchor_shadow_mode`).
  - explicit exclusion of Phase 3 frontend slim-payload path.
- Conclusion:
  - no additional Phase 1+2 code gaps requiring patching were identified in this closeout pass.

### Checkpoint 7 (closeout re-verification)
- Focused Phase 1+2 tests re-run (green):
  - `/Users/liusihang/openwebui/.venv/bin/python -m pytest backend/open_webui/test/util/test_chat_message_anchor_state.py backend/open_webui/test/util/test_openai_responses_anchor_capture.py backend/open_webui/test/util/test_stateful_anchor_shadow_mode.py backend/open_webui/test/util/test_stateful_anchor_fallback.py -q`
  - Result: `13 passed, 1 warning`.
- Required bifrost regressions re-run (green):
  - `/Users/liusihang/openwebui/.venv/bin/python -m pytest backend/open_webui/test/util/test_bifrostapi_cache_key.py backend/open_webui/test/util/test_bifrostapi_pipe_function.py -q`
  - Result: `13 passed`.

## Current status
- Phase 1 implemented: server-side capture + persistence of explicit anchor metadata from Responses stream/non-stream completions.
- Phase 2 implemented: strict lineage/route/model/tool guards for server-side shadow-mode anchor reuse with immediate replay fallback.
- Phase 3 not implemented (no frontend slim-payload send-path changes).

### Checkpoint 8 (worktree cleanup for Phase1+2-only diff)
- Goal: trim this worktree to only Phase 1+2 deliverables before adding higher-fidelity integration tests.
- Actions:
  - restored `docs/plans/2026-03-25-openwebui-chat-performance-handoff.md` to branch state (out of Phase1+2 scope).
  - removed unrelated bifrost baggage copied into this worktree:
    - `backend/open_webui/test/util/test_bifrostapi_cache_key.py`
    - `backend/open_webui/test/util/test_bifrostapi_pipe_function.py`
    - `tools/openwebui/functions/bifrostapi.py`
- Result:
  - retained only Phase1+2 backend/test/docs deltas in this worktree.

### Checkpoint 9 (higher-fidelity integration test added)
- Goal: increase confidence beyond helper-only tests without expanding into Phase 3.
- Added:
  - `backend/open_webui/test/util/test_stateful_anchor_shadow_mode_integration.py`
- Coverage:
  - exercises `_apply_stateful_anchor_shadow_mode(...)` rather than only raw helper functions
  - verifies eligible linear append rewrites to `[system?, latest user] + previous_response_id`
  - verifies non-eligible request preserves full replay messages and clears stale `previous_response_id`

### Checkpoint 10 (final verification after cleanup + integration test)
- Focused Phase 1+2 tests including the new integration test (green):
  - `/Users/liusihang/openwebui/.venv/bin/python -m pytest backend/open_webui/test/util/test_chat_message_anchor_state.py backend/open_webui/test/util/test_openai_responses_anchor_capture.py backend/open_webui/test/util/test_stateful_anchor_shadow_mode.py backend/open_webui/test/util/test_stateful_anchor_fallback.py backend/open_webui/test/util/test_stateful_anchor_shadow_mode_integration.py -q`
  - Result: `14 passed, 1 warning`
- Note on bifrost regressions:
  - those copied bifrost regression files were intentionally removed during worktree cleanup because they are not part of the Phase1+2 deliverable set in this isolated worktree.
  - they had already been green earlier in this worktree before cleanup, but are no longer runnable here by design.

## Acceptance
- Phase 1 + Phase 2 in this worktree: accepted
- Remaining caveat:
  - validation remains backend-focused; Phase 3 frontend explicit slim-payload send mode is still intentionally out of scope.

### Checkpoint 11 (post-review remediation plan)
- Trigger: a full review of commit `02de84de7` found 4 concrete follow-up issues in the Phase1+2 implementation.
- Remediation targets:
  - normalize or compare `anchor_model_id` consistently so captured Responses model ids can be reused on the next turn.
  - persist anchor metadata for non-streaming Responses payloads even when `choices` is absent but `output` is present.
  - make fallback Responses-route inference recognize provider-prefixed OpenAI model ids such as `openai/gpt-5.4-mini` and `openai/o3`.
  - always sanitize stale `previous_response_id` on every non-eligible / early-return path in shadow-mode preparation.
- Execution plan:
  - worker A owns runtime fixes in `backend/open_webui/utils/middleware.py`.
  - worker B owns test updates in `backend/open_webui/test/util/test_openai_responses_anchor_capture.py`, `backend/open_webui/test/util/test_stateful_anchor_shadow_mode.py`, `backend/open_webui/test/util/test_stateful_anchor_shadow_mode_integration.py`, and any new focused regression tests needed.
- Validation target after integration:
  - rerun focused stateful-anchor tests in this worktree and confirm they reflect real capture->reuse behavior rather than helper-only assumptions.
- Controller note: runtime worker has landed a focused `middleware.py` patch for the 4 reviewed defects; awaiting test worker to update focused regression tests before integration verification.

### Checkpoint 12 (post-review test remediation)
- Owner: worker B (test-only)
- Scope respected: modified only focused test files under `backend/open_webui/test/util/`; no production files changed by this worker.
- Test updates:
  - `test_openai_responses_anchor_capture.py`: added direct non-streaming handler regression for Responses output-without-choices persistence and anchor metadata save.
  - `test_stateful_anchor_shadow_mode.py`: switched eligible anchor fixture to real capture-style bare provider model id.
  - `test_stateful_anchor_fallback.py`: added regression asserting bare captured model ids are accepted against provider-prefixed requested ids.
  - `test_stateful_anchor_shadow_mode_integration.py`: added regressions for prefixed-model auto route inference and early-return stale `previous_response_id` sanitization.
- Verification:
  - `/Users/liusihang/openwebui/.venv/bin/python -m pytest backend/open_webui/test/util/test_openai_responses_anchor_capture.py backend/open_webui/test/util/test_stateful_anchor_shadow_mode.py backend/open_webui/test/util/test_stateful_anchor_shadow_mode_integration.py backend/open_webui/test/util/test_stateful_anchor_fallback.py -q`
  - Result: `17 passed, 1 warning` (`pytest_asyncio` loop-scope deprecation warning)
