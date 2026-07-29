# OpenWebUI v0.11 tool-call dropdown regression handoff

## Objective

Find and fix why clicking the tool-call analysis summary on the authenticated test stack does not reveal tool-call details, while keeping the custom Agent transcript semantics authoritative.

## Truth surfaces

- Browser/UI: `http://192.168.2.238:18085/c/67fc5e94-a9db-4828-9dd8-d5b424fe5362` in the authenticated PR7 Test User flow.
- Live test service: `aiserver` container `open-webui-pr7`, currently publishing port `18085`.
- Source: `/Users/liusihang/openwebui/.worktrees/v011-upstream-integration-base`, branch `codex/v011-upstream-integration-base`.
- Acceptance: reproduce before the fix; add a focused regression test; deploy only to the test stack; verify the same click reveals persisted tool-call details after refresh, with no relevant console/runtime error.

## Completed actions

- Confirmed live test image `open-webui:v011-hotfix-0bb586c8699d`, image ID `sha256:bdbd84db321857ee8a8cd29326dd000b4c51d77256c2805fe2e817b987ffa63a`, container `74eff7a80690...`, healthy.
- Reproduced on the user's exact chat URL with a fresh authenticated browser context.
- Before click: the first tool summary had `aria-expanded=false`; after click it remained false and no `.tool-call-body` appeared.
- Browser error capture reported `Uncaught ReferenceError: Markdown is not defined` from the deployed frontend chunk.
- Traced the defect to `ToolCallDisplay.svelte`: the custom component still uses `<Markdown>`, while the v0.11 integration baseline dropped its import. The failure is lazy because grouped detail children are instantiated only after the outer summary is expanded.
- Added a focused guardrail first; RED was exactly 1 failure among 33 tests because the import was absent.
- Applied the minimal source fix by restoring the existing custom `Markdown.svelte` import.
- Focused GREEN: `v011Integration.presentation.test.ts` passes 33/33 under Node 22.
- Full frontend GREEN: 35 test files and 395/395 tests passed under Node 22.
- Production build succeeded with 6408 modules transformed and the static adapter completed. Existing repository-wide Svelte warnings remain unchanged; the build exited successfully.
- Committed the root fix and regression guardrail as `9643bd7ad189ddc1e65fd6996d8b5c047e6e06e8` (`fix(frontend): restore tool call detail expansion`).
- Rebuilt the static frontend with the exact commit as `APP_BUILD_HASH`, packaged a 112 MiB thin-build context with SHA-256 `e4857ecdba9dad7b40424f6510878345ff6730eeb7156fdc06eb96a082a51127`, and layered it on the prior accepted image without rebuilding backend dependencies.
- Deployed only `open-webui-pr7` as `open-webui:v011-hotfix-9643bd7ad189`; image ID `sha256:c5fecd259933068ac435e37c4698ed84a027e67fab58817b41b813052dd6aaca`.
- The replacement container `2390111a9f70...` reached healthy with restart count 0 and `OOMKilled=false`; DB revision remains `a11c0d3f0bd0`.
- Formal `open-webui` remained on image `sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b`, healthy with restart count 0; non-target compose containers were unchanged.
- Post-deploy exact-chat E2E passed: frontend version is the exact source commit; outer summary changed `false -> true`; four visible tool cards rendered; all four opened with non-empty Output; `error`, `unhandledrejection`, and post-clear console arrays were empty.
- Temporary browser authentication files were removed locally and remotely, and the browser session was closed.

## Checkpoint

- Root cause and minimal source fix are complete.
- Complete. The test stack is fixed and accepted; formal-live promotion remains separately authorized and was not performed.

## Verification results

- Pre-fix screenshot: `output/playwright/v011-tool-dropdown-before-fix.png`.
- Pre-fix DOM/error evidence: `before=false`, `after=false`, `toolCallBodies=0`, `ReferenceError: Markdown is not defined`.
- Focused test RED: 1 failed, 32 passed.
- Focused test GREEN: 33 passed.
- Full frontend suite: 395 passed.
- Production build: passed, 6408 modules transformed.
- Browser version: `9643bd7ad189ddc1e65fd6996d8b5c047e6e06e8`.
- Exact click E2E: `summaryBefore=false`, `summaryAfter=true`, one tool body with Input/Output, zero captured errors/rejections.
- Multi-tool E2E: 4/4 visible cards (`get_current_timestamp`, `create_tasks`, `search_calendar_events`, `list_automations`) opened with Output, zero captured errors/rejections, empty cleared console.
- Visual proof: `output/playwright/v011-tool-call-dropdown-20260729/expanded-details-viewport-success-9643bd7ad189.png`.
- Runtime final audit: healthy, restart count 0, DB head `a11c0d3f0bd0`, no fatal/HTTP 5xx since deploy.
- Runtime evidence: `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/evidence/v011-tool-call-dropdown-9643bd7ad189-20260729-142000`.
- Rollback: `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/backups/pre-hotpatch-9643bd7ad189/rollback-open-webui-pr7.sh`.

## Current state

Complete on the isolated test stack. Formal live was not changed.

## Next steps

No remaining action for this defect. Continue the broader functional acceptance matrix or request formal-live promotion separately.
