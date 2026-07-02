# W12D-1 Runtime / Chat / Finalization Handoff

Date: 2026-06-18

## Goal

Prove live acceptance scenarios 1, 9, and 12 against this worktree, or make
only narrow fixes needed for those scenarios.

Scenarios:

1. Ordinary Q&A uses Agent Mode and streams final answer.
9. Final deltas only stream in final-answer phase.
12. Runtime unavailable is visible failure when Agent Mode is enabled.

## Base

- Worktree:
  `/Users/liusihang/openwebui/.worktrees/agent-mode-w12d-runtime-chat`
- Branch: `codex/agent-mode-w12d-runtime-chat`
- Base commit: `78f4cf294`
- Integration target:
  `/Users/liusihang/openwebui/.worktrees/agent-mode-agentscope-pr7`

## Read-Only Context

- Root implementation plan:
  `/Users/liusihang/openwebui/docs/plans/2026-06-17-openwebui-agent-mode-agentscope-implementation.md`
- Runtime contracts:
  `/Users/liusihang/openwebui/docs/plans/2026-06-18-openwebui-agent-mode-runtime-contracts.md`
- Design:
  `/Users/liusihang/openwebui/docs/plans/2026-06-17-openwebui-agent-mode-agentscope-design.md`
- Controller handoff:
  `/Users/liusihang/openwebui/.worktrees/agent-mode-agentscope-pr7/handoff/agent-mode/controller.md`

## Owned Outputs

- Update this handoff with commands, evidence, fixes, and blockers.
- Write live evidence to:
  `handoff/agent-mode/w12d-runtime-chat-evidence.json`
- If code changes are required, keep them limited to runtime/chat/finalization
  behavior and commit them on this branch.

## Suggested Ports And Paths

- Backend: `http://127.0.0.1:18101`
- AgentScope runtime: `http://127.0.0.1:8111`
- Data dir: `/private/tmp/openwebui-agent-mode-w12d-runtime-data`
- Static dir: `/private/tmp/openwebui-agent-mode-w12d-runtime-static`
- Service token: `test-service-token`

Use long-running terminal sessions for backend/runtime. Short-lived shell
backgrounding with `&`/`nohup` has already been observed to stop services when
the command exits.

## Constraints

- Do not fork or rely on the full brainstorming chat.
- Do not edit tool/terminal/subagent/frontend layout code unless a direct bug in
  scenario 1, 9, or 12 proves it is required.
- Do not mass-format service runtime files; full service-runtime ruff has known
  pre-existing quote/import debt. Use focused tests, `ruff --select F`, and
  diff-check for narrow fixes.
- Do not stage root `uv.lock` churn.

## Required Evidence

- For scenario 1: run id, event sequence, final assistant message, and proof
  that the enabled Agent Mode path was used rather than the legacy chat path.
- For scenario 9: event ordering showing `final.delta` only after
  `final.started`, and no tool/subagent events after finalization begins.
- For scenario 12: runtime-unavailable request produces a failed Agent Run and a
  user-visible error, with no silent fallback to legacy chat.

## Verification

Run the narrow tests you add or touch, plus the smallest relevant existing
backend/runtime tests. If you make code changes, also run:

- `git diff --check HEAD~1..HEAD`
- focused ruff or `ruff --select F` on changed Python files

## Final Response To Controller

Return:

- evidence file path;
- run ids and service URLs used;
- tests run and results;
- commit hash if you changed code;
- blockers, if any.

## W12D-1 Checkpoint - 2026-06-18 Initial Read

Context read before action:

- This handoff.
- Controller handoff:
  `/Users/liusihang/openwebui/.worktrees/agent-mode-agentscope-pr7/handoff/agent-mode/controller.md`.
- Root implementation plan:
  `/Users/liusihang/openwebui/docs/plans/2026-06-17-openwebui-agent-mode-agentscope-implementation.md`.
- Runtime contract addendum:
  `/Users/liusihang/openwebui/docs/plans/2026-06-18-openwebui-agent-mode-runtime-contracts.md`.

Initial state:

- Worktree branch confirmed:
  `codex/agent-mode-w12d-runtime-chat`.
