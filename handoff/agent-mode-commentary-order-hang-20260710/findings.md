# Native phase findings

## Requirements

- Commentary shown before tools must be authored by the model.
- Missing commentary should produce a tool card directly.
- Commentary may be displayed with buffering or pseudo-stream delivery.
- Final output must use real provider streaming.
- Backend review/testing remains in scope alongside UI behavior.

## Root cause

- Bifrost upstream emits assistant message phase before its first text delta.
- The repo-managed `bifrostapi` Pipe discards message item phase and reduces text to untyped Chat Completions content.
- The AgentScope callback parser drops phase.
- The bridge converts all text to the same AgentScope delta event.
- The runtime interprets every text event as final output.
- ToolProxy separately creates synthetic first-person notes.

## Provider proof

### Tool probe

```text
seq 2  output_item.added message commentary
seq 4  first output_text.delta, commentary already known
seq 13 commentary message done
seq 14 function_call added
```

### Final probe

```text
seq 4 output_item.added message final_answer
seq 6 first output_text.delta, final_answer already known
```

## Implementation seam

- Pipe output extension: `choices[0].delta.phase` with validated values only.
- Commentary bypasses AgentScope intermediate text streaming and is persisted before tool execution.
- Final-answer content remains the only source of intermediate `TextBlockDeltaEvent`.
- Existing tool transaction replay canonicalization is retained.

## TDD evidence

- Pipe RED command: `pytest -q backend/open_webui/test/util/test_bifrostapi_pipe_function.py -k phase`.
- Expected result observed: valid assistant input phases were `None`; commentary and final normalized content deltas lacked `phase`.
- The invalid/non-assistant phase omission test already passed, so the production change must preserve strict validation rather than copying arbitrary values.
- Callback parser RED: valid `commentary` phase was absent while invalid phase omission already passed.
- Callback parser GREEN: valid `commentary|final_answer` is copied from normalized choice deltas; the full client parser file passes 22 tests.
- Bridge RED: commentary was yielded as final-stream text, unclassified no-tool text did not fail, and `final_answer` plus a tool call did not fail. The four focused tests fail for those exact missing behaviors.
- Bridge GREEN: commentary is written once through a stable model-call transcript block before tool execution or the first final delta; only `final_answer` yields intermediate AgentScope text, and `_on_final_text` receives final text only.
- ToolProxy RED proved every success/failure/approval path still generated runtime-authored `assistant_note` / `action_summary` text and same-round replay entries.
- ToolProxy GREEN removes that text and its live replay cache while retaining structured tool lifecycle events and AgentScope tool results.
- App integration proves commentary persistence completes while the run is still `running`; `finalizing` begins only after the first `final_answer` TextBlock delta.
- A phase-less no-tool stream terminates as `run.failed` with `model_phase_missing` and produces neither `final.started` nor `final.delta` fallback output.
- Expanded backend regression found a separate prior-line defect: `serialize_output()` used `html.escape` in reasoning/tool rendering without importing `html`, causing a real `NameError`; the existing reasoning-line-break test reproduced it.
- Diff review found a strict-terminal gap: commentary-only/no-tool responses and reasoning-only empty public responses were treated as successful terminal model turns, allowing commentary or emptiness to masquerade as a final answer. They now fail explicitly after any valid commentary is persisted.
- Same-run request-body tracing found the original ordering bug still existed for AgentScope-formatted assistant messages: when `content` and `tool_calls` coexist, the Pipe emitted only function calls and discarded the model commentary before them.
- Event persistence and frontend folding are run-global by `block_id`, while model-call counters are per participant; the initial model commentary block id would therefore collide between leader and subagents on `model-call-1`.
- Independent review found provider-native web-search/image display chunks were still phase-less. They now use a distinct `provider_auxiliary` marker and public `action_summary` block, never model commentary or final output.
- Cancellation now polls while the provider is silent and explicitly closes the active stream; malformed merged tool calls now fail before empty-response fallback.
- Commentary and auxiliary block ids include runtime session, participant, and model call, preventing both parallel-subagent and runtime-restart collisions in the run-global event store/UI fold.

## Relevant files

- `tools/openwebui/functions/bifrostapi.py`
- `backend/open_webui/test/util/test_bifrostapi_pipe_function.py`
- `services/agentscope-runtime/agentscope_runtime/openwebui_client.py`
- `services/agentscope-runtime/agentscope_runtime/agentscope_bridge.py`
- `services/agentscope-runtime/agentscope_runtime/app.py`
- `services/agentscope-runtime/tests/test_openwebui_client.py`
- `services/agentscope-runtime/tests/test_agentscope_bridge.py`
- `services/agentscope-runtime/tests/test_app.py`

## Protected boundaries

- `.playwright-cli/`
- `handoff/agent-mode-7e7fd83-image-rebuild-20260710/`
- `handoff/agent-mode-b2e665078-image-rebuild-20260710/`
- `handoff/agentmode-v0102-migration-20260708/`
- Live service and broad Bifrost log inventory
