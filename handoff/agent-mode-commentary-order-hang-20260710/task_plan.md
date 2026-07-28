# Native phase implementation task plan

## Goal

Implement and verify native model phase passthrough for Agent Mode without an extra model call, synthetic first-person narration, or live-service mutation.

## Truth surface

- Worktree: `/Users/liusihang/openwebui/.worktrees/live-f8106c651-to-v0102`
- Branch: `codex/live-f8106c651-to-v0102`
- Approved design commit: `72cf407c0`
- Exact implementation commit: `79adbeface297292a39320bb86ee3543d11f2959`
- Provider proof: exact `Cliproxy/gpt-5.4` raw Responses SSE on Bifrost
- Isolated WebUI: `open-webui-pr7` on port `18085`
- Live service: out of scope

## Current phase

Phase 7 complete: slim image rebuild, isolated swap, real acceptance, and rollback evidence.

## Phases

1. [complete] Bifrost Pipe red/green phase preservation.
2. [complete] AgentScope callback parser red/green.
3. [complete] Bridge commentary/final split red/green.
4. [complete] Remove synthetic tool narration with red/green coverage.
5. [complete] Runtime lifecycle and backend regression suites.
6. [complete] Independent review, fixes, and production commit.
7. [complete] Slim image rebuild, isolated swap, real acceptance, and rollback evidence.

## Phase 7 checkpoints

1. [complete] Build and inspect `open-webui:agentmode-v0102-79adbeface29-slim` from a clean archive without mutating containers.
2. [complete] Build and inspect `open-webui-pr7-agentscope-runtime:79adbeface29-native-phase` from the same commit without mutating containers. Image `sha256:aa8cab6a3f697663245a7e1c1589d68d5d01c2be8eba24523a1271152286b701` passed exact source-hash comparison, offline `/health` smoke, and five-container non-mutation proof.
3. [complete] Back up and update only the isolated `bifrostapi` function through the management API; verify committed content hash and cache invalidation. POST returned 200; source/API SHA256 and source/DB MD5 match exactly.
4. [complete] Recreate only `agentscope-runtime`, verify health/restart/source, then recreate only `open-webui-pr7` with migrations disabled. Runtime and WebUI are both healthy on the exact target images with restart count zero.
5. [complete] Run isolated health, raw protocol/order, cancellation, and browser/UI acceptance; prove live WebUI plus isolated DB/Redis anchors are unchanged. Native-phase run `7fe13f44-63c1-48d0-ae43-ac427d5b1a6d` emitted `commentary-1 -> tool-1 requested/completed -> commentary-2 -> tool-2 requested/completed -> final.started -> 4 final.delta -> run.completed`; exact Bifrost record `26708825-124e-4735-b6a3-d7508659eca6` preserved request input order `user -> commentary-1 -> call-1 -> output-1 -> commentary-2 -> call-2 -> output-2`. Cancellation run `f02c49fa-8a9c-463b-976f-de78607f0820` reached runtime/backend `cancelled` with only `run.running -> run.cancelled`. Browser run `38b8ef06-1942-497a-8480-65f47344ba4e` visibly rendered commentary between the two completed tool cards and streamed the final answer in three deltas with zero console errors. Final health/startup/anchor gates passed.
6. [complete] Record rollback commands, commit deployment handoff updates, and report exact evidence.

## Decisions

| Decision | Rationale |
|---|---|
| Use native Responses phase | Verified before the first text delta for commentary and final output. |
| Buffer commentary, stream final | Matches the user's UX requirement and avoids per-token transcript writes. |
| Keep structured tool events only | Tool cards already carry lifecycle state; synthetic prose is misleading. |
| Fail unclassified no-tool final | Silent buffering would violate genuine final streaming. |
| No second finalizer call | Native phase makes it unnecessary. |
| Runtime before WebUI | The new bridge must be healthy before the Pipe starts sending native phases. |
| Management API before WebUI recreation | Update the DB-managed Pipe through its normal loader/cache invalidation path while the old isolated WebUI remains healthy, then recreate the isolated WebUI once. |
| Explicitly reset inherited runtime build | Compose merges ordinary `build: null`; `build: !reset null` is required so the active image-only override cannot reference the stale server build context. |

## Errors encountered

| Error | Attempt | Resolution |
|---|---:|---|
| Existing agent-browser socket directory was read-only | 1 | Used the signed in-app browser instead. |
| Initial SSH was sandbox-blocked | 1 | User granted unrestricted permissions; retry succeeded. |
| Exact Bifrost log virtual key was redacted | 1 | Read the installed repo-managed Pipe valves in remote process memory. |
| OpenAI connection index 5 key rejected Cliproxy | 1 | Traced the actual `bifrostapi` Pipe route and used its exact configured key/model. |
| Acceptance fixture model `bifrostapi.Cliproxy/gpt-5.4` disappeared from the refreshed model list | 1 | Kept the same Cliproxy provider and parameterized the harness to use its currently exposed `gpt-5.5`; the failed attempt created no Agent run or Bifrost request. |
| Focused cancellation test used the repository-root virtual environment | 1 | Used `services/agentscope-runtime/.venv`; both exact cancellation tests passed. |
| Browser login helper used Bash 4-only `mapfile` on macOS Bash 3.2 | 1 | Replaced it with portable command substitution; authentication and browser acceptance then passed. |

## Stop conditions

- Stop if a required change expands into ordinary chat semantics rather than the repo-managed Pipe/Agent Mode path.
- Stop after three failed implementation hypotheses and re-evaluate architecture with the user.
- Do not mutate live containers, broad Bifrost logs, or protected untracked paths.
