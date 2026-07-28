# Agent Mode pre-production blocker remediation design

## Context

Pre-production Gates 2–5 found three independent defects on the isolated PR7 stack:

1. Anthropic/Bifrost emits zero-argument tool calls as `arguments: ""`; the AgentScope bridge forwards that string to JSON decoding and the tool fails before execution. Anthropic can also wrap phase markers in a visible Markdown/Chinese label that the current prefix normalizer does not recognize.
2. The backend cancellation endpoint works and terminates an active tool process, but the Agent UI has no stop control wired to it.
3. Historical run rows can remain `running` or `waiting_approval` after their latest persisted event indicates a terminal or resumed transition. The strongest example is run `b7ff7f4b-30fb-4021-b193-fe6cf0da9334`, whose latest event is `run.failed` while the row remains `running`.

## Alternatives

### A. Narrow boundary fixes (recommended)

- Normalize only empty or whitespace-only tool argument strings to `{}` at the AgentScope model bridge. Preserve failures for malformed non-empty JSON.
- Extend the phase-envelope parser only for verified leading label variants, while retaining strict phase validation and final-stream ordering.
- Wire the existing cancel endpoint into the live Agent processing UI and let persisted run events remain authoritative.
- Reproduce the run-state mismatch and fix the state transition write path that permits event/state divergence. Do not conceal divergence at read time and do not bulk-edit historical user rows during implementation.

This is the smallest change set that addresses each observed root cause without changing provider contracts or inventing a second run-state model.

### B. Generic compatibility layer

Add broad provider argument coercion, generic text-marker stripping, optimistic frontend state transitions, and read-time run-state derivation. This covers more variants but risks hiding malformed provider payloads and durable persistence bugs.

### C. Agent runtime protocol redesign

Replace text phase envelopes, cancellation flow, and event/state persistence with a new end-to-end protocol. This may be appropriate later, but it is too large for the current release blockers and would invalidate already-passing OpenAI, replay, concurrency, and configuration gates.

## Selected design

Use Alternative A with three independent implementation owners.

### Anthropic/Bifrost boundary

The bridge converts `""` and whitespace-only argument strings to the canonical JSON object string `"{}"` before constructing the tool-call block. Non-empty malformed JSON remains an error. Regression tests must prove RED against the current bridge and cover empty, whitespace, valid object, and malformed non-empty arguments. A separate marker test must reproduce the exact visible Anthropic label variant and prove that only a leading verified envelope is removed.

### Agent UI cancellation

When an Agent run is actively processing, the composer exposes a real stop action. The action calls the existing authenticated cancel endpoint for the active run, becomes disabled while cancellation is pending, and resolves from server/run events rather than fabricating a local terminal response. It must not conflict with voice-recording cancellation or ordinary non-Agent generation controls. Component/API tests must fail before implementation and cover success, repeated clicks, error recovery, and absence outside an active Agent run.

### Run state consistency

Trace every transition that appends `run.failed`, `run.cancelled`, approval completion, and user-input completion, then identify the exact write path capable of committing the event without the corresponding state. The fix must make the affected transition atomic or otherwise ensure the authoritative state update cannot be skipped. Tests must first reproduce event/state divergence through the real model/repository layer. Historical rows are evidence and post-fix verification targets, not authorization for bulk mutation.

## Integration boundaries

- Anthropic work owns AgentScope bridge/parser files and AgentScope tests.
- UI cancellation work owns frontend Agent runtime/API components and frontend tests.
- State consistency work owns backend run transition persistence and backend tests.
- The primary agent owns conflict review, combined test execution, image rebuild/deployment, live Gate 2/3 reruns, and release recommendation.

## Acceptance

1. Each fix demonstrates a RED test on the unmodified behavior and GREEN after the minimal change.
2. Existing AgentScope, backend Agent run, and frontend Agent UI suites remain green together.
3. Anthropic executes the real zero-argument tools and displays clean commentary/final text.
4. The UI stop control cancels an active long-running tool and persistence ends at `run.cancelled` with no later final.
5. A newly reproduced terminal failure cannot leave the run row non-terminal.
6. OpenAI multi-tool ordering, replay, five-run concurrency, global prompt persistence, container health, and `UVICORN_WORKERS=1` remain unchanged.

## Approved durable decision execution architecture

Incremental lifecycle fixes proved that handler-local approval and user-input resume cannot simultaneously prevent duplicate destructive execution and recover from process failure. The approved replacement is a resource-level durable decision execution protocol.

### Ownership boundaries

- `AgentRunOperation` remains a caller-idempotency receipt. It is not the execution owner.
- A new backend table, `agent_run_decision_execution`, owns one canonical decision for each `(run_id, resource_type, resource_id)` and carries the durable outbox state.
- The AgentScope runtime owns a persistent execution journal and serialized AgentScope checkpoint. Runtime memory is never authoritative for resume deduplication.
- Backend tool authority remains the only executor of system/terminal tools. Runtime supplies the stable decision execution identity and never bypasses backend tool authorization.

### Backend decision execution record

The record contains a stable `execution_id`, run/resource identity, requested-event sequence, runtime session and checkpoint version, canonical decision payload/fingerprint, outbox status, dispatch lease, retry metadata, runtime prepare response, completion event pointer, runtime outcome, and audit timestamps.

