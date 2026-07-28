# Progress — first-open product update modal

## 2026-07-28 discovery

- Started a new handoff directory for this feature.
- Read the required brainstorming and file-planning instructions.
- Located the existing changelog API, version-aware modal, user setting, and layout trigger.
- Confirmed automatic “What's New” display is currently administrator-only and acknowledgement is persisted in server-backed user UI settings.
- Reached the first product clarification: whether this update guidance should be shown to every signed-in user or only administrators.
- User confirmed the existing version-update popup should appear once for every signed-in user after each version update; the current administrator-only trigger is not desired.
- Confirmed the current app version is `0.10.2`, memory is config-backed/default-enabled, and conversation mode is fixed after conversation creation.
- Next clarification is the modal content hierarchy: focused guided update only, or focused highlights plus the standard detailed changelog.
- User selected focused highlights plus the standard detailed changelog.
- Prepared three trigger approaches; the recommended design preserves the existing account-level application-version acknowledgement and ships as version `0.10.3`.
- User asked whether product code must change. Clarified that no product code has been changed yet; minimal code changes are required because the current automatic trigger is administrator-only and the current generic modal has no focused guide section.
- User approved a code-based solution on the condition that no Docker image is rebuilt; a guarded static/source hotpatch is acceptable.
- Prepared the concise design for final approval before implementation.
- User corrected the task: a configurable administrator announcement popup had already been implemented before the upgrade, and the management UI was omitted during the update.
- Verified original commit `d2420ff67`; current branch has the modal/layout consumer from replay `92c49dfae` but lacks the admin General controls and admin config API fields.
- Root-cause investigation continues by comparing the original five-file implementation against the current schemas before writing a regression test.
- Confirmed the exact regression boundary against `d2420ff67`, `92c49dfae`, and the v0.10.2 merge history.
- Declared separate source, isolated-runtime, and formal-live truth surfaces.
- User's correction and hotpatch constraint establish the approved recovery design: restore the prior administrator publisher without an image rebuild.
- Entered TDD RED phase; no product code or remote runtime has been changed yet.
- RED evidence: backend announcement contract failed 3/3 and General presentation failed 3/3 for the expected missing fields; the initial missing `pytest` executable was corrected by using `.venv/bin/pytest` and was not counted as evidence.
- GREEN implementation restores DB defaults, protected admin read/write fields, authenticated `/api/config` projection, General settings controls, and Chinese administrator labels.
- Focused GREEN evidence: backend 3 passed; frontend 4 passed.
- Drafted the publishable Chat/Agent/memory announcement at `announcement-content.md` with key `2026-07-chat-agent-memory-v1`.
- Related regression: backend announcement/default-seeding/cache tests 9 passed; frontend announcement and Chat/Agent UI tests 49 passed.
- Production frontend build completed successfully with `npm run build` and wrote `build/`; no Docker image was built.
- Full-repository `npm run check` remains red on pre-existing type errors in unrelated legacy components (first failures in `RichTextInput/AutoCompletion.js` and `listDragHandlePlugin.js`). The production build and scoped tests do not report an announcement-component error.
- Requested an independent read-only review of the scoped diff before commit.
- Completed fresh read-only remote preflight. Isolated and formal WebUI/AgentScope containers are healthy, restart zero; both WebUI services run four workers on the same image. Formal live remains container `ae1b858332b7...` with the four previously observed worker PIDs.
- Independent review found no Critical issue and confirmed the admin-only mutation boundary, authenticated projection, DB-direct multi-worker reads, and Markdown sanitization.
- Review fixes completed with a second RED/GREEN cycle: legacy submissions now preserve omitted announcement fields; enabled announcements require non-empty key/content in backend and frontend; failed admin-config loads remain in the loading state rather than exposing a partial form.
- Final related regression after review fixes: backend 13 passed; frontend 52 passed.
- Rebuilt the final static package successfully (`6375` transformed modules, adapter-static completed, exit 0). Only existing repository-wide Svelte warnings remain.

## 2026-07-28 isolated hotpatch and E2E

- Prepared host-side rollback backup for `open-webui-pr7`, seeded `build.next` from the running build, and checksum-synced the final local build. Only 40,112,745 of 261,464,644 regular-file bytes differed; post-sync checksum dry-run was empty.
- Installed backend source first, rotated all four Uvicorn workers with targeted socket-to-PID readiness, then atomically switched the merged static directory. Result: four replacements, 350 health probes with zero failures, zero traceback, container/image/start/restart anchors unchanged.
- Browser administrator session rendered the restored Chinese General controls and existing persisted announcement config, then successfully published v1 and v2 probes through the UI.
- A 16-request keep-alive probe covered four distinct worker PIDs `11020 11156 11375 11510`; all returned the same v1 announcement hash.
- Dedicated ordinary user browser showed the v1 popup with real Markdown strong rendering, persisted “Got it” server-side, did not re-show after localStorage acknowledgement deletion, and re-showed immediately after the administrator changed the key to v2. Both browser sessions had zero console errors.
- Captured the rendered probe at `output/playwright/pr7-announcement-user-v1.png` (untracked user output, not committed).
- Restored the complete pre-E2E administrator config snapshot through the protected API. A second four-PID probe returned one identical restored-announcement hash.
- Postflight found host UID ownership preserved by `docker cp`; fixed isolated build/backend metadata to `root:root` without worker restart and hardened the live install script to chown staged files before switch.
- Formal live remained untouched throughout isolated acceptance: container `ae1b858332b7...`, image `sha256:ab6d8f1816a...`, started `2026-07-28T03:19:21Z`, healthy, restart zero, original worker PIDs unchanged.

