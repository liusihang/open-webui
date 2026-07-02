# PR7 Agent Mode Refresh Recovery Handoff

## Scope
- Worktree: `/Users/liusihang/.codex/worktrees/pr7-review-security-fixes/openwebui`
- Branch: `codex/pr7-review-security-fixes`
- Start commit verified: `5e51a3aba`
- Do not deploy, restart services, edit backend/services, or print secrets.

## Goal
Fix Agent Mode refresh recovery UI with TDD. A refreshed in-flight Agent Mode chat must show the persisted assistant message, AgentRunEvents backfill/stream, and final answer without duplicate rendering.

## Checkpoints
- [x] Confirm worktree branch and start commit.
- [x] Create this handoff for continuation.
- [x] Inspect frontend history/message/event code and existing tests.
- [x] Add RED frontend test for reload/backfill persisted Agent Mode UI behavior.
- [x] Implement minimal frontend fix inside allowed files.
- [x] Run focused GREEN frontend tests.
- [x] Report changed files, RED/GREEN evidence, and browser retest recommendation. Do not commit.

## Notes
- Root cause found: `mergeHistorySnapshot` did merge an incoming `agent_run_id`, but did not count that field as a changed/renderable assistant update. Empty assistant shells could therefore fail to trigger the UI path that mounts `AgentRunEvents` for backfill/reconnect.
- Existing files of interest: `src/lib/components/chat/historySync.ts`, `src/lib/components/chat/historySync.test.ts`, `src/lib/components/chat/Messages/ResponseMessage.svelte`, `src/lib/components/chat/AgentEvents/AgentRunEvents.svelte`.

## Evidence
- RED 1: `PATH="$HOME/.nvm/versions/node/v22.22.0/bin:$PATH" npm run test:frontend -- src/lib/components/chat/historySync.test.ts` failed in watch mode on `marks persisted Agent Mode run ids as renderable reload changes without duplicating final text`: expected `result.changed` true, got false.
- RED 2: `PATH="$HOME/.nvm/versions/node/v22.22.0/bin:$PATH" npx vitest run src/lib/components/chat/historySync.test.ts -t "recovers an empty Agent Mode assistant shell"` failed: expected `hasRenderableAssistantUpdate` true, got false.
- GREEN: `PATH="$HOME/.nvm/versions/node/v22.22.0/bin:$PATH" npx vitest run src/lib/components/chat/historySync.test.ts` passed 15 tests.
- GREEN: `PATH="$HOME/.nvm/versions/node/v22.22.0/bin:$PATH" npx vitest run src/lib/components/chat/AgentEvents/eventFold.test.ts src/lib/components/chat/AgentEvents/renderModel.test.ts` passed 17 tests.
