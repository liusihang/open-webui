# Agent Mode native phase streaming design

Status: approved on 2026-07-10.

## Problem

Agent Mode currently shows runtime-authored text such as `I will use ...` before tools, while the model-authored Responses transcript is flattened into untyped Chat Completions content. The same untyped text is consumed by AgentScope as `TextBlockDeltaEvent`, and the runtime treats the first text delta as the beginning of the final answer.

This creates two defects:

1. The visible tool narration is not what the model wrote.
2. Real model commentary can enter `final.delta`, move the run into `finalizing`, and conflict with later tool execution.

The required behavior is:

```text
model commentary
tool call
tool output
model commentary
tool call
tool output
streamed final answer
```

Tool-prelude commentary may be displayed with buffered or pseudo-stream delivery. The final answer must be genuinely streamed from provider deltas.

## Verified provider contract

A bounded probe used the exact PR7 route:

- OpenWebUI model: `bifrostapi.Cliproxy/gpt-5.4`.
- Pipe upstream: `http://192.168.2.238:18080/v1/responses`.
- Upstream model: `Cliproxy/gpt-5.4`.

No broad Bifrost log scan was performed. Credentials were read in remote process memory and were not printed or written to disk.

The tool probe produced:

```text
response.output_item.added  message phase=commentary
response.output_text.delta  phase already known as commentary
response.output_item.done   message phase=commentary
response.output_item.added  function_call
...
response.completed          [commentary message, function_call]
```

The final-answer probe produced:

```text
response.output_item.added  message phase=final_answer
response.output_text.delta  phase already known as final_answer
...
response.output_item.done   message phase=final_answer
response.completed          [reasoning, final_answer message]
```

The phase therefore arrives before the first text delta. Native phase routing can preserve both model-authored commentary and genuine final streaming without a second model call.

## Considered approaches

### Two-stage acting and finalizer calls

This is provider-independent and makes the second call safe to stream as final output, but it adds latency and cost to every successful run. The verified native phase contract makes this unnecessary.

### Prompt-level `COMMENTARY` and `FINAL` markers

This avoids an extra call but makes correctness depend on prompt compliance and adds marker parsing to every provider. It is weaker than metadata the provider already emits.

### Post-hoc classification from the presence of tool calls

This can classify text as commentary after a tool call is known, but a no-tool final answer must be buffered until the response ends. It cannot satisfy genuine final streaming.

### Native phase passthrough

Selected. It uses the provider's existing `commentary` and `final_answer` message phases and keeps tool events structured.

## Current loss points

### Repo-managed Bifrost Pipe

`tools/openwebui/functions/bifrostapi.py::_messages_to_responses_input` rebuilds assistant message items without copying valid historical phase.

`_parse_responses_event` ignores message items from `response.output_item.added|done`. It converts each `response.output_text.delta` into:

```json
{"choices":[{"delta":{"content":"..."}}]}
```

The normalized chunk no longer identifies commentary or final-answer text.

### AgentScope callback client

`services/agentscope-runtime/agentscope_runtime/openwebui_client.py::_parse_openai_chunk` extracts content, reasoning, and tool calls only.

### AgentScope model bridge

`agentscope_bridge.py::_stream_model_call` emits every content chunk as an intermediate `ChatResponse`. AgentScope converts each one into an untyped `TextBlockDeltaEvent`.

### Runtime finalization

`app.py::_run_leader_streaming` transitions to `finalizing` for every `TextBlockDeltaEvent`.

### Tool proxy narration

`OpenWebUIToolProxy` creates first-person assistant notes and result summaries independently of model output.

## Architecture

### 1. Preserve phase in provider input

When the Bifrost Pipe converts an assistant message to a Responses `message` item, it copies `phase` only when the value is `commentary` or `final_answer`.

Function calls and function-call outputs remain separate Responses items. Phase is never placed between a function call and its matching output.

### 2. Correlate phase in the Bifrost Pipe stream

The Pipe stream state records message metadata by both `output_index` and `item_id` when it receives `response.output_item.added`:

```text
output_index or item_id -> item type, phase
```

When `response.output_text.delta` arrives, the normalized Chat Completions-compatible delta includes the validated phase as an internal extension:

```json
{
  "choices": [
    {
      "delta": {
        "content": "...",
        "phase": "commentary"
      }
    }
  ]
}
```

Ordinary chat consumers may ignore the additional field. Agent Mode consumes it explicitly. Invalid phase values are omitted.

`response.output_text.done` uses the same correlated phase when it supplies a non-delta fallback text value.

### 3. Parse phase in the AgentScope callback client

`_parse_openai_chunk` adds validated `phase` to its internal delta dictionary. It continues accepting normal Chat Completions chunks without phase.

The callback protocol remains internal; no public API or database migration is required.

### 4. Split commentary and final text in the model bridge

The bridge maintains separate buffers for:

- commentary text;
- final-answer text;
- unclassified text;
- tool-call deltas.

Commentary deltas are not emitted as AgentScope intermediate text events. They are buffered and written to the public transcript through `append_text_delta` with:

- phase `running`;
- block kind `assistant_note`;
- payload source `model`;
- stable model-call block and delta identifiers.

The commentary buffer is flushed before the bridge yields the final `ChatResponse` containing tool calls. AgentScope therefore cannot start tool execution before the public model commentary has been persisted.

If a response changes from commentary to final-answer text, commentary is flushed before the first final delta is yielded.

Final-answer deltas remain intermediate `ChatResponse` chunks. AgentScope converts only these chunks to `TextBlockDeltaEvent`, so the existing runtime final-answer streamer remains genuinely incremental.

`_on_final_text` records final-answer text only. Commentary is retained in the AgentScope response context but is not duplicated through live synthetic replay.

### 5. Remove synthetic tool narration

`OpenWebUIToolProxy` stops emitting runtime-generated `assistant_note` and `action_summary` text for requested, completed, failed, approval-required, approval-rejected, and related tool states.

The following structured events remain:

- `tool.requested`;
- `tool.completed`;
- `tool.failed`;
- approval lifecycle events;
- user-input lifecycle events;
- `artifact.registered`.

If the model emits no commentary, the UI displays the tool card directly.

## Strict phase behavior

The selected route is known to provide phase before text. Agent Mode must not silently hide a missing final phase.

- `commentary` text plus tool calls: publish commentary, then execute tools.
- Tool calls with no text: execute tools and show structured cards only.
- Unclassified text plus tool calls: buffer until the tool call is known, then treat the model-authored text as pre-tool commentary.
- `final_answer` text without tool calls: stream immediately as final output.
- Unclassified text without tool calls: fail with a clear `model_phase_missing` protocol error because genuine final streaming cannot be guaranteed.
- `final_answer` followed by a tool call in the same response: fail with `final_phase_with_tool_call`; do not continue as a normal tool round.
- Empty response without tool calls: fail with `empty_model_response`.

These are explicit protocol errors, not fallback answers.

## Persistence and replay

Model commentary is persisted as public transcript text and replayed on later user turns as an assistant Responses message with `phase=commentary`.

Final output remains `final.delta` and is replayed with `phase=final_answer`.

Existing tool transaction canonicalization remains authoritative:

```text
commentary
contiguous function calls
contiguous matching function-call outputs
post-tool summaries when present
final answer
```

Removing synthetic result summaries reduces replay noise but does not change call/output adjacency.

## UI behavior

No new frontend protocol is required.

- Model commentary appears in the existing public transcript presentation.
- Tool lifecycle remains represented by tool cards.
- Final answer continues using the current streamed final-answer surface.
- Runtime-authored first-person narration disappears.

## Tests

### Bifrost Pipe

- Preserve valid assistant input phase.
- Omit invalid or non-assistant phase.
- Record message phase from `output_item.added` by index and ID.
- Attach commentary phase to every matching text delta.
- Attach final-answer phase to every matching text delta.
- Preserve tool-call argument streaming.
- Preserve normal Chat Completions behavior when no phase exists.

### AgentScope callback and bridge

- Parse phase from normalized chunks.
- Commentary plus tool call produces public commentary before `tool.requested`.
- Commentary does not produce `final.delta` or enter `finalizing`.
- Tool-only output produces no narration.
- Final-answer deltas remain genuinely streamed.
- `_on_final_text` excludes commentary.
- Missing final phase fails explicitly.
- A final phase followed by a tool call fails explicitly.

### Runtime and backend regression

- Parallel tools.
- Sequential multi-round tools.
- Approval required, approved, and rejected paths.
- User-input pause and resume.
- Cancellation during commentary and final streaming.
- Streaming callback timeout behavior.
- Replay canonicalization and cross-run call-ID uniqueness.
- Idempotent transcript and final-delta persistence.

## Deployment and acceptance

1. Run focused Pipe, AgentScope runtime, and backend Agent Mode tests.
2. Run the full existing Agent Mode regression groups used for commit `7e7fd83ca2f7`.
3. Rebuild a new slim image from the exact worktree commit.
4. Update the installed repo-managed `bifrostapi` function source in the isolated PR7 database and verify its content hash.
5. Recreate only `open-webui-pr7`; do not mutate the live service.
6. Run a bounded raw-SSE probe and an isolated browser conversation that performs at least two tool rounds.
7. Verify persisted order:

```text
model commentary
tool.requested
tool.completed or tool.failed
model commentary when emitted
next tool transaction
final.started
streamed final.delta
run.completed
```

8. Confirm container health, restart count zero, no browser console errors, and no request stall.

Rollback recreates only the isolated PR7 WebUI and restores the prior installed `bifrostapi` function content. Port `18080` configuration and the live OpenWebUI stack remain unchanged.
