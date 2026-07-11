# Agent model stream liveness implementation plan

Design: `docs/plans/2026-07-11-agent-model-stream-liveness-design.md`

## Constraints

- Work only in `/Users/liusihang/openwebui/.worktrees/live-f8106c651-to-v0102`.
- Preserve unrelated dirty and untracked paths.
- Do not scan broad Bifrost logs.
- Observe focused tests fail before changing production code.
- Do not deploy until code, tests, docs, and a source commit are complete.

## Checkpoints

1. [in progress] Add red tests for event-loop responsiveness, async Bifrost
   streaming, lifecycle control comments, SSE heartbeat, and layered timeouts.
2. [pending] Implement the generic sync-iterator compatibility bridge and native
   async Bifrost stream with cancellation-safe cleanup.
3. [pending] Implement lifecycle control comments and the Agent model-call
   liveness wrapper.
4. [pending] Split AgentScope model-call connect, read-idle, and total timeouts;
   document configuration and error semantics.
5. [pending] Run focused and expanded backend/runtime suites, static checks, and
   independent code review; fix verified findings.
6. [pending] Commit code/tests/docs, rebuild the exact isolated PR7 image, update
   only the isolated live surfaces, and run bounded slow-stream/browser tests.

## Completion gates

- The original blocking/timeout behavior is reproduced by tests before fixes.
- A slow provider cannot block unrelated OpenWebUI event-loop work.
- A live but semantically silent stream stays alive through control comments.
- Heartbeats never enter assistant text, transcript persistence, or UI output.
- Cancellation closes the native Bifrost upstream stream.
- Commentary/tool/final phase ordering and genuine final streaming remain green.
- Timeout failures identify connect, idle, or total ownership.
- Isolated PR7 acceptance passes with healthy containers and no restart increase.
- All requested production changes, tests, API/config docs, and TODO status are
  committed.
