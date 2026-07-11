# Agent model stream liveness design

Status: approved by the user on 2026-07-11.

Related design: `docs/plans/2026-07-10-agent-mode-native-phase-streaming-design.md`

## Problem

An Agent Mode model call can return HTTP 200 headers and then remain silent long
enough for the AgentScope callback client to raise `httpx.ReadTimeout`. In the
same request path, a repo-managed Pipe may expose a synchronous iterator backed
by blocking `requests.iter_lines()`. The async OpenWebUI response consumes that
iterator directly, so one slow provider request can occupy the sole Uvicorn
event loop and make the application appear frozen.

The incident is therefore not fixed by increasing one timeout. The stream must
be cancellable and event-loop safe, transport activity must be represented
without becoming assistant text, and timeout ownership must be explicit.

## Required invariants

1. Waiting for provider bytes never blocks the OpenWebUI event loop.
2. Cancelling the downstream request closes the native upstream stream promptly.
3. Agent model-call SSE sends an immediate control frame and periodic heartbeat
   frames while the next provider frame is pending.
4. Control frames are SSE comments. They are ignored by model parsing and are
   never persisted to the transcript or rendered in the UI.
5. Known Responses lifecycle events are forwarded as control comments instead
   of being silently discarded.
6. Model commentary, tool calls, tool outputs, and final-answer deltas retain the
   native phase and ordering contract from the existing phase-streaming design.
7. Connection establishment, read idleness, and total model-call duration have
   separate limits and separate failure messages.

## Architecture

### Native asynchronous Bifrost stream

The streaming Bifrost Pipe uses `httpx.AsyncClient.stream()` and
`response.aiter_lines()`. Its stream branch returns an async generator from the
Pipe without opening a blocking request first. Retry/fallback decisions remain
bounded to failures that occur before semantic output is committed.

The existing synchronous request path may remain for non-streaming calls, but
it is not used by the Agent Mode streaming route.

### Safe compatibility bridge for synchronous Pipes

OpenWebUI still supports third-party Pipes that return synchronous iterators.
The generic function bridge advances those iterators in a worker thread instead
of executing `next()` in the event loop. This is a compatibility boundary, not
the Bifrost implementation: Bifrost itself remains natively asynchronous so
downstream cancellation can close its socket.

### SSE control protocol

The Agent model-call route wraps the provider body iterator with a liveness
stream. It yields an immediate comment and, while a single pending `anext()`
task waits for the next provider frame, yields periodic comments:

```text
: openwebui-stream-start

: openwebui-keep-alive

data: {provider chunk}
```

The wrapper does not cancel and recreate `anext()` on every heartbeat. It keeps
one pending task, which prevents accidental cancellation or closure of the
provider async generator. On downstream cancellation it cancels that task and
closes the source iterator when supported.

Responses lifecycle events such as `response.created` and
`response.in_progress` become SSE comments. They prove upstream protocol
activity without fabricating an OpenAI text delta or polluting history.

### Layered timeout model

The AgentScope callback client uses three independent settings:

- connect timeout: time to establish the OpenWebUI request;
- read-idle timeout: maximum time without receiving any SSE bytes;
- total model-call timeout: maximum wall-clock duration for the whole stream.

The heartbeat interval must be lower than the read-idle timeout. A healthy but
slow provider therefore does not trigger a false idle timeout, while an
unreachable or wedged OpenWebUI stream still fails. The total timeout remains a
hard upper bound and is not extended by heartbeat traffic.

Defaults are chosen from the existing run budget: short connection timeout,
read-idle timeout comfortably above the heartbeat cadence, and a total timeout
that does not exceed the Agent run timeout.

## Failure semantics

- connect failure: callback connection could not be established;
- read-idle timeout: no SSE bytes, including control frames, arrived in time;
- total timeout: the model call remained active beyond its wall-clock budget;
- provider error frame: preserved as the existing structured stream error;
- cancellation: propagated; it is not rewritten as a timeout or fallback answer.

No automatic second model call and no synthetic final answer are introduced.

## Test strategy

1. A deliberately blocking synchronous iterator must not prevent a concurrent
   event-loop ticker from running.
2. The Bifrost streaming path must use an async iterator and close on
   cancellation.
3. Lifecycle events must appear as SSE comments and never as assistant content.
4. A silent async source must emit stream-start and keep-alive comments before
   its first data frame, without cancelling the pending source read.
5. The callback client must ignore comments while they reset HTTP read idleness.
6. Connect, read-idle, and total timeouts must be independently configurable and
   distinguishable in focused tests.
7. Existing native phase, tool ordering, final streaming, replay, cancellation,
   and runtime lifecycle suites must remain green.
8. The exact isolated PR7 image must pass a slow-first-semantic-event probe and
   remain responsive to an unrelated health/API request during the wait.

## Live acceptance boundary

Only the isolated PR7 stack on `aiserver` is changed. The image must be built
from the committed worktree revision, only the required containers/function row
may be updated, and broad Bifrost log scans remain prohibited. Acceptance uses
the exact run/request identity, bounded logs, health/restart counters, raw SSE
timing, and a browser conversation.
