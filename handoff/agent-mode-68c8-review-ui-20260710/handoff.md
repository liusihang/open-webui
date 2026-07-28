# Handoff: Agent Mode 68c8 Review and UI Productization

## Objective

Review and harden the Agent Mode stack ending at `68c8a49e9`, improve the Agent UI to a mature web-agent experience, verify it, and create a local commit.

## Truth Surface

- Exact worktree: `/Users/liusihang/openwebui/.worktrees/live-f8106c651-to-v0102`
- Branch/design baseline: `codex/live-f8106c651-to-v0102` / `23405b4ad`
- Root checkout is out of scope and dirty.
- Pre-existing target-worktree untracked paths must be preserved: `.playwright-cli/`, `handoff/agentmode-v0102-migration-20260708/`.

## Current Checkpoint

CP0 through CP4 are complete. The replay and UI defects were reproduced with red tests, fixed, and verified. Final gates: all 163 backend Agent tests passed, all 77 AgentScope runtime tests passed, 81 AgentEvents/API tests passed, nine touched Svelte components compiled, scoped Ruff checks passed, Prettier and `git diff --check` passed, and the Node 22 production build completed with 6363 modules. Only the final local commit remains.

## Provisional Review Boundary

- Agent Mode migration/UI/replay commits: `5c9de0ebf` through `68c8a49e9`.
- Review `940bbb7a5` separately as a mixed static/OnlyOffice commit.

## Implemented Fixes

- Normalize raw Responses `function_call` / `function_call_output` replay items into canonical assistant/tool messages before provider routing.
- Sanitize JSON-encoded replay strings and case/casing variants of private reasoning keys.
- Drop orphaned tool calls and outputs after replay trimming.
- Auto-open a transcript only for a new attention state, while preserving a user's collapse choice for the same state.
- Add theme-native semantic surfaces, mature iconography, successful-tool disclosure, human-readable tool names, inline action errors, keyboard focus, and reduced-motion behavior.
- Preserve mixed JSON Schema input fields beside choice questions, keep Continue disabled until required supplemental fields are complete, and render submitted answers without internal `_source` / `_label` metadata.
- Prefer user-facing runtime tool summaries over grammatical fallbacks such as `Ran Run command`.

## Browser Acceptance

- Isolated surface: `http://127.0.0.1:18080` with SQLite data under `/tmp/openwebui-agentmode-68c8-data` only; no live service or root checkout data was touched.
- Verified completed, waiting-approval, waiting-input, tool-detail, inline-error, submitted-answer, light/dark, reduced-motion, keyboard focus, and 390px mobile behavior.
- Confirmed no horizontal overflow, no private reasoning-field leakage, no duplicate toast on recoverable action errors, and correct merged mixed-schema submission content.
- Evidence screenshots are under `/tmp/openwebui-agentmode-qa/`, including `desktop-light-approval.png`, `mobile-dark-input.png`, `desktop-dark-inline-error.png`, and `final-desktop-dark-expanded-clean.png`.

## Remaining Action

1. Create the final local implementation commit including this handoff.

## Stop/Rollback Conditions

- Never overwrite or clean the protected untracked paths.
- Do not touch live services without explicit authorization.
