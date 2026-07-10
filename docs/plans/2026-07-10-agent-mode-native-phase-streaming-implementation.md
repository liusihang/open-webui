# Agent Mode native phase streaming implementation plan

Design: `docs/plans/2026-07-10-agent-mode-native-phase-streaming-design.md`

## Goal

Preserve model-authored Responses `commentary` and `final_answer` phases through the repo-managed Bifrost Pipe and AgentScope runtime, remove synthetic first-person tool narration, and retain genuine final-answer streaming.

## Constraints

- Work only in `/Users/liusihang/openwebui/.worktrees/live-f8106c651-to-v0102`.
- Preserve all unrelated dirty and untracked paths.
- Do not scan broad Bifrost logs.
- Do not mutate the live service.
- Write and observe failing tests before each production change.
- Do not add a second model call or prompt marker protocol.

## Phase 1: Bifrost Pipe phase preservation

### RED

Add focused tests to `backend/open_webui/test/util/test_bifrostapi_pipe_function.py`:

1. Assistant Responses input preserves `phase=commentary` and `phase=final_answer`.
2. Invalid phase and non-assistant phase are omitted.
3. `response.output_item.added` records message phase by `output_index` and `item_id`.
4. Matching `response.output_text.delta` emits `choices[0].delta.phase`.
5. Commentary followed by a function call preserves commentary-before-call chunk order.
6. Final-answer delta contains `phase=final_answer` before any completion fallback.

Run only the new tests and confirm they fail because phase is absent.

### GREEN

- Add phase validation helper local to the Pipe.
- Copy valid assistant phase in `_messages_to_responses_input`.
- Extend `_new_stream_state` with message phase/type maps.
- Record message item metadata in `_parse_responses_event`.
- Add correlated phase to normalized text delta and text-done fallback chunks.
- Keep tool, reasoning, image, and ordinary Chat Completions behavior unchanged.

Run the focused Pipe tests, then the full Pipe test file.

## Phase 2: AgentScope callback parsing

### RED

Add tests to `services/agentscope-runtime/tests/test_openwebui_client.py` proving:

- normalized chunks expose valid phase;
- invalid phase is dropped;
- phase-less Chat Completions chunks remain unchanged.

Confirm expected failures.

### GREEN

Extend `_parse_openai_chunk` to copy only `commentary` and `final_answer` from the choice delta.

Run the focused client tests.

## Phase 3: Bridge commentary/final split

### RED

Add bridge tests to `services/agentscope-runtime/tests/test_agentscope_bridge.py`:

1. Commentary deltas plus a tool call do not yield intermediate text responses.
2. Model commentary is persisted before the bridge returns the tool-call response.
3. Final-answer deltas still yield intermediate text responses.
4. `_on_final_text` receives final-answer text only.
5. Tool-only responses emit no public narration.
6. Unclassified no-tool text raises `model_phase_missing`.
7. Final-answer text plus a tool call raises `final_phase_with_tool_call`.

Confirm each failure is caused by current untyped handling.

### GREEN

- Maintain commentary, final, and unclassified text buffers per model call.
- Flush commentary through a stable model-authored public transcript callback before tool execution.
- Yield only final-answer content as intermediate AgentScope text chunks.
- Construct the final `ChatResponse` with the correct model text and tool blocks for AgentScope memory.
- Restrict `_on_final_text` to final text.
- Add explicit protocol error types/messages for invalid terminal classification.

Run focused bridge tests and the full bridge test file.

## Phase 4: Remove synthetic tool narration

### RED

Update/add tests proving `OpenWebUIToolProxy` emits structured tool events without `assistant_note` or `action_summary` text callbacks for requested, successful, failed, approval-required, and approval-rejected paths.

Confirm current tests fail because synthetic deltas are present.

### GREEN

Remove `_public_tool_intent_note`, `_public_tool_result_summary`, and their text-delta/live-context call sites when unused. Keep structured event summaries and payloads.

Run focused tool-proxy tests.

## Phase 5: Runtime lifecycle regression

Add or adapt `services/agentscope-runtime/tests/test_app.py` tests proving:

- commentary never starts `finalizing`;
- the first final-answer delta starts `finalizing` once;
- final-answer deltas retain order and incremental delivery;
- cancellation before final leaves no final output;
- protocol errors terminate the run without a fallback answer.

Run the full AgentScope runtime suite.

## Phase 6: Backend and replay regression

Run the established Agent Mode backend groups covering:

- Responses payload conversion;
- replay canonicalization;
- model authority;
- chat entry;
- event/idempotency persistence;
- approval and user input;
- compaction and subagents.

Verify `git diff --check`, focused Ruff, and repo-managed function compilation.

## Phase 7: Review and commit

- Review the complete diff against the approved design.
- Request independent code review from the existing review subagent when available.
- Fix only verified findings.
- Re-run affected tests after review changes.
- Commit production code and tests with a focused Agent Mode message.

## Phase 8: Isolated image and acceptance

- Rebuild a new slim image from the exact commit on `aiserver`.
- Sync the committed `bifrostapi.py` source into the isolated PR7 function row and verify its hash.
- Recreate only `open-webui-pr7`.
- Verify health, version, restart count, and runtime connectivity.
- Run a bounded raw-SSE probe and a two-round browser Agent conversation.
- Verify model commentary precedes tool cards, synthetic narration is absent, final text streams, and the run does not stall.
- Leave the live service unchanged.

## Completion gates

- All new tests observed red before production changes.
- Focused and expanded suites pass.
- No commentary delta enters `final.delta`.
- No runtime-generated first-person transcript text remains.
- Final answer remains genuinely streamed.
- Provider call/output replay adjacency remains valid.
- Isolated PR7 acceptance passes with restart count zero.
- Production code and documentation are committed.
