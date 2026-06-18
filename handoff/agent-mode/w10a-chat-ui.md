# W10A Frontend Agent Event UI Handoff

Date: 2026-06-18

## Goal

Integrate the Agent Mode event UI into the chat surface after the backend event
fixtures are stable.

Reducer/API prep can start now from existing W10 helper work, but the
`Chat.svelte` integration should merge only after W9B1 fixtures are checked in.

## Base

- Worktree:
  `/Users/liusihang/openwebui/.worktrees/agent-mode-w10-chat-ui`
- Branch: `codex/agent-mode-w10-chat-ui`
- Base commit: `28830b966`

## Read-Only Context

- `/Users/liusihang/openwebui/docs/plans/2026-06-17-openwebui-agent-mode-agentscope-implementation.md`
- `/Users/liusihang/openwebui/docs/plans/2026-06-18-openwebui-agent-mode-runtime-contracts.md`
- `/Users/liusihang/openwebui/docs/plans/2026-06-17-openwebui-agent-mode-agentscope-design.md`
- `/Users/liusihang/openwebui/docs/adr/0002-agent-mode-runtime-boundaries.md`

## Owned Files

- frontend agent event API/store/components
- focused frontend tests
- one controlled integration in `src/lib/components/chat/Chat.svelte`
- This handoff

## Must Not Touch

- backend runtime/model/tool authority logic
- `services/agentscope-runtime/*`
- nested `open-terminal/`

## Required First Step

Inspect the integrated W10 helper slice and write failing tests first. Record
the red command/result here.

Required behavior tests:

- Agent Event Stream reconnect backfills by sequence;
- concise action summaries render by default;
- approval, artifact, tool, warning/error, and subagent details are expandable;
- raw reasoning is never displayed;
- final answer renders only after final-answer phase;
- socket incremental assistant content does not duplicate Agent Mode SSE final
  deltas for messages with `agent_run_id`;
- legacy chat rendering still works when Agent Mode is disabled.

## Verification To Record

- Focused Vitest for event reducer/API/helpers.
- New `Chat.svelte` integration test if supported by existing frontend test
  patterns.
- `git diff --check`
- Do not change package locks unless dependency work is explicitly required.

## Progress Log

- 2026-06-18 W10A resumed on
  `/Users/liusihang/openwebui/.worktrees/agent-mode-w10-chat-ui`, branch
  `codex/agent-mode-w10-chat-ui`, current HEAD
  `6e486bb8359b0234f7ddd520d4c82091406da9e3`.
- Read required handoff and root read-only design/contract/ADR docs.
- Confirmed owned frontend helper slice already exists:
  `src/lib/apis/agentRuns/index.ts`,
  `src/lib/components/chat/AgentEvents/types.ts`,
  `eventFold.ts`, `fixtures.ts`, and focused tests.
- UI/design context note: the project-local Impeccable loader path
  `.agents/skills/impeccable/scripts/load-context.mjs` is absent in this
  worktree. Proceeding from the approved Agent Mode design docs instead of
  blocking on that optional helper.
- Next checkpoint: write failing frontend Vitest coverage for final-phase
  gating, warning/error summaries, expandable UI view-model details, and
  socket/SSE duplicate prevention for messages with `agent_run_id`. Record the
  red command/result below before production code changes.

## TDD Evidence

- RED 2026-06-18:
  `npm run test:frontend -- src/lib/components/chat/AgentEvents/eventFold.test.ts src/lib/components/chat/historySync.test.ts`
  failed as expected.
  - `shouldApplySocketContentEvent is not a function` for Agent Mode socket
    duplicate-prevention tests.
  - `eventFold` dropped unseen reconnect backfill events with lower seq after a
    higher seq had arrived.
  - `eventFold` appended `final.delta` text before `final.started`.
  - Existing passing tests in the same focused run: 14 passed, 5 failed.
  - Note: command entered Vitest watch mode after failure; killed leftover
    `node (vitest)` process `41412`.
- GREEN 2026-06-18:
  `npm run test:frontend -- --run src/lib/apis/agentRuns/index.test.ts src/lib/components/chat/AgentEvents/eventFold.test.ts src/lib/components/chat/historySync.test.ts`
  passed: 3 files, 24 tests. Vitest emitted the existing
  `vite-plugin-static-copy` warning about missing
  `node_modules/onnxruntime-web/dist/*.jsep.*` assets, but exited 0.

## Implementation Notes

- `eventFold` now accepts unseen reconnect/backfill events even when their seq
  is lower than the current max, keeps rendered items sorted by seq, and
  ignores `final.delta` unless the final phase has started.
- Agent Run JSON backfill now uses `/api/v1/agent/runs/{run_id}/events/list`;
  SSE subscriptions still use `/events`.
- Added `AgentRunEvents.svelte` and a small local store for SSE-backed action
  summaries, sanitized expandable details, and final-answer text while the
  persisted message content is still empty.
- `Chat.svelte` records backend `agent_run_id` on the assistant message and
  ignores socket incremental content events for Agent Mode messages to prevent
  duplicate SSE/socket text.

## Verification Notes

- `npm run check` failed repo-wide with existing diagnostics: 9700 errors and
  275 warnings across 388 files, primarily unrelated `i18n` store typing plus
  `FileNav.svelte`, `XTerminal.svelte`, and RichTextInput JS typing issues.
  No failure was isolated to the new AgentEvents files from the truncated
  output.
- Targeted Svelte syntax compile passed:
  `node --input-type=module -e "import { readFileSync } from 'node:fs'; import { compile } from 'svelte/compiler'; for (const file of ['src/lib/components/chat/AgentEvents/AgentRunEvents.svelte','src/lib/components/chat/Messages/ResponseMessage.svelte','src/lib/components/chat/Chat.svelte']) { compile(readFileSync(file, 'utf8'), { filename: file, generate: 'client' }); console.log(file); }"`
- Final focused Vitest after formatting passed:
  `npm run test:frontend -- --run src/lib/apis/agentRuns/index.test.ts src/lib/components/chat/AgentEvents/eventFold.test.ts src/lib/components/chat/historySync.test.ts`
  -> 3 files, 24 tests passed. Same existing `vite-plugin-static-copy`
  warning about missing `onnxruntime-web/dist/*.jsep.*` assets.
- Final targeted Svelte syntax compile passed for
  `AgentRunEvents.svelte`, `ResponseMessage.svelte`, and `Chat.svelte`.
- Final Prettier check passed for all touched files.
- `git diff --check` passed.
