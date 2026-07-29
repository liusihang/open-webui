# Test-stack home spinner diagnosis handoff

## Goal

Determine why `http://192.168.2.238:18085/` remains on the initial loading spinner after the v0.11 test-stack cutover, implement the confirmed fixes, and deploy them only to the isolated test stack. The user authorized implementation and asked to prefer a thin hot-patch image over a full image rebuild.

## Fix execution

- Authorization: received 2026-07-29 after root-cause diagnosis.
- Truth surface: source worktree, resulting hot-patch image on `aiserver`, isolated `open-webui-pr7` container, and a real authenticated browser against port 18085.
- Current checkpoint: accepted in the user's real Microsoft Edge profile after the expanded-sidebar import fix and a forced reload onto the new immutable assets.
- Implemented image path: built frontend assets once, layered only `/app/build` onto the locked v0.11 test base image, and recreated only `open-webui-pr7`.
- Stop/rollback condition: wrong source/image/compose target, failed focused tests or frontend build, unhealthy replacement container, wrong image at runtime, or failed empty-IndexedDB browser regression.

### Reopened after real Edge retest

- User evidence at 2026-07-29 08:54: the existing authenticated Edge tab at `http://192.168.2.238:18085/` renders navigation/sidebar and `PR7 Test User`, while the main content remains a central spinner.
- This disproves the second completion claim. The injected pending-IndexedDB E2E proved only that one known startup blocker was removed; it did not identify the still-pending boundary in the user's actual browser profile.
- Truth surface is now the user's existing Edge tab plus matching `open-webui-pr7` request/log evidence. New Playwright contexts are comparison controls only and cannot establish completion.
- Real Edge console evidence: `Uncaught ReferenceError: Dropdown is not defined` at `Sidebar.svelte:1523:8`; DevTools readback also confirmed persisted `localStorage.sidebar === 'true'`.
- Deterministic control reproduction: a newly authenticated Playwright session with `sidebar=true` produced the identical spinner and `Dropdown is not defined`, while every initialization API returned HTTP 200. Screenshot: `output/playwright/v011-home-spinner-20260729/expanded-sidebar-before-third-hotfix.png`.
- Root cause: merge commit `1f93cd9a3` resolved the Sidebar import conflict as either official chat-menu imports or the custom OpenClaw import. It kept OpenClaw but dropped official `Dropdown`, `DropdownMenu`, `CheckIcon`, and `MoreHorizontalIcon` imports even though the official action-slot markup remained.
- Previous E2E gap: both accepted Playwright snapshots had a collapsed sidebar, so the action slot containing the undefined components was never instantiated.
- TDD red: the focused integration suite ran 28 tests with exactly one expected failure listing all four missing official imports. Minimal fix restored those imports alongside the custom OpenClaw import; the same suite then passed 28/28.
- Source fix commit: `98ae1cd6071d5434f080c98e8195884b391af124` (`fix(frontend): restore expanded sidebar imports`).
- Production build: Vite completed successfully in 57.08 seconds with an 8 GB V8 heap. `build/_app/version.json` equals the full source commit and its source map contains the restored Sidebar imports.
- Third hot-patch artifact: `output/playwright/v011-home-spinner-20260729/openwebui-v011-hotfix-98ae1cd6071d-context.tar.gz`, SHA-256 `7e825836deb1d05cbbdfaf932a587e05da4db3cf68bab2bde9a66d73407562d1`.
- Third thin image: `open-webui:v011-hotfix-98ae1cd6071d`, image ID `sha256:38a2aa1b41fd1107254ca8ff36f0d1059ff4d3d0d79bf6b480a2c610520cbe6f`, size 2,312,420,687 bytes. It is based directly on the original locked v0.11 test image rather than stacking earlier frontend hot-patches.
- Deployment overlay: `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/compose.webui-v011-hotfix-98ae1cd6071d.yaml`; durable copies are `deployment/Dockerfile.hotpatch-98ae1cd6071d` and `deployment/compose.webui-v011-hotfix-98ae1cd6071d.yaml`.
- The durable YAML copy is Prettier-normalized to single quotes while the remote heredoc uses double quotes; the lexical difference is limited to quoting, and image plus both environment values are identical.
- Rollback: `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/backups/pre-hotpatch-98ae1cd6071d/rollback-open-webui-pr7.sh` restores the second hot-patch Compose surface and image.
- Runtime acceptance: `open-webui-pr7` is healthy with restart count 0, frontend version `98ae1cd6071d…`, Redis keepalive and health-check settings intact, all health/version endpoints successful, and authenticated `/api/models` returned 35 models. Formal live remains healthy on unchanged image ID `sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b`.
- Expanded-sidebar E2E: a fresh authenticated context with `localStorage.sidebar='true'` rendered the complete chat list and home composer. All initialization APIs returned HTTP 200 and the console had 0 errors and 0 warnings. Screenshot: `output/playwright/v011-home-spinner-20260729/expanded-sidebar-after-third-hotfix.png`.
- Cached-page finding: the retained pre-fix Playwright page continued executing old chunk `nodes/2.BSccNmZS.js` after a normal `goto`, so it correctly remained broken until a cache-bypassing reload. This was old frontend execution, not the new image.
- Real Edge acceptance: the user's existing profile was force-reloaded without clearing login or site data. The exact tab then rendered the expanded sidebar, chat action menu, conversation mode controls, selected model, composer, and suggestions. DevTools read back frontend version `98ae1cd6071d5434f080c98e8195884b391af124` and new main chunk `nodes/2.BDS2JQ8V.js`.
- Real Edge interaction: the sidebar “More” dropdown opened and exposed `Mark all as read`, directly exercising all four restored components. `Dropdown is not defined` is absent; the remaining console errors originate from the Zotero extension's `chrome-extension://` scripts, not OpenWebUI.
- Final container log audit: 0 runtime error signatures, health `healthy`, restart count 0, and `OOMKilled=false`.
- Current checkpoint: complete on the exact user-reported Edge truth surface. DevTools was closed and the tab was left on the fully rendered home page.

