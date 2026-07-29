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

## Checkpoint

- Root cause and minimal source fix are complete.
- Source verification is complete. Commit, thin-image hotpatch, and post-deploy E2E remain.

## Verification results

- Pre-fix screenshot: `output/playwright/v011-tool-dropdown-before-fix.png`.
- Pre-fix DOM/error evidence: `before=false`, `after=false`, `toolCallBodies=0`, `ReferenceError: Markdown is not defined`.
- Focused test RED: 1 failed, 32 passed.
- Focused test GREEN: 33 passed.
- Full frontend suite: 395 passed.
- Production build: passed, 6408 modules transformed.

## Current state

In progress. No live change has been made yet for this defect.

## Next steps

1. Commit only the component, regression test, and this handoff/task evidence.
2. Build a thin frontend-only image from the exact current test image, preserving a rollback anchor.
3. Deploy only `open-webui-pr7`, verify health/version/logs, and rerun the exact click E2E after refresh.
