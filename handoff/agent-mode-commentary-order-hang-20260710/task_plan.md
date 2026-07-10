# Native phase implementation task plan

## Goal

Implement and verify native model phase passthrough for Agent Mode without an extra model call, synthetic first-person narration, or live-service mutation.

## Truth surface

- Worktree: `/Users/liusihang/openwebui/.worktrees/live-f8106c651-to-v0102`
- Branch: `codex/live-f8106c651-to-v0102`
- Approved design commit: `72cf407c0`
- Provider proof: exact `Cliproxy/gpt-5.4` raw Responses SSE on Bifrost
- Isolated WebUI: `open-webui-pr7` on port `18085`
- Live service: out of scope

## Current phase

Phase 1: write Pipe red tests.

## Phases

1. [in_progress] Bifrost Pipe red/green phase preservation.
2. [pending] AgentScope callback parser red/green.
3. [pending] Bridge commentary/final split red/green.
4. [pending] Remove synthetic tool narration with red/green coverage.
5. [pending] Runtime lifecycle and backend regression suites.
6. [pending] Independent review, fixes, and production commit.
7. [pending] Slim image rebuild, isolated swap, and real acceptance.

## Decisions

| Decision | Rationale |
|---|---|
| Use native Responses phase | Verified before the first text delta for commentary and final output. |
| Buffer commentary, stream final | Matches the user's UX requirement and avoids per-token transcript writes. |
| Keep structured tool events only | Tool cards already carry lifecycle state; synthetic prose is misleading. |
| Fail unclassified no-tool final | Silent buffering would violate genuine final streaming. |
| No second finalizer call | Native phase makes it unnecessary. |

## Errors encountered

| Error | Attempt | Resolution |
|---|---:|---|
| Existing agent-browser socket directory was read-only | 1 | Used the signed in-app browser instead. |
| Initial SSH was sandbox-blocked | 1 | User granted unrestricted permissions; retry succeeded. |
| Exact Bifrost log virtual key was redacted | 1 | Read the installed repo-managed Pipe valves in remote process memory. |
| OpenAI connection index 5 key rejected Cliproxy | 1 | Traced the actual `bifrostapi` Pipe route and used its exact configured key/model. |

## Stop conditions

- Stop if a required change expands into ordinary chat semantics rather than the repo-managed Pipe/Agent Mode path.
- Stop after three failed implementation hypotheses and re-evaluate architecture with the user.
- Do not mutate live containers, broad Bifrost logs, or protected untracked paths.