### Reopened after user retest

- User truth surface: after the first hot-patch, the user's existing browser still remained on the post-login spinner at port 18085.
- Previous acceptance gap: the browser regression covered a valid empty database whose `open()` resolved; it did not cover a permanently pending `indexedDB.open()`/legacy-database request.
- Source gap: `checkLocalDBChats()` is still an awaited member of the app-layout startup `Promise.all`, so any browser-local IndexedDB request that never resolves still keeps `loaded=false` forever.
- Current checkpoint: complete. The stronger condition was reproduced, guarded by a failing test, fixed by moving the optional legacy check out of the blocking startup gate, and accepted in the same injected browser context.
- Current service mutation in this reopened phase: only `open-webui-pr7` was recreated onto `open-webui:v011-hotfix-fd8fe181823d`; formal live and dependent services were not restarted.

### Fix checkpoint 4: permanently blocked IndexedDB request

- Reproduced against the first hot-patch image with a real `IDBOpenDBRequest` held permanently in the `blocked` state. The browser showed the same sidebar-plus-central-spinner UI.
- During that reproduction, auth/config/banner/tools/settings/models requests all returned 200 and the browser console had no error; the only diagnostic entry confirmed that `Chats` had been redirected to the blocked upgrade request.
- Screenshot before the second fix: `output/playwright/v011-home-spinner-20260729/blocked-open-before-second-hotfix.png`.
- Root cause: the optional legacy migration call `checkLocalDBChats()` remained inside the awaited app-startup `Promise.all`, so a browser-level IndexedDB request with no success/error event kept `loaded=false` forever.
- TDD red: the focused integration suite ran 27 tests with exactly one expected failure proving the legacy call was still in the blocking startup gate.
- Minimal fix: start `checkLocalDBChats()` with `void` before the blocking network initialization set; the migration UI may still appear when the request eventually resolves, but it can no longer prevent the app from rendering.
- TDD green: the same suite passed 27/27.
- Source fix: commit `fd8fe181823dd0e71071a025cac74ef95e05489a` (`fix(frontend): decouple legacy chat database startup`).
- Build checkpoint: the first production-build attempt reached chunk rendering but hit Node's default heap limit and exited 134. No image was packaged from that failed output; after host-memory verification, the successful retry used an explicit 8 GB V8 heap.
- Build completion: `NODE_OPTIONS=--max-old-space-size=8192 APP_BUILD_HASH=fd8fe181823d ./node_modules/.bin/vite build` completed successfully in 57.83 seconds. `build/_app/version.json` contains the full source commit and the emitted source map contains the non-blocking `void checkLocalDBChats()` call.
- Second hot-patch artifact: `output/playwright/v011-home-spinner-20260729/openwebui-v011-hotfix-fd8fe181823d-context.tar.gz`, SHA-256 `4aa2a886a982178df46a5f937dde42230a0bbf923508c045e1a2062a68a933d9`.
- Second thin image: `open-webui:v011-hotfix-fd8fe181823d`, image ID `sha256:c05f9e01b67bb33f409a53eaf53ac2073fcc189f9e23607ccc870a8444252874`, size 2,312,420,436 bytes. It is based directly on `open-webui:v011-test-4d3543438b-slim`, not stacked on the first hot-patch image.
- Deployment overlay: `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/compose.webui-v011-hotfix-fd8fe181823d.yaml`; durable source copies are `deployment/Dockerfile.hotpatch-fd8fe181823d` and `deployment/compose.webui-v011-hotfix-fd8fe181823d.yaml`.
- Rollback: `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/backups/pre-hotpatch-fd8fe181823d/rollback-open-webui-pr7.sh` restores the pre-second-hot-patch Compose surface.
- Runtime acceptance: the test container is healthy with restart count 0, frontend version `fd8fe181823d…`, Redis keepalive and 30-second health check enabled, all three health/version endpoints successful, and authenticated `/api/models` returned 35 models.
- Strong E2E acceptance: the retained second browser page still held `__codex_blocked_chatdb__` version 1 open, while an intercepted `indexedDB.open('Chats')` on the main page remained `readyState=pending`. Despite that exact fault, the authenticated home page rendered its navigation, mode controls, selected model, composer, and suggestions.
- Strong E2E network/console evidence: all initialization APIs returned HTTP 200; the console contained only the expected `[codex-diagnostic]` info message, with 0 errors and 0 warnings. Screenshot: `output/playwright/v011-home-spinner-20260729/blocked-open-after-second-hotfix.png`.
- Clean-session E2E also rendered the complete authenticated home page with all initialization APIs at HTTP 200 and 0 console errors/warnings. Screenshot: `output/playwright/v011-home-spinner-20260729/clean-session-after-second-hotfix.png`.
- Post-deployment log audit found 0 runtime error signatures; health remained healthy, restart count 0, and `OOMKilled=false`. Formal live remained healthy on the unchanged image ID `sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b` with restart count 0.

