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

The history replay builder treats overlapping tool interactions as one transaction batch. Intent commentary remains before the batch, all calls stay contiguous, matching outputs follow contiguously in completion-event order, and result commentary is emitted only after the output batch:

```text
assistant_note...
function_call...
function_call_output...
action_summary...
final_answer
```

This preserves parallel-tool semantics without placing an assistant message between provider tool calls and their outputs. Legacy summaries without `tool_call_id` are associated when exactly one call is open; ambiguous multi-call summaries trail the complete output batch. Incomplete calls and their associated public commentary are omitted rather than replayed as completed work.

Because each runtime run allocates tool IDs from `tool-call-1`, replay also rewrites every complete historical transaction to a deterministic `replay-<run hash>-<ordinal>` call ID. Calls and outputs are rewritten together, keeping IDs unique across the three historical runs that may coexist in one provider request and below the Responses length limit.

An end-to-end test passes the canonical replay through `AgentModelAuthority` and the Responses converter, asserting the actual provider payload order. Both backend and runtime replay trimming also remove orphan call/output items if a size boundary cuts through a transaction.

For the hang path, `httpx.TimeoutException` no longer triggers an automatic streaming model-call retry. The explicit queued-state rejection remains retryable because it proves the provider call did not begin. Streaming requests perform run/user/model preflight before `StreamingResponse` sends headers, ensuring the runtime receives the structured queued rejection required by that narrow retry. This avoids duplicate model work and reduces timeout amplification without inventing a fallback answer.

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

- Real persisted event order produces a contiguous call batch followed immediately by its matching contiguous output batch.
- Parallel calls, reverse completion, legacy no-ID summaries, failed tools, and incomplete transactions remain provider-safe.
- Multiple historical runs cannot contribute duplicate provider `call_id` values.
- Result commentary follows the output batch; final answer remains last.
- Size trimming cannot leave orphan tool calls or outputs.
- A streaming `httpx.ReadTimeout` performs one model callback attempt, not three.
- A queued streaming call returns structured `model_run_rejected` diagnostics before response headers and remains narrowly retryable.
- The existing Agent Mode backend/runtime suites remain green.
- An isolated PR7 rebuild reproduces a multi-tool second turn without malformed request ordering or a retry-amplified stall.
