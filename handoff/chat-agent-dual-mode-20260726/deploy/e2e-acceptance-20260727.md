# Chat/Agent dual-mode isolated live acceptance — 2026-07-27

## Decision

**GO for a controlled PR7 live upgrade with four WebUI workers.** The candidate was built from committed source, migrated against the real isolated PostgreSQL database, exercised through authenticated API and browser surfaces, and then run with four real worker PIDs. Cross-worker profile invalidation, startup singleton ownership, Chat/Agent binding, native Agent streaming, interactions, cancellation, and non-destructive concurrency all passed.

This report does not authorize or perform the formal-live upgrade. The live container remained read-only and unchanged throughout. The release runbook should retain the same backup/migration/health/rollback gates and verify that the selected provider model is present immediately before cutover.

## Candidate

- Branch: `codex/pr7-chat-agent-dual-mode-20260726`
- Candidate commit: `1d8dba8a77e6e8adc5952891bac83a2a7c5a4804`
- Candidate image: `open-webui:pr7-chat-agent-dual-mode-1d8dba8a7-slim`
- Candidate image ID: `sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b`
- Image revision label: exact candidate commit above
- Requested reasoning UI commits `0c736a9e4` and `db98ad0e3` were already ancestors; no duplicate cherry-pick was performed.

## Defect found and fixed before acceptance

The first real PostgreSQL migration failed transactionally because `uq_conversation_mode_profile_temporary_binding_user_conversation` is 64 characters, above PostgreSQL's 63-character identifier limit. The database remained at `f8a9b0c1d2e3`, with no partial tables or Chat column.

Commit `1d8dba8a7` renamed the migration and ORM constraint to `uq_conv_mode_profile_temp_user_conversation` and added a PostgreSQL-dialect identifier-length regression. The retry then upgraded successfully to `c0d3b4a5e6f7`.

The verified pre-migration backup is:

- Path: `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/backups/chat-agent-dual-mode-20260727/webui-pr7-before-c0d3b4a5e6f7-20260727-170958.dump`
- Size: 224,441,848 bytes
- SHA-256: `85ce19785e478604f22e24463154825fb029126e14ffdff1ddf3d8e3d22344f6`
- `pg_restore --list`: passed

## Acceptance matrix

| Surface | Result | Evidence |
| --- | --- | --- |
| Image integrity | PASS | 6374 frontend modules built; backend `compileall` passed; Pyodide package version 0.28.3, core files and required offline PyPI wheels present. |
| Real PostgreSQL migration | PASS | Alembic reached `c0d3b4a5e6f7`; three profile tables, Chat binding column, Chat/Agent baseline heads, and the shortened unique constraint read back correctly. |
| Authenticated API/privacy | PASS | `/api/config`, admin profiles, models, and terminals returned 200. Public Chat/Agent projections omitted System Prompt, model defaults, and Reasoning defaults; private admin responses contained Prompt only on the authorized route. |
| Administrator UI | PASS | Admin Settings → Models rendered Chat/Agent tabs, enforced Prompt, Terminal/Tools/Skills/Filters/features, immutable revision history/detail/restore, and no model or Reasoning Depth field. |
| New-conversation selector | PASS | Real browser showed a top Chat/Agent radiogroup. Both modes initially used the existing system/user default model path. |
| Agent defaults | PASS | A temporary Agent revision initialized `terminals`, one safe tool, and one skill. The browser draft bound revision `33d0e0c4-f446-44f8-b571-301859b90e46`. |
| User temporary overrides | PASS | Browser toggled Terminal/tool/skill to empty and switched from the inherited model to `openai/gpt-5.5`; the mode revision stayed fixed and Reasoning remained independently `medium`. |
| Chat defaults | PASS | An explicit-empty Chat profile overrode a model-supplied default tool. The real request contained no Terminal/tool/skill/filter and bound Chat revision `c507d074-c2c2-48c1-bf99-e86be31af50b`. |
| Chat provider result | PASS | `gpt-5.5` returned `CHAT-LIVE-OK` and `STREAM-COMPLETE`; the UI persisted a two-message Chat with `mode=chat`. |
| Conversation immutability | PASS | Clicking Agent on the existing Chat displayed “This conversation mode is fixed…”; cancel preserved the same `/c/...` URL and Chat selection. DB mode/revision matched the UI request. |
| Native Agent streaming | PASS | Run `0b15a644-adbe-4da2-9935-87756e76b396`: two model commentary rounds, two requested/completed tool transactions, then one final start, five contiguous final deltas, and run completion in 36.239 seconds. |
| Existing-chat revision immutability | PASS | After later administrator head updates, the native Agent Chat remained bound to its original revision `1390b2f4-63b0-4d77-94e9-4e5ea3816bd9`. |
| Four real workers | PASS | Container process table showed master plus worker PIDs 11, 12, 13, and 14; pinned keep-alive client ports proved requests reached each PID. |
| Profile cache invalidation | PASS | All four private/public heads converged after save in 0.064 s and after restore in 0.063 s. Defaults and private Prompt hashes agreed; public Prompt exposure remained false. |
| Startup singleton | PASS | Four server processes; dependency installation ran once plus three skips; lifespan singleton ran once plus three skips; tool/terminal initialization ran once; zero worker finishes/errors/respawns; restart count zero. |
| Approval | PASS | Approved and rejected cases started/decided on different worker PIDs. Refresh recovered `waiting_approval` on all workers; duplicate decisions returned `historical_completed`. |
| User input | PASS | Accepted and cancelled cases were consistent across all workers. Accepted input survived connection refresh; duplicate responses returned `historical_completed`. |
| Run cancellation | PASS | A run waiting for user input started on PID 11, was cancelled via PID 14, and read `cancelled` on all four workers; duplicate cancel via PID 12 returned 200. |
| Concurrent Chat/SSE | PASS | Two concurrent streams, two successes, both `[DONE]`, six SSE data lines each, max 2042.28 ms. |
| Concurrent knowledge/files/models | PASS | 8 model requests plus four each for knowledge list/search and file list/count: 24/24 successes, zero errors. Max latencies: models 2940.57 ms, knowledge list 56.16 ms, search 40.55 ms, file list 83.96 ms, count 27.0 ms. |
| Runtime resources | PASS | Sample: WebUI 11.09–11.18% CPU, about 1.8 GiB; DB about 319 MiB with 53 connections (1 active); Redis about 8 MiB with 21 clients and 0 blocked; runtime about 59 MiB. |
| Exact-window anomalies | PASS | No traceback, error-level record, `ReadTimeout`, `runtime_finalization`, worker death, or respawn. Two existing async-context frontend-language warnings were observed. |

