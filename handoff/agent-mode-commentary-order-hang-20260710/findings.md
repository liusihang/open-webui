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