- `git status --short` was clean before this handoff update.
- Assigned service URLs from this handoff:
  - Backend: `http://127.0.0.1:18101`
  - AgentScope runtime: `http://127.0.0.1:8111`
  - Service token: `test-service-token`
- W12C-3 already proved the minimal integrated path through `run.running`;
  this worker must not reuse that as final evidence for scenarios 1, 9, or 12.

Execution plan:

1. Inspect the live harness and current service startup commands.
2. Start backend and AgentScope runtime in long-running terminal sessions on
   the assigned ports.
3. Run a live ordinary Q&A through Agent Mode and capture run id, events, final
   assistant message, and proof the Agent Mode path was used.
4. Inspect event ordering to prove `final.delta` appears only after
   `final.started` and that no tool/subagent events appear after finalization.
5. Stop or mispoint the runtime and trigger a second enabled Agent Mode request
   to prove visible failure with no silent fallback.
6. Write `handoff/agent-mode/w12d-runtime-chat-evidence.json`.
7. If a scoped bug appears, make the smallest fix in this worktree, run focused
   tests, update this handoff, and commit.

## W12D-1 Checkpoint - 2026-06-18 Finalization Gap Investigation

Files inspected:

- `services/agentscope-runtime/agentscope_runtime/app.py`
- `services/agentscope-runtime/agentscope_runtime/openwebui_client.py`
- `services/agentscope-runtime/agentscope_runtime/schemas.py`
- `services/agentscope-runtime/agentscope_runtime/agentscope_bridge.py`
- `backend/open_webui/routers/agent_service.py`
- `backend/open_webui/agent/events.py`
- `backend/open_webui/models/agent_runs.py`
- `backend/open_webui/main.py`
- `scripts/agent_mode/acceptance_harness.py`
- `docs/runbooks/agent-mode-runtime-deployment.md`

Root-cause finding before fixes:

- Backend has a DB-backed `final.delta` callback, but it correctly rejects
  deltas unless the run state is `finalizing` and a `final.started` event exists.
- The runtime service currently accepts a run and appends only `run.running`;
  it does not call OpenWebUI model authority, does not enter the final-answer
  phase, does not post `final.delta`, and does not post `run.completed`.
- There is no service-side state transition callback even though the runtime
  contract addendum lists `state.transition` as a required callback family.
- `agent_run.final_text` is accumulated by final deltas, but completion does
  not write the final assistant message content back to the chat message.

Impact:

- Scenario 12 already has focused backend coverage for runtime-unavailable
  visible failure, but still needs live evidence.
- Scenarios 1 and 9 cannot be live-proven from the current runtime skeleton
  because the integrated stack has no path from ordinary Q&A to
  `final.started -> final.delta -> run.completed`.

Narrow fix direction:

- Add a service callback for state transitions/final completion in
  `backend/open_webui/routers/agent_service.py`, backed by `AgentRuns`.
- Add runtime client methods for state transition and final delta.
- Make ordinary no-tool runtime runs call OpenWebUI model authority, enter
  finalization, stream final delta(s), complete the run, and let OpenWebUI write
  final text into the assistant chat message.
- Start with failing tests before implementation.

## W12D-1 Checkpoint - 2026-06-18 Narrow Finalization Fix

Red tests added and observed:

- `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/agent/test_agent_run_routes_db_store.py -k state_transition_completed_writes_final_text_to_chat`
  - First attempt exposed a test monkeypatch target issue; corrected the test
    to inject `Chats` at module level.
  - Proper red failure: `POST /api/agent/service/runs/{run_id}/state-transition`
    returned `404`.
- `cd services/agentscope-runtime && uv run --extra test pytest -q tests/test_openwebui_client.py -k 'append_final_delta or transition_state'`
  - Red failure: `OpenWebUIClient` had no `append_final_delta` or
    `transition_state`.
- `cd services/agentscope-runtime && uv run --extra test pytest -q tests/test_app.py -k finalizes_ordinary_qa`
  - Red failure: `create_app()` had no `auto_finalize_ordinary_qa` argument and
    no ordinary-Q&A finalization loop.

Implementation:

