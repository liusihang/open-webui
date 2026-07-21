# d72 frontend conflict resolution

## Scope

- Worktree: `/Users/liusihang/openwebui/.worktrees/pr7-live-compatible-20260722`
- Cherry-pick: `d72ffcaca` (`fix(agent-mode): harden durable recovery and interaction`)
- Owned paths: `src/lib/components/chat/AgentEvents/**`, `src/lib/components/chat/Messages/ResponseMessage.svelte`, and `src/lib/apis/agentRuns/**`
- Boundaries: preserve the current live-compatible UI/native phase behavior; do not touch backend, services, migrations, staging, commits, or `cherry-pick --continue`.

## Checkpoints

- [x] Confirm exact worktree, HEAD `18373524cdf8196473cf9fd9b0ecc8014afcc770`, active cherry-pick conflicts, and owned path set.
- [x] Compare stage 2 (live-compatible branch) and stage 3 (`d72ffcaca`) semantics per conflicted file.
- [x] Resolve all conflict markers in owned paths while retaining mature UI and merging reliability/accessibility/privacy changes.
- [x] Run Prettier and focused frontend Vitest/compile checks.
- [x] Recheck unresolved paths and report results to the parent agent.

## Current findings

- Owned unmerged paths are confined to eleven files under `src/lib/components/chat/AgentEvents/`.
- `src/lib/apis/agentRuns/index.ts` and `index.test.ts` are already merged by Git and remain in scope for verification.
- `ResponseMessage.svelte` is not currently modified or conflicted by this cherry-pick.
- Backend/migration conflicts remain and are owned by the parallel backend lane.

## Resolution summary

- Preserved the live-compatible Codex-like collapsible transcript, native phase rendering, theme tokens, i18n, reduced-motion behavior, and quiet detail disclosure.
- Added run-state gating: unresolved approvals are pending only in `waiting_approval`, unresolved user inputs only in `waiting_user_input`; otherwise they render `stale` and expose no controls.
- Kept API-backed approval/input actions and added stable per-attempt idempotency keys plus optimistic submitted state.
- Adopted the d72 schema parser/validator for Codex-style questions, mixed schemas, optional-value omission, integer/number validation, and JSON array/object normalization.
- Added accessible pressed state, explicit custom-answer label/id linkage, validation announcements, and retained keyboard focus styles.
- Kept recursive transcript privacy sanitization, including normalized nested `debug` key removal.
- Replaced the bridge's manual reconnect loop with the tested connection helper while retaining the mature connection-state UI; reconnects backfill first, stop on permanent 4xx responses, and stop after the failure cap.

## Verification

- Conflict marker scan over owned paths: clean.
- `git diff --check` over owned paths: clean.
- Focused Vitest: 8 files, 118 tests passed.
- `AgentRunStatusBridge.compile.test.ts`: all five Svelte components compiled.
- Filtered `svelte-check --output machine` for `src/lib/components/chat/AgentEvents` and `src/lib/apis/agentRuns`: no diagnostics.
- A broader filtered check still reports existing `ResponseMessage.svelte` baseline diagnostics unrelated to this cherry-pick; the file itself was already auto-merged and required no manual change.
- Per task boundary, files were not staged and the cherry-pick was not continued; Git still labels manually resolved paths `UU`/`AA` until the parent agent stages them.
