# Agent Mode commentary ordering and hang design

## Problem

Completed Agent runs persist public events in the real order:

```text
assistant_note
tool.requested
action_summary
tool.completed
final.delta
```

Replaying that sequence literally places an assistant commentary message between a tool call and its output. The provider-facing request therefore violates the tool protocol's call/result adjacency. Separately, the runtime retries streaming model callback timeouts three times even though streaming calls bypass server-side operation idempotency, turning one timeout into repeated duplicate work and a much longer apparent hang.

## Considered approaches

### 1. Frontend-only reordering

This would make the timeline look better but leave the malformed provider request unchanged. It does not address the defect.

### 2. Silent repair only at the provider boundary

`AgentModelAuthority` could move any commentary found between a call and result. This protects providers, but it hides the replay producer's invalid semantic output and makes future ordering bugs harder to detect.

### 3. Source canonicalization plus request-level regression (selected)

The history replay builder will treat each tool interaction as a transaction. Intent commentary remains before the call; commentary describing the result is buffered until the matching `tool.completed`, producing:

```text
assistant_note
function_call
function_call_output
action_summary
final_answer
```

An end-to-end test will pass that replay through `AgentModelAuthority` and the Responses converter, asserting that `function_call` and the matching `function_call_output` are adjacent in the actual provider payload.

For the hang path, `httpx.TimeoutException` will no longer trigger an automatic streaming model-call retry. The explicit queued-state rejection remains retryable because it proves the provider call did not begin. This avoids duplicate model work and reduces timeout amplification without inventing a fallback answer.

## Scope

In scope:

- Completed-run replay ordering.
- Provider-facing Responses payload adjacency regression.
- Streaming model callback timeout retry policy.
- Focused backend and AgentScope runtime tests.

Out of scope for this patch:

- Redesigning final-answer streaming when text and tool calls coexist.
- Full enforcement of runtime `timeout_seconds` and `max_tool_calls` budgets.
- Frontend visual changes unrelated to the reported defect.

## Acceptance

- Real persisted event order produces `function_call` immediately followed by its matching `function_call_output`.
- Result commentary follows the tool output; final answer remains last.
- A streaming `httpx.ReadTimeout` performs one model callback attempt, not three.
- The existing Agent Mode backend/runtime suites remain green.
- An isolated PR7 rebuild reproduces a multi-tool second turn without malformed request ordering or a retry-amplified stall.
