# Agent model stream liveness implementation plan

Design: `docs/plans/2026-07-11-agent-model-stream-liveness-design.md`

## Constraints

- Work only in `/Users/liusihang/openwebui/.worktrees/live-f8106c651-to-v0102`.
- Preserve unrelated dirty and untracked paths.
- Do not scan broad Bifrost logs.
- Observe focused tests fail before changing production code.
- Do not deploy until code, tests, docs, and a source commit are complete.

## Checkpoints

1. [completed] Add red tests for event-loop responsiveness, async Bifrost
   streaming, lifecycle control comments, SSE heartbeat, and layered timeouts.
2. [completed] Implement the generic sync-iterator compatibility bridge and native
   async Bifrost stream with cancellation-safe cleanup.
3. [completed] Implement lifecycle control comments and the Agent model-call
   liveness wrapper.
4. [completed] Split AgentScope model-call connect, read-idle, and total timeouts;
   document configuration and error semantics.
5. [completed] Run focused and expanded backend/runtime suites, static checks, and
   independent code review; fix verified findings. Fresh current counts are
   Agent backend 181, AgentScope runtime 112, and Pipe/Bifrost/Responses/
   model-list/alias/plugin-loader plus migration 79 tests.
6. [in progress] Commit code/tests/docs, rebuild the exact isolated PR7 image, update
   only the isolated live surfaces, eliminate the cold plugin-loader event-loop
   stall, migrate the exact installed Anthropic manifold from blocking
   `requests` to native async HTTP, and run bounded slow-stream/browser tests.

## Completion gates

- The original blocking/timeout behavior is reproduced by tests before fixes.
- A slow provider cannot block unrelated OpenWebUI event-loop work.
- Cold Function/Tool source execution and constructors cannot block unrelated
  OpenWebUI event-loop work, and their process-global initialization remains
  serialized without consuming the shared default executor's worker capacity.
- Synchronous manifold discovery retains its off-loop compatibility bridge;
  async discovery and chat execution remain on their owning server event loop
  and use native nonblocking I/O for correct loop ownership and cancellation.
- The exact installed Anthropic Function is migrated through the supported API
  only after source-hash and snippet verification. Apply requires explicit
  confirmation of an exclusive maintenance window because the API has no CAS,
  rejects redirects, writes a durable `0600` backup, performs a second
  preflight read, and reconciles uncertain POST outcomes without overwriting an
  unknown state. Its live discovery call leaves the event loop responsive.
- Arbitrary blocking third-party async Python is not disguised by a thread
  timeout. Hard containment remains a separate process/service design problem.
- Failed and cancelled cold loads clean up their exact `sys.modules` entry;
  cancellation cleanup remains independent of the request event loop lifetime;
  plugin top-level code and synchronous constructors obey the documented
  loop-independent initialization contract.
- A live but semantically silent stream stays alive through control comments.
- Heartbeats never enter assistant text, transcript persistence, or UI output.
- Cancellation closes the native Bifrost upstream stream.
- Commentary/tool/final phase ordering and genuine final streaming remain green.
- Timeout failures identify connect, idle, or total ownership.
- OpenWebUI owns streaming operation claims; duplicate callbacks never re-POST
  the provider and abandoned streams are finalized as failed operations.
- Isolated PR7 acceptance passes with healthy containers and no restart increase.
- All requested production changes, tests, API/config docs, and TODO status are
  committed.