- Added `AgentStateTransitionAppend` protocol schema.
- Added service `state-transition` callback in
  `backend/open_webui/routers/agent_service.py`.
- State transition callbacks use the `state.transition` operation ledger and
  cache duplicate successful responses.
- On completed runs, OpenWebUI writes `agent_run.final_text` back to the
  assistant chat message with `done=true`.
- Added runtime callback schemas and `OpenWebUIClient.append_final_delta()` /
  `OpenWebUIClient.transition_state()`.
- Runtime `create_app()` now starts an ordinary-Q&A finalization loop after
  `run.running`: model callback, `finalizing`, `final.started`,
  `final.delta`, `completed`, and `run.completed`.

Green verification:

- `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/agent/test_agent_run_routes_db_store.py -k state_transition_completed_writes_final_text_to_chat`
  -> `1 passed, 6 deselected`.
- `cd services/agentscope-runtime && uv run --extra test pytest -q tests/test_openwebui_client.py -k 'append_final_delta or transition_state'`
  -> `2 passed, 6 deselected`.
- `cd services/agentscope-runtime && uv run --extra test pytest -q tests/test_app.py -k finalizes_ordinary_qa`
  -> `1 passed, 7 deselected`.

## W12D-1 Checkpoint - 2026-06-18 Live Model Authority Gap

Live service setup:

- Backend:
  `http://127.0.0.1:18101`
- AgentScope runtime:
  `http://127.0.0.1:8111`
- Fake OpenAI provider:
  `http://127.0.0.1:18109/v1`
- Data dir:
  `/private/tmp/openwebui-agent-mode-w12d-runtime-data`
- Static dir:
  `/private/tmp/openwebui-agent-mode-w12d-runtime-static`
- Runtime service token:
  `test-service-token`

Observed blocker:

- Authenticated `/api/models?refresh=true` returned
  `w12d-fake-model`, proving the product catalog saw the fake provider model.
- A live Agent Mode chat still failed after `run.running`:
  - Failed run id:
    `b14b36cb-d669-48f4-91c3-0740624e57af`
  - Error:
    `model_not_allowed: Model not found`
- Root cause:
  `AgentModelAuthority._resolve_authorized_model()` always called
  `check_model_access()`. For provider-catalog models with no DB `model` row,
  `check_model_access()` returns `Model not found`. The product chat path
  already skips that DB access check for admins when
  `BYPASS_ADMIN_ACCESS_CONTROL=true` or globally when
  `BYPASS_MODEL_ACCESS_CONTROL=true`; internal Agent Mode model callbacks did
  not mirror that parity.

Red/green fix:

- Added a regression to
  `backend/open_webui/test/agent/test_model_authority.py`:
  `test_admin_model_call_uses_product_chat_access_bypass_for_provider_model`.
- Red command:
  `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/agent/test_model_authority.py -k admin_model_call_uses_product_chat_access_bypass_for_provider_model`
  -> failed because the admin/provider model path still invoked the access
  checker.
- Narrow implementation:
  `backend/open_webui/agent/model_authority.py` now skips the internal
  model-access checker only under the same admin/global bypass rules used by
  product chat.
- Green command:
  `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/agent/test_model_authority.py -k admin_model_call_uses_product_chat_access_bypass_for_provider_model`
  -> `1 passed, 5 deselected`.

After the fix, backend was restarted on the same assigned port so live evidence
used the patched code.

## W12D-1 Checkpoint - 2026-06-18 Live Scenario Evidence

Evidence file written:

- `handoff/agent-mode/w12d-runtime-chat-evidence.json`

Scenario 1, ordinary Q&A uses Agent Mode and streams final answer:

- Live run id:
  `ff800529-1725-450e-b24c-b6e06eb5b749`
- Runtime session id:
  `rt_ff800529-1725-450e-b24c-b6e06eb5b749_e76VIXH24L8`
- Chat id:
  `fc21b0b7-d5dc-4922-a612-b71ee6bfee1f`
- Assistant message id:
  `w12d-assistant-1781750118257`
- `/api/chat/completions` response:
  `status=true`, included `agent_run_id`, included `runtime_session_id`, and
  returned no legacy provider completion body.
