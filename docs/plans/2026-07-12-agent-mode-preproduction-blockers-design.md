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
