# Task Plan: ChatGPT-style composer model and reasoning controls

## Goal

Implement a single-model ChatGPT-style composer model/reasoning menu with a synchronized navbar selector and four Bifrost-native effort levels, then verify the exact request contract without touching formal live.

## Current Phase

Complete

## Phases

### Phase 0: Requirements and approved design

- [x] Inspect the current ChatGPT UI and loaded source bundles.
- [x] Inspect current OpenWebUI model and Bifrost reasoning paths.
- [x] Confirm composer-primary/navbar-secondary placement.
- [x] Confirm removal of new multi-model comparison.
- [x] Confirm four effort levels: `low`, `medium`, `high`, `xhigh`.
- [x] Approve architecture, data flow, error handling, and verification.
- [x] Commit the approved design as `119a0b4cd`.
- **Status:** complete

### Phase 1: Baseline and test contract

- [x] Read the test-driven-development skill before implementation.
- [x] Identify the smallest focused frontend test commands available in this worktree.
- [x] Add failing tests for the four effort values and effort-only request payload.
- [x] Add failing tests for single-model normalization and removed multi-model UI entry points.
- [x] Record baseline and expected failures in `progress.md`.
- **Status:** complete

### Phase 2: Canonical state and request mapping

- [x] Replace `ReasoningDepth` product state with four-value `ReasoningEffort` state.
- [x] Remove frontend hard-coded reasoning token caps.
- [x] Add centralized reasoning capability/allowed-effort resolution.
- [x] Enforce one selected model at product request boundaries.
- [x] Preserve length-one array adapters only where legacy APIs still require them.
- [x] Omit reasoning for unsupported models at the request boundary.
- [x] Make the complete focused mapping/request tests pass.
- **Status:** complete

### Phase 3: Composer components

- [x] Add the accessible four-stop `ReasoningEffortSlider` component.
- [x] Add the compact `ComposerModelSettings` pill and unified menu.
- [x] Reuse existing model catalog search/filter/pinning behavior through a single-select submenu.
- [x] Implement keyboard, Escape, outside-click, focus return, dark mode, and reduced motion.
- [x] Add focused component/presentation tests.
- **Status:** complete

### Phase 4: Chat integration and multi-model removal

- [x] Integrate the composer control into `MessageInput` and placeholder surfaces.
- [x] Synchronize the navbar selector with canonical scalar model state.
- [x] Remove add/remove-model and multi-select creation controls.
- [x] Prevent new product requests with more than one model.
- [x] Keep historical multi-response rendering readable.
- [x] Update translations and draft normalization.
- **Status:** complete

### Phase 5: Verification and acceptance

- [x] Run focused frontend tests and filtered Svelte diagnostics for touched files.
- [x] Run focused `bifrostapi` tests for all four efforts and empty reasoning omission.
- [x] Run desktop/mobile browser checks for layout, synchronization, keyboard behavior, and single-model constraints.
- [x] Run a real isolated Bifrost request and capture `reasoning.effort=xhigh` at the mock upstream.
- [x] Verify formal live remained untouched.
- [x] Update handoff, findings, and progress with exact evidence.
- **Status:** complete

### Phase 6: Documentation and commit

- [x] Update task-local implementation and verification notes.
- [x] Inspect the exact dirty scope and preserve unrelated backend/handoff files.
- [x] Commit tested implementation in an intentional scoped commit (`0c736a9e4`).
- [x] Produce final handoff with the remaining live-deployment boundary.
- **Status:** complete

## Key Questions

1. Which existing model metadata can safely advertise configurable reasoning without hardcoding every provider?
2. Which legacy array boundaries can remain length-one adapters without leaving a multi-model creation path?
3. Which focused Svelte/Vitest commands provide meaningful signal on this branch's noisy baseline?
4. Is an isolated Bifrost/OpenWebUI runtime available for real request acceptance without touching formal live?

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Composer is primary; navbar remains synchronized | Matches the requested workflow while preserving the familiar top entry. |
| Remove new multi-model comparison | User explicitly does not want the capability. |
| Use scalar product state with narrow length-one adapters | Removes product ambiguity without forcing a risky repository-wide API rewrite. |
| Use `low/medium/high/xhigh` | These are the current Bifrost-native valid effort levels. |
| Send effort without frontend token caps | Effort is the user semantic; token policy belongs in Bifrost/provider configuration. |
| Omit reasoning for unsupported models | Prevents empty/invalid reasoning retries and false UI claims. |
| Keep historical multi-response rendering | Product removal must not make existing chats unreadable. |
| Do not touch formal live | No deployment authorization was given. |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| Direct ChatGPT bundle fetch failed with `SSL_ERROR_SYSCALL` | 1 | Retried through Clash at `192.168.2.201:7897`; HTTP 200. |
| Backtick in an exec template regex caused a JavaScript parse error | 1 | Replaced it with a narrower regex without backticks. |
| New `docs/plans/*` file was ignored | 1 | Force-added only the approved design path after scope inspection. |
| `git diff --check` found a blank line at EOF | 1 | Removed the final blank line and reran the check before commit. |
| Editable `uv run` triggered a frontend build and exposed an unclosed `ModelSelector` div | 1 | Added a compile regression test and closed the exact missing wrapper. |
| Editable frontend build exhausted the default Node heap | 1 | Used the existing project venv for focused Python tests; production build passed with `NODE_OPTIONS=--max-old-space-size=8192`. |
| First isolated SSE fixture emitted literal escaped newlines | 1 | Replaced the fixture separator with real newline characters and reran the end-to-end request successfully. |

## Notes

- Re-read this plan before each major implementation decision.
- Update phase status and the handoff after every completed checkpoint.
- Log every failure and do not repeat a failed command unchanged.
- Preserve the unrelated untracked `handoff/chat-agent-dual-mode-20260726/` directory.
