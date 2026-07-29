# Pre-v0.11 answer typography and sidebar cleanup handoff

## Goal

- Restore the model-answer typography and paragraph rhythm from commit `665221e1910a11cfd20e034d9967c93f5d4025d2`.
- Preserve the explicitly requested 58rem alignment between answer content and the composer.
- Remove the OpenClaw quick entry from the sidebar without deleting channel/backend functionality.

## Truth surfaces

- Code: `/Users/liusihang/openwebui/.worktrees/v011-upstream-integration-base`
- Branch: `codex/v011-upstream-integration-base`
- Starting HEAD: `f2fd0480fd2fb8bb0108f856f0cdaba1f47ec201`
- Pre-v0.11 presentation reference: `665221e1910a11cfd20e034d9967c93f5d4025d2`
- Test runtime: `http://192.168.2.238:18085/`
- Rollback image: `open-webui:v011-hotfix-4934cdf59bbf`
- Formal runtime: read-only.

## Scope boundaries

- Restore only `.markdown-prose` typography/rhythm from the pre-v0.11 reference.
- Retain `max-w-none break-words` so the inner prose does not reintroduce a 65ch limit inside the 58rem answer/composer column.
- Retain the restored ordinary assistant `.markdown-prose` wrapper and citation behavior.
- Remove only the sidebar OpenClaw import/state/handler/markup and its sidebar-only resolver module.
- Preserve OpenClaw channel types, APIs, stored data, and non-sidebar routes.
- Preserve unrelated `.playwright-cli/`, `output/`, and concurrent handoff artifacts.

## Current status

- Code committed at `768461828`.
- TDD RED: 4 expected failures across typography and OpenClaw removal contracts.
- TDD GREEN: 38/38 focused tests passed.
- Node 22 production build passed at source commit `768461828`; 6,407 modules transformed and `build/_app/version.json` identifies that exact commit.
- Compiled CSS contains the 58rem outer column plus pre-v0.11 prose overrides: unrestricted inner prose width, 4px heading rhythm, zero paragraph/list/pre/table rhythm, normal 2px blockquotes, and pre-line whitespace.
- First incremental hotpatch build was safely rejected before container replacement because one CSS assertion assumed adjacent minified declarations. The assertion now checks declarations independently; the test runtime remains on rollback image `open-webui:v011-hotfix-4934cdf59bbf`.
- Corrected 112 MiB hotpatch context packaged with SHA-256 `c340520fe80d8363b82d2dc5e18f6b31fc861d41d5e89e479b7309650b35a570`; every Dockerfile assertion was reproduced successfully against the local build before packaging.
- Independent remote diagnostics proved all assertions pass on the new build under GNU grep 3.11 both on the host and in the base-image environment. The actual failure was stale hashed chunks retained by Docker directory-overlay `COPY`; revision r3 removes only `/app/build` inside the new image layer before copying the complete build.
- Clean-replacement r3 context SHA-256: `445d4d278e8edfa29377e8c185edd8b07482929bcc2794c0f1628a63a093c04d`.
- Clean-replacement image built successfully as `sha256:e6749eb2fc8a4222a1a8965318abcc322a055e6f7a75f303e5e41bddb73505bb`; image labels and asset assertions passed.
- Initial cold-start attempt automatically rolled back because the deployment script treated initialization-period `unhealthy` as terminal. Failed-container logs had zero fatal matches and showed normal four-worker initialization. Rollback image and formal image both returned healthy with zero restarts/OOM.
- Retry reuses only the exact verified image ID and allows `starting/unhealthy` to recover inside a 420-second deadline; `exited/dead` and deadline expiry still trigger automatic rollback.
- Retry succeeded at 2026-07-29 18:36:42 +08:00. Isolated test image is `sha256:e6749eb2fc8a4222a1a8965318abcc322a055e6f7a75f303e5e41bddb73505bb`, source label `7684618281df7a9adbd4217d127c3abb284cc261`, healthy, restart count 0, OOM false.
- Database revision remains `a11c0d3f0bd0`. Formal image remains `sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b`, healthy, restart count 0.
- Rollback script: `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/backups/pre-hotpatch-7684618281df-r4/rollback-open-webui-pr7.sh`.
- Deployment evidence: `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/evidence/v011-pre-v011-answer-typography-sidebar-7684618281df-r4-20260729-183400`.
- Final remote audit passed: functional API, persisted chat detail (60 chats, selected chat with 2 messages), health/DB health, v0.11.0 version, frontend source label, runtime CSS, no stale sidebar asset, and zero recent fatal/5xx signals.
- Authenticated Playwright E2E at 1280x900 passed. Existing-chat navigation from `/` loaded `/c/9b26887e-fc1d-40dd-8cf8-6e730307f31a` with spinner count 0 and console errors/warnings 0.
- Browser computed styles: prose 16px/28px, `white-space: pre-line`, `max-width: none`; paragraph margins 0 and adjacent gap 0; H1 36px/40px/600 with 4px margins; H2 24px/31.9999px/600 with 4px margins; list margins 0; blockquote margins 0, 2px border, normal 400 text.
- Browser geometry: assistant `.message-listitem` and composer `max-w-[58rem]` wrappers both measured exactly 928px, including with expanded sidebar.
- Expanded sidebar text and DOM contained neither `OpenClaw` nor `🦞`; sidebar OpenClaw button count was 0.
- Browser screenshot: `output/playwright/v011-pre-v011-answer-typography-sidebar-20260729/browser-e2e/pre-v011-typography-sidebar-expanded.png`.
- Temporary browser user/chat and remote/local password files were deleted after E2E; screenshot and non-secret deployment evidence were retained.
- Final verification at 18:54 passed: 38/38 focused Vitest assertions, all deployment shell scripts parsed with `bash -n`, and `git diff --check` was clean.
- Deployment documentation was committed separately from the production code commit.
- Final live readback at 2026-07-29 18:54:53 +08:00 confirmed the test container still uses image `sha256:e6749eb2fc8a4222a1a8965318abcc322a055e6f7a75f303e5e41bddb73505bb`, frontend source `7684618281df7a9adbd4217d127c3abb284cc261`, healthy, restart count 0, OOM false, with health and DB health true.
- The same readback confirmed the formal container is unchanged at image `sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b`, healthy, restart count 0, OOM false.
- Current status: complete.

## Verification required

- Completed: focused presentation/unit tests, including observed RED before production edits.
- Completed: Node 22 production frontend build.
- Completed: isolated test image/container health and rollback evidence.
- Completed: browser computed style for pre-v0.11 paragraph/heading/list rhythm.
- Completed: browser sidebar snapshot with no OpenClaw button or label.
- Completed: existing conversation loads with spinner count zero.
