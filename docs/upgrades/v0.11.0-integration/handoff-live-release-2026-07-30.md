# OpenWebUI v0.11 production release handoff — 2026-07-30

## Goal

Perform a fresh, comprehensive release audit of the customized v0.11 integration. If every production gate passes, build a traceable immutable image, replace only the formal `open-webui` service on `aiserver`, and verify the real live runtime with an immediate rollback path.

## Truth surfaces

- Source: `/Users/liusihang/openwebui/.worktrees/v011-upstream-integration-base`, branch `codex/v011-upstream-integration-base`.
- Isolated acceptance: `aiserver:/home/aiserver/staging/openwebui-pr7-eea11194ed-test`, container `open-webui-pr7`, host port `18085`.
- Formal live: `aiserver:/srv/openwebui-migration`, container/service `open-webui`, host port `80`.
- Formal-live changes are authorized only after all source and isolated-runtime gates pass.

## Release requirements

1. Confirm the exact committed source, dirty-file boundary, integration exclusions, and all independent-review findings against the source worktree.
2. Pass focused regression suites, full backend and frontend suites, lint/format gates, and a production frontend/image build.
3. Pass authenticated browser E2E on the isolated stack for home load, new chat, tool call, persisted-chat reopen, and the previously fixed spinner/plain-text-error regressions.
4. Build and inspect an immutable production candidate tied to the exact source SHA.
5. Before live mutation, capture compose/config, container/image, health, restart/OOM, database revision, and database backup rollback anchors.
6. Recreate only formal `open-webui`; abort or roll back on wrong image, migration failure, unhealthy state, restart loop, authentication failure, or core-chat regression.
7. Verify live cold start, health/database health, version SHA, multiple workers, authenticated chat/tool/history behavior, browser console, and post-cutover logs.

## Completed actions

- Declared the source, isolated test stack, and formal live truth surfaces.
- Read the image rebuild, live upgrade, truth-surface, and verification skill instructions.
- Confirmed the source branch currently points at `36de71c612d3ac3dbe021d8ee49fea5e78c119e0`; only pre-existing untracked `.playwright-cli/`, `handoff/2026-07-29-openwebui-v011-lazy-audit/`, and `output/` are present.
- Collected three prior independent review reports. Their findings are not yet accepted as current because two reports referenced the root checkout instead of the designated v0.11 worktree; each finding must be rechecked here.
- Fresh remote inventory at `2026-07-30T01:17:49+08:00` confirmed the isolated v0.11 container and formal live are both healthy, use four Uvicorn workers, have restart count 0, and have not OOM-killed. The isolated container runs image `sha256:2eaa381ce64f...` at source `b7cb48cb9ab1383791b66cc94bd40050b0af6e30`; formal live remains on the prior `1d8dba8a7` image and was not mutated.
- Rechecked the independent-review findings on the exact worktree:
  - AgentRun detail/list/SSE owner-or-admin authorization and missing-run 404 behavior are implemented and covered by the 70-case route suite.
  - Terminal HTTP proxy rejects disabled connections and WebSocket authentication uses `get_verified_user_by_token`, which includes token-revocation validation.
  - The empty terminal-proxy subpath behavior is identical in pre-v0.11 `origin/main`, so it is a pre-existing compatibility debt, not a v0.11 regression.
  - `build_fork_history()` explicitly rejects missing messages and cycles; its route maps these to 404/400.
  - Removal of official Sub-agents and chat-files tools is the user-approved integration contract, not a compatibility defect.
- Focused high-risk backend matrix: `140 passed`.
- Frontend Vitest matrix on Node `22.22.0`: `37` files and `400 passed`.
- Initial full backend matrix exposed four unit tests that unintentionally reached the workspace's legacy SQLite `config` table after the v0.11 per-key Config reads. The tests were isolated at the exact Config/permission boundary; the four RED failures became `4 passed`.
- Fresh full backend rerun: `1286 passed`, with only pre-existing deprecation/test-key warnings.
- Full fatal Ruff set (`E9,F63,F7,F82`) passed; changed-test Ruff passed after excluding their pre-existing import-order and complexity findings; Ruff format check passed.
- Full `svelte-check` still reports the exact known baseline of `8195 errors and 216 warnings in 352 files`. This is not represented as a pass; no frontend source changed in this task. Production build and real browser E2E remain mandatory release gates.
- The first production frontend build reached chunk rendering and then exhausted Node's default approximately 4 GB heap. The identical build on Node `22.22.0` with an 8 GB heap completed successfully in `1m 12s`; both generated version files contain exact source `4c426d8f6da4ad811bc61de6ff78e7b6270e1e5f`.
- Added a RED/GREEN Dockerfile contract requiring the frontend build stage to set `NODE_OPTIONS=--max-old-space-size=8192`, so the full remote image build uses the proven resource boundary rather than an operational one-off.
- Fresh authenticated isolated-stack browser acceptance passed:
  - Home rendered with the composer and zero active spinners.
  - New `openai/gpt-5.5` chat called `get_current_timestamp`, rendered its expandable input/output detail, and returned exact marker `V011_RELEASE_TOOL_OK`.
  - Returning home and directly reopening the saved chat rendered the message/tool history with zero spinners and no `Unexpected token`, invalid-JSON, or Internal Server Error text.
  - Agent mode returned exact marker `V011_RELEASE_AGENT_OK`; direct history reopen preserved Agent mode and rendered with zero spinners.
  - Browser console ended with 0 errors and 0 warnings; all dynamic API requests shown by Playwright were HTTP 200.
  - The two temporary chats, local auth state, and remote auth helpers were deleted and their absence verified. Screenshots remain under `output/playwright/v011-release-20260730/.playwright-cli/`.

## Checkpoints

| Checkpoint                                | Status    | Evidence / next verification                                                                                                      |
| ----------------------------------------- | --------- | --------------------------------------------------------------------------------------------------------------------------------- |
| C0 source and runtime inventory           | completed | Exact source, test container, formal-live container, health, workers, image IDs, compose roots, and checksums recorded.           |
| C1 code-review findings resolved          | completed | Current source/tests prove security findings fixed; exclusions intentional; terminal empty-subpath issue classified as baseline.  |
| C2 source test and build gates            | completed | Backend 1286/1286, frontend 400/400, fatal lint, formatting, and production build pass; global svelte-check baseline is recorded. |
| C3 isolated browser/runtime acceptance    | completed | Chat/tool/detail/history and Agent/history E2E passed with zero spinners or console errors; fixtures cleaned.                     |
| C4 immutable image and rollback readiness | pending   | Build/inspect candidate; create live backups before any switch.                                                                   |
| C5 formal live cutover and acceptance     | pending   | Recreate only `open-webui`, then prove cold-start and core flows.                                                                 |

## Current state

Release decision is **not yet made**. No formal-live mutation has occurred in this task.

## Next step

Commit the reproducible Docker build heap contract, then create and inspect the immutable full slim image from a clean archive.