Required constraints:

```text
PRIMARY KEY (execution_id)
UNIQUE (run_id, resource_type, resource_id)
INDEX (status, next_attempt_at)
```

Different caller idempotency keys with the same canonical decision bind to the same execution. Conflicting decisions for the same approval or user-input resource return a resource conflict before any runtime call or lifecycle event.

The caller operation receipt and canonical execution row are inserted/completed in one database transaction. Recording a decision does not advance the run and does not execute a tool.

### Runtime protocol

Runtime exposes three internal endpoints:

```text
PUT  /v1/openwebui/runs/{run_id}/executions/{execution_id}
POST /v1/openwebui/runs/{run_id}/executions/{execution_id}/activate
GET  /v1/openwebui/runs/{run_id}/executions/{execution_id}
```

The prepare request includes schema version, runtime session, execution ID, expected checkpoint version, subject ID, command type, canonical payload, and payload fingerprint. Replaying the same execution and payload returns the persisted record; the same execution with another fingerprint is a protocol conflict.

Runtime execution states are:

```text
new -> prepared -> activated -> applying -> applied
any non-terminal -> cancelled | failed | indeterminate | unrecoverable
```

`prepared` means the runtime journal and matching wait checkpoint are durably committed without applying input or executing a tool. `applied` means the resume command has been durably applied once to the Agent checkpoint; it does not claim that the entire run has completed.

### Two-phase ordering

1. The user decision API records the canonical execution and returns its current status, normally HTTP 202.
2. A backend dispatcher leases the outbox row and sends runtime prepare.
3. After durable runtime `prepared` acknowledgement, the backend atomically appends the canonical `approval.completed` or `user_input.*` event, advances the run to `running`, and marks the execution `backend_committed`.
4. The backend activates the same execution ID.
5. Runtime applies the command exactly once to the persisted checkpoint and continues the Agent.
6. Backend query/retry reconciles lost prepare/activate responses without generating another decision, lifecycle event, or input injection.

The resulting persisted event order is:

```text
approval.requested | user_input.requested
runtime prepared acknowledgement
approval.completed | user_input.completed|declined|cancelled|expired
runtime activation
tool/model events
final.started
final.delta*
run terminal event
```

### Runtime checkpoint

The runtime stores execution journal and checkpoint data in SQLite on a persistent volume behind a `RuntimeExecutionStore` interface. The checkpoint includes runtime/run identity, `AgentState`, wait kind and subject, checkpoint version, stable tool-call identity, bridge counters required for replay ordering, applied execution identity, cancellation state, and execution outcome.

Backend-facing tools are represented as AgentScope external executions so that a pending tool call remains serializable before a backend side effect. Approval and user-input are explicit durable waits instead of backend process-local closures or long-lived polling coroutines.

### Approval and user-input behavior

- Approval approved: prepare the suspended tool call, commit `approval.completed`, activate, then replay the original backend tool request with the stable execution identity and original tool-call idempotency key.
- Approval rejected: prepare and commit the decision, activate a rejection result, and never call the tool.
- User-input accepted/declined/cancelled/expired: one resource-level execution wins; activation injects the result into the checkpoint once.
- Timeout is recorded by the same durable decision recorder and races through the same unique resource constraint; it is not written by an in-memory polling coroutine.

Old `_PendingApproval.resume`, backend `_resume_approved_tool`, in-memory approval wait registries, and long user-input polling are removed from the authoritative path.

### Dispatch and recovery

- Dispatcher ownership uses a database lease only to decide which backend worker sends a request. It never guesses whether a tool executed.
- Prepare or activate timeout is reconciled with runtime `GET` before retry.
- Runtime restart reloads the SQLite journal/checkpoint and returns the persisted execution state.
- Backend cancellation atomically cancels unacknowledged execution rows; an acknowledged run follows the normal run-cancellation path.
- A missing or corrupt checkpoint becomes `unrecoverable` and closes the run through one `run.failed` event. It is never guessed or replayed from user text.

### External side-effect guarantee

The protocol provides effectively-once prepare, activation, decision event, and user-input injection. It cannot provide universal exactly-once semantics for arbitrary external side effects when a tool completes externally but its local outcome is not persisted.

For non-idempotent tools, that window becomes `tool_outcome_indeterminate` and automatic replay is prohibited. A tool adapter may opt into safe retry only when it supports the stable execution/tool-call key or an authoritative reconciliation query.

### Implementation split

Backend owner:

- migration and `agent_run_decision_execution` repository;
- decision recording, resource-level conflicts, dispatcher lease and retry;
- prepare/activate/query runtime client;
- lifecycle commit after prepare and cancellation integration;
- removal of direct approval/user-input resume and long-poll authority.

Runtime owner:

- prepare/activate/query schemas and endpoints;
- SQLite execution store and persistent volume configuration;
- checkpoint serialization/recovery;
- external tool and user-input pause/resume;
- stable execution replay and indeterminate outcome handling.

The two owners must implement against the exact endpoint and payload contract above, without editing each other's file domains. Integration, migration review, image construction, and live verification remain the primary agent's responsibility.