### Fix checkpoint 1: source and TDD

- Source branch/HEAD before fixes: `codex/v011-upstream-integration-base` at `14cc48ddfd27e61f3b2c32b7ccd80338b2fe1138`.
- Test-stack rollback image: `open-webui:v011-test-4d3543438b-slim`, image ID `sha256:4cc390c27e677220516c8c627c1d490001cf89f8d9183dff41548792606dbd5b`.
- Formal-live read-only anchor: container `open-webui`, image ID `sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b`, healthy, restart count 0.
- TDD red: focused v0.11 frontend guardrail suite ran 26 tests with exactly 2 expected failures (missing `handledSettingsUrl` declaration and blocking empty-DB deletion).
- Minimal source fix: restored `let handledSettingsUrl = '';`; the empty local DB path now closes and releases `DB`, then starts `deleteDB('Chats')` without awaiting that optional cleanup.
- TDD green: the same focused suite passed 26/26.
- Current checkpoint: inspect effective Compose intent, build frontend assets, and assemble the thin hot-patch image; no test-stack mutation has occurred yet.

### Fix checkpoint 2: thin image and isolated deployment

- Source fix commit: `af2d1b3480856a81e97ce5ff648c0cc467e2500f` (`fix(frontend): unblock v0.11 app initialization`).
- Frontend production build: `APP_BUILD_HASH=af2d1b348085`; Vite completed successfully and wrote `/build/_app/version.json` with the full source commit. The build emitted existing repository-wide Svelte warnings but no build error.
- Hot-patch artifact: `output/playwright/v011-home-spinner-20260729/openwebui-v011-hotfix-af2d1b348085-context-v2.tar.gz`, SHA-256 `8b0814fa0a16167e1b20d7979f3a7983cdeb6a438c6c9ed5ceda473ef5638d14`, approximately 100 MB compressed.
- Thin image strategy: `FROM open-webui:v011-test-4d3543438b-slim`, remove and replace only `/app/build`; no Python, OS, model, or JavaScript dependency installation was rerun.
- Resulting image: `open-webui:v011-hotfix-af2d1b348085`, ID `sha256:64da9813adb3ff4395fac1d530d9ab8e48a3a7c054e66cd36ef4d7d2a722844d`, size 2,313,334,048 bytes. The original image was 2,056,352,528 bytes, so the hot-patch adds about 257 MB rather than rebuilding its dependency layers.
- Redis client fix is deployment configuration, not another code patch: the final Compose overlay sets `REDIS_HEALTH_CHECK_INTERVAL=30` and `REDIS_SOCKET_KEEPALIVE=true`.
- Compose overlay: `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/compose.webui-v011-hotfix-af2d1b348085.yaml`.
- Durable source copies: `deployment/Dockerfile.hotpatch-af2d1b348085` and `deployment/compose.webui-v011-hotfix-af2d1b348085.yaml`.
- Pre-deploy backup and manifest: `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/backups/pre-hotpatch-af2d1b348085/`.
- Rollback command is packaged as `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/backups/pre-hotpatch-af2d1b348085/rollback-open-webui-pr7.sh` and restores the prior six-file Compose surface plus image `open-webui:v011-test-4d3543438b-slim`.
- Only service `open-webui-pr7` was force-recreated with `--no-deps`. Formal `open-webui`, Redis, PostgreSQL, and AgentScope runtime were not restarted.
- First health probe occurred during cold start and received a connection reset while health was `starting`; logs showed four Uvicorn workers starting, restart count stayed 0, and the later bounded verification reached `healthy`.