- Event sequence from `/api/agent/runs/{run_id}/events/list`:
  1. `run.running` phase `running`
  2. `final.started` phase `finalizing`
  3. `final.delta` phase `finalizing`, delta text
     `Agent Mode final answer from W12D fake provider.`
  4. `run.completed` phase `completed`
- Run detail:
  state `completed`, state version `3`, no error.
- Chat detail:
  assistant message content
  `Agent Mode final answer from W12D fake provider.`, `done=true`,
  model `w12d-fake-model`, and matching `agent_run_id`.

Scenario 9, final deltas only stream in final-answer phase:

- Uses the same completed run:
  `ff800529-1725-450e-b24c-b6e06eb5b749`.
- `final.started` seq `2` appears before the only `final.delta` seq `3`.
- Every `final.delta` event has phase `finalizing`.
- Events after `final.started` were only `final.delta` and `run.completed`.
- No tool, subagent, artifact, approval, or model-selection events appeared
  after finalization began.

Scenario 12, runtime unavailable is visible failure when enabled:

- Runtime was stopped with Ctrl-C and `lsof -nP -iTCP:8111 -sTCP:LISTEN`
  returned no listener before the request.
- Live failed run id:
  `310055d5-c926-4f2f-9611-c82a84b15c2c`
- Chat id:
  `3787b89d-dfd8-42bf-a149-6c61158898aa`
- Assistant message id:
  `w12d-unavail-assistant-1781750152421`
- `/api/chat/completions` response:
  `status=false`, included `agent_run_id`, and returned error code
  `agent_runtime_unavailable`.
- Run detail:
  state `failed`, state version `1`, error code
  `agent_runtime_unavailable`.
- Event list:
  seq `1`, event `run.failed`, phase `failed`.
- Chat detail:
  assistant message retained the matching `agent_run_id`, empty content, and a
  visible error message. No `final.delta`, no `run.completed`, and no legacy
  provider answer were produced.

## W12D-1 Checkpoint - 2026-06-18 Verification

Fresh verification after the final code and evidence updates:

- Backend focused gate:
  `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/agent/test_agent_run_routes_db_store.py backend/open_webui/test/agent/test_events.py backend/open_webui/test/agent/test_chat_entry_agent_mode.py backend/open_webui/test/agent/test_model_authority.py`
  -> `41 passed, 9 warnings`.
- AgentScope runtime service-local gate:
  `cd services/agentscope-runtime && uv run --extra test pytest -q tests/test_app.py tests/test_openwebui_client.py`
  -> `16 passed`.
- Focused ruff:
  `uv run ruff check --select F backend/open_webui/agent/protocol.py backend/open_webui/agent/model_authority.py backend/open_webui/routers/agent_service.py backend/open_webui/test/agent/test_agent_run_routes_db_store.py backend/open_webui/test/agent/test_model_authority.py services/agentscope-runtime/agentscope_runtime/app.py services/agentscope-runtime/agentscope_runtime/openwebui_client.py services/agentscope-runtime/agentscope_runtime/schemas.py services/agentscope-runtime/tests/test_app.py services/agentscope-runtime/tests/test_openwebui_client.py`
  -> passed.
- Evidence JSON parse:
  `python3 -m json.tool handoff/agent-mode/w12d-runtime-chat-evidence.json`
  -> passed.
- W12 harness validation for owned scenarios:
  custom `validate_evidence(..., require_live=True)` check over
  `handoff/agent-mode/w12d-runtime-chat-evidence.json`
  -> owned scenarios 1, 9, and 12 valid; `valid_cases=3`;
  `expected_missing_cases=9` because this fragment intentionally covers only
  W12D-1.
- Diff whitespace check:
  `git diff --check -- <changed W12D-1 files>`
  -> passed.

Lockfile handling:

- Root `uv.lock` was modified by `uv run` environment resolution and restored
  before commit.

Remaining blockers:

- None for W12D-1 scenarios 1, 9, and 12.
- Full W12 acceptance still depends on W12D-2, W12D-3, W12D-4, and W12D-5
  evidence for the other nine scenarios.