## 2026-07-28 formal live hotpatch

- Prepared a fresh formal-live rollback backup and checksum-verified the payload before mutation. The live WebUI container/image/start/restart anchors and the existing pgvector hotpatch hash were recorded in the guarded installer.
- Installed the three backend source files, rotated four Uvicorn workers one at a time, and atomically switched the static build without rebuilding or recreating the container. Rotation produced four replacement PIDs and 508 successful health checks with zero HTTP failures.
- The guarded postflight found two `Traceback` markers in the exact rotation window, so publication was deliberately paused. A narrow follow-up is expanding only those blocks and counting startup/death events before any announcement config is changed.
- Expanded inspection found four markers: two pairs from two file-upload background tasks. Each pair starts with `remote origin is not allowed` and then prints a chained full ASGI stack whose process root is `SpawnProcess-*`; the process-root frame is not itself evidence of a worker startup failure. Exact terminal lines and PID lifecycle events are being correlated before clearing the pause.
- The exact event correlation confirms two unrelated PaddleOCR-VL upload failures at `06:34:46Z` and `06:37:12Z`. Each terminated as `RuntimeError: ... remote origin is not allowed`; the four `Child process died` events are exactly the planned old PIDs, each followed by one new `Started server process` PID. There is no extra death/respawn event.
- The first formal postflight invocation exposed a local evidence-script bug: the existing readiness probe requires one target PID, but the wrapper called it without an argument. It made no remote mutation. The wrapper was corrected to enumerate exactly four workers and probe each PID independently.
- A second postflight attempt exposed a latent bug in that reused probe's targeted mode: it proved the requested PID but then serialized unselected PIDs and raised `KeyError`. The serializer now reports only actually selected workers; the failure was in evidence formatting after a successful health request and caused no service mutation.
- Corrected postflight then proved live worker PIDs `10531 10762 10886 11017`, root-owned sources/build, the expected static and pgvector hashes, healthy/restart-zero unchanged container anchors, and unchanged AgentScope anchors.
- The first authenticated live consistency probe returned HTTP 401 when an isolated-stack token was reused. Password sign-in with the isolated credential file then returned HTTP 400, and in-container re-signing of the isolated IDs still returned 401. None of these attempts changed config or user records.
- A safe in-container diagnosis proved both tokens decoded with the same key used by PID 1, but neither isolated user ID exists in the live user table. Therefore identity data is not shared between these stacks; the secret-mismatch hypothesis was rejected and its temporary fallback removed.
- The corrected path follows the already accepted formal-cutover procedure: resolve one administrator ID read-only from the exact live database, sign a two-hour token inside live, and require both session-role and protected admin-config authorization before use. No account is created and no role is changed.
- Live administrator ID resolution succeeded, but the first token write was blocked by a stale root-owned `/tmp` token copied during diagnosis. The wrapper now removes only its exact namespaced temporary files before issuance; no token or announcement config was produced by the failed attempt.
- The corrected token is accepted as admin through the host entrypoint and protected admin-config endpoint, while the first direct-container `/api/config` request returned 401. Publication remains paused; a socket-to-PID matrix will now distinguish a route/probe issue from real cross-worker authentication inconsistency.
- The PID matrix covered all four live workers and both authenticated routes returned only HTTP 200. The standalone announcement probe still saw 401; its remaining unique condition is copying onto a token path left by the matrix probe. The wrapper now removes only its exact container temp paths first and verifies the copied token digest before execution.
- After namespaced temp cleanup and digest verification, the pre-publish announcement probe passed: 16 keep-alive requests covered PIDs `10531 10762 10886 11017`, all returning the same prior announcement hash `c51f511a...`. The release announcement is now cleared for snapshot-backed publication.
- Published key `2026-07-chat-agent-memory-v1` after saving a complete protected admin-config snapshot. Exact API readback produced announcement hash `4d602987...`; a second 16-request probe covered all four unchanged PIDs with that same hash.
- The public live URL `https://ai.shuofang.cloud` rendered the real modal with the Chat/Agent title, fixed-mode warning, separate System Prompt/Terminal/tools/Skills defaults, and memory guidance. DOM inspection found all required sections, Markdown rendered structurally, and the browser console had zero errors. The close/Got-it controls were deliberately not clicked to avoid changing the real administrator's acknowledgement setting.
- Protected settings readback after the browser run confirmed `announcement_acknowledged=false`; the browser was closed without invoking either acknowledgement control.
- Final auth cleanup removed all local and remote short-lived tokens/identity files and retained only token-free isolated/live config snapshots.
- Final precise live window `06:58:00Z–07:04:33Z` contained zero traceback, child death/start, ReadTimeout, runtime-finalization marker, HTTP 5xx, or ERROR lines.
- Final postflight again proved unchanged WebUI/AgentScope container, image, start, health, and restart anchors; all four live worker PIDs remained `10531 10762 10886 11017` and all installed source/build ownership remained `root:root`.
- Fresh completion verification: backend announcement/default/cache suite `13 passed`; ten related frontend files `115 passed`; production build transformed `6375` modules and completed with exit 0; all task shell scripts passed `bash -n`, all task Python scripts compiled, targeted Prettier passed, and `git diff --check` passed.