### Fix checkpoint 3: runtime and browser acceptance

- Runtime image/config: `open-webui-pr7` is healthy on image ID `64da9813…`, restart count 0; `/app/build/_app/version.json` equals source commit `af2d1b348085…`.
- Runtime Redis readback: `_socket_options()` is `{'socket_keepalive': True, 'health_check_interval': 30}`.
- API acceptance: `/health`, `/health/db`, and `/api/version` returned success; authenticated `/api/models` returned 35 models.
- Redis resilience probe: a disposable Redis with a two-second idle timeout closed client ID 5; a client using the deployed options (interval scaled to one second for the short probe) automatically reconnected as client ID 6 and completed `HLEN` successfully. The disposable container was removed and its absence was verified.
- Browser regression: seeded the exact valid-but-empty version-1 `Chats` IndexedDB used to reproduce the bug, authenticated, and loaded port 18085. The complete home UI rendered; all initialization APIs returned 200; console reported 0 errors and 0 warnings.
- Settings regression: navigating to `/?settings=general` opened the General settings dialog, stripped the query parameter as designed, and left the console at 0 errors and 0 warnings.
- Browser screenshot: `output/playwright/v011-home-spinner-20260729/empty-chats-hotfix-home.png`.
- Post-acceptance container log audit found 0 `Traceback`, `Broken pipe`, `ERROR`, or unhandled-exception signatures; health remained healthy, restart count 0, and `OOMKilled=false`.
- Formal-live after-anchor: container `open-webui` remains healthy on the same image ID `ab6d8f1816a…`, restart count 0.

## Truth surfaces

- Browser: the user's existing authenticated Microsoft Edge profile is authoritative; expanded-sidebar Playwright sessions provide deterministic before/after controls with DOM, console, request, and screenshot evidence.
- Live service: `aiserver` container `open-webui-pr7`, its exact image/health/restart state, request logs, and relevant API responses.
- Source: `/Users/liusihang/openwebui/.worktrees/v011-upstream-integration-base`, accepted hot-patch source commit `98ae1cd6071d5434f080c98e8195884b391af124`.

## Completed actions