Browser evidence remains locally at:

- `output/playwright/dual-mode-agent-defaults-20260727.png`
- `output/playwright/dual-mode-agent-user-overrides-20260727.png`
- `output/playwright/dual-mode-admin-agent-profile-20260727.png`

## Runtime caveat reproduced

The browser's inherited `gpt-5-codex-mini` selection returned provider HTTP 502 with `unknown provider`. A later first user-input start also encountered one transient `Model not found` while refreshing the model directory. Immediate pinned reads before/after forced refresh showed the same 35-model catalog, including `gpt-5.5`, on all four workers; focused user-input retries then passed.

This is provider/catalog availability rather than persistent worker cache divergence. The live runbook should perform an exact selected-model inference precheck and pause/rollback if the intended model is absent. HTTP 200, Chat creation, or catalog presence alone must not be treated as provider success.

## Regression gates

- Focused frontend: 9 files, 125 tests passed.
- Expanded related frontend: 23 files, 286 tests passed.
- Migration-focused: 5 tests passed.
- Broader conversation-mode/profile backend: 264 tests passed.
- Ruff, Prettier, compile, and `git diff --check`: passed on the committed product tree.
- Production frontend build: passed, 6374 modules, static adapter output written.
- Independent final specification review: compliant.
- Independent final quality review: approved with no Critical/Important findings.

## Cleanup and restoration

- Eight exact E2E Chats deleted.
- Six exact Agent runs deleted with their 49 events, 65 operations, and four decision executions; no probe artifacts remained.
- Temporary tools deleted.
- Chat and Agent heads restored to baseline content and observed consistently by all four workers.
- Before downgrade: zero bound Chats and zero temporary bindings.
- Alembic downgraded cleanly to `f8a9b0c1d2e3`; profile tables and Chat binding column were absent afterward.
- Isolated WebUI restored to `open-webui:agentmode-v0102-4a4e43e206-slim`, image ID `sha256:fd6145b041f28269a0766e8f0f1ab91653a998745290041c43ef314c2456c8c4`, one worker, healthy, restart count zero.
- Candidate compose overrides were removed from the remote stack directory. The temporary BuildKit builder was removed; the verified candidate image and audit logs were retained.

## Formal live unchanged

Before and after acceptance, formal live remained:

- Container ID: `78faa81d479d8c5ef33a85277feeb3dc5de68861c3f25dcaac67285935f9c13e`
- Image ID: `sha256:7ec820b71fa94205b273cb8cd00344a130e1921ae8e643ba6192b0e58933bd45`
- Image: `open-webui:live-f8106c651-to-v0102-pr7-b3-7-onlyoffice-mergefix-slim-20260707013738`
- Health: healthy
- Restart count: 0
- Started at: `2026-07-07T03:53:51.178582025Z`

No formal-live file, container, image, configuration, database, or process was modified or restarted.