- Confirmed before this diagnosis that `/health`, `/health/db`, and `/api/version` respond successfully and report v0.11.0.
- Selected systematic root-cause investigation; no fix has been attempted.
- Confirmed the Playwright CLI prerequisite `npx` is available.
- Reproduced both anonymous and authenticated flows in fresh Playwright contexts. Anonymous flow reaches the login page; an authenticated admin flow renders the full home page and all blocking initialization APIs return HTTP 200.
- Captured a stable authenticated console exception: `ReferenceError: handledSettingsUrl is not defined` from the compiled app-layout chunk.
- Traced the exception to `src/routes/(app)/+layout.svelte`: the integration retains reads/writes of `handledSettingsUrl` but omitted official v0.11's `let handledSettingsUrl = '';` declaration.
- Correlated the user's first post-login request window with the exact container logs. The first `/api/models` call returned 500 because a cached Redis connection had been closed by Redis's configured 1800-second idle timeout; the next connection recovered and subsequent calls returned 200.
- Forced `/api/models` to return 500 in an isolated authenticated browser. The full home page still rendered, proving the transient Redis error is visible but does not keep the app-layout `loaded` gate false.
- Reproduced the exact reported UI (sidebar rendered, main content replaced by a permanent central spinner) while all authenticated initialization APIs returned 200 by holding the awaited `Chats` IndexedDB request open.
- Reproduced the same spinner without synthetic interception: seeded a valid version-1 `Chats` database containing an empty `chats` store and `timestamp` index, then loaded the authenticated home page. `checkLocalDBChats()` opened that database, read zero chats, and waited forever on `deleteDB('Chats')` because its own open `DB` connection was never closed.
- Compared this block with the official v0.11 donor. The `checkLocalDBChats()` implementation is identical there, so the IndexedDB self-deadlock is an upstream v0.11 defect rather than an integration-only omission.
- Traced the empty-database deletion path to upstream commit `d78df8345` (`feat: delete idb after migration`, 2023-12-26). This is a latent upstream defect exposed by the user's persisted browser state, not a behavior first introduced by the v0.11 merge.
- Saved the natural reproduction screenshot at `output/playwright/v011-home-spinner-20260729/empty-chats-natural-spinner.png`.

## Checkpoints

| Checkpoint                                          | Status   | Evidence                                                                                                                                                                           |
| --------------------------------------------------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Reproduce in a fresh browser context                | complete | Fresh authenticated session renders normally; an empty valid `Chats` IndexedDB deterministically reproduces the permanent spinner                                                  |
| Identify the first failing browser/runtime boundary | complete | `checkLocalDBChats()` self-deadlocks while deleting its still-open empty `Chats` database                                                                                          |
| Correlate with exact container/API/log evidence     | complete | All blocking APIs can be 200 during the spinner; forced `/api/models` 500 does not prevent rendering                                                                               |
| State root cause and minimal fix options            | complete | Immediate recovery: clear the site's `Chats` IndexedDB/site data. Product fix: close `DB` before deletion and avoid making legacy local-chat cleanup an unbounded app-loading gate |
| Permanently blocked IndexedDB regression            | complete | A real pending `IDBOpenDBRequest` remains pending while the full authenticated home page renders; console has 0 errors and 0 warnings                                              |
| Second thin hot-patch deployment                    | complete | Test container runs image ID `c05f9e01…`, is healthy with restart count 0, and formal live is unchanged                                                                            |
| Expanded-sidebar regression                         | complete | Before: `Dropdown is not defined` and spinner. After: chat list plus full home render, initialization APIs all 200, console 0 errors/warnings                                      |
| Exact Microsoft Edge profile                        | complete | Edge loaded version `98ae1cd6071d…` and chunk `2.BDS2JQ8V.js`; expanded sidebar and its “More” dropdown work, with no OpenWebUI console error                                      |
| Third thin hot-patch deployment                     | complete | Test image ID `38a2aa1b…` is healthy with restart count 0 and no runtime error signatures; formal live remains unchanged                                                           |

## Current state

- Three independently confirmed frontend failures contributed to post-login initialization: missing `handledSettingsUrl`, blocking legacy IndexedDB migration, and missing expanded-sidebar component imports from the v0.11 merge conflict.
- The first two defects are fixed in `af2d1b348085…` and `fd8fe181823d…`; the exact real-Edge defect is fixed in `98ae1cd6071d…`. All three commits are present in the deployed frontend.
- The test deployment now guards cached Redis sockets with a 30-second health check and TCP keepalive; the reconnect mechanism passed an isolated forced-idle-timeout probe.
- Test service `open-webui-pr7` runs thin hot-patch image `open-webui:v011-hotfix-98ae1cd6071d` and passed the user's real Edge profile plus expanded-sidebar and injected-IndexedDB controls. Formal live remains unchanged.

## Next step

No further action is required for the current test browser: the user's Edge tab was left on the complete home page. Promotion beyond the isolated test stack remains a separate authorization decision.
