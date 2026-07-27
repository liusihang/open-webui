# PR7 Chat/Agent dual-mode controlled live upgrade runbook

## Scope and immutable anchors

Target truth surface: `aiserver:/srv/openwebui-migration`, service/container `open-webui` only.

- Current live image ID: `sha256:7ec820b71fa94205b273cb8cd00344a130e1921ae8e643ba6192b0e58933bd45`
- Current Compose SHA-256: `7fff73a9037687460bd6c27669e9224203241546928173106c9999d6b3425da1`
- Current database revision: `f3a4b5c6d7e8`
- Candidate image ID: `sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b`
- Candidate source revision: `1d8dba8a77e6e8adc5952891bac83a2a7c5a4804`
- Candidate database revision: `c0d3b4a5e6f7`
- Final worker count: 4
- Final migration policy: explicit one-off Alembic; candidate WebUI starts with `ENABLE_DB_MIGRATIONS=false`.

Do not modify the base Compose file. The candidate is selected only by the prepared override. Do not restart DB, Redis, Bifrost, OnlyOffice, or AgentScope runtime. Do not query broad Bifrost logs.

## Prepared artifacts

- `live-prep-backup.sh`: online custom-format backup with checksum/list/live-anchor verification.
- `live-prep-rehearse-migration.sh`: disposable restore plus `f3 -> c0 -> f3` rehearsal.
- `compose.live-pr7-dual-mode-1d8dba8a7.yaml`: exact image, four workers, migrations disabled at app startup.
- `live-migrate-controlled.sh`: guarded explicit forward/downgrade migration.
- `live-enter-maintenance-controlled.sh`: guarded stop of only the old WebUI after the fresh backup.
- `live-deploy-controlled.sh`: guarded WebUI-only candidate recreation and health/PID checks.
- `live-rollback-controlled.sh`: guarded `c0 -> f3` rollback followed by old-image recreation.
- `live-verify-runtime.sh`: read-only image/health/revision/worker verification.
- `live-resource-inventory.py`: authenticated, sanitized resource/profile inventory; never emits the bearer token or System Prompt.
- `live-admin-mode-profile-template.json`: reviewed-payload template. Placeholders make accidental application invalid.
- `live-apply-admin-profiles.py`: exact-confirmation, administrator-token profile apply plus repeated sanitized head convergence reads; it never emits prompts or the token.

## Pre-cutover gates

1. Obtain explicit user authorization for the formal live change.
2. Re-run `live-verify-runtime.sh` against old image, revision `f3a4b5c6d7e8`, four workers.
3. Create a new backup immediately before migration. The migration guard rejects a dump older than one hour by default. Require:
   - custom-format dump exists;
   - SHA-256 matches the manifest;
   - `pg_restore --list` succeeds;
   - live container anchor is unchanged before/after backup.
4. Require the recorded disposable rehearsal to show:
   - restored revision `f3a4b5c6d7e8`;
   - upgraded revision `c0d3b4a5e6f7`;
   - downgraded revision `f3a4b5c6d7e8`;
   - identical before/after schema dump;
   - unchanged Chat identity signature;
   - old image recognizes its `f3` head after downgrade.
   - Do not repeat the restore on formal live host/storage. The 2026-07-27 same-host rehearsal saturated shared I/O and caused old-image worker respawns despite an unchanged healthy container. Future restore drills require separate storage/host or explicit I/O isolation.
5. Copy the release artifacts to `/home/aiserver/staging/pr7-live-prep-20260727/release`, validate permissions, and run Compose `config --quiet` with the base plus override.
6. The staged initial profile is Chat with explicit empty Terminal/tool/skill defaults, and Agent with Terminal `terminals`, tools `web_search_and_crawl` plus `sub_agent`, and Skill `get-available-resources`; both System Prompts are initially empty. Run the DB-backed validator before cutover and the authenticated sanitized inventory after upgrade. Administrators may revise this proposal before save. Do not add model or Reasoning Depth fields.
7. Prepared smoke model: `gpt-5.5`, which produced real isolated Chat/Agent output. Re-prove a real provider response immediately before formal cutover; do not use the isolated `gpt-5-codex-mini` route unless its provider error has been independently fixed.

## Forward sequence

1. Record live container ID, image ID, health, restart count, start time, Compose checksum, DB revision, four worker PIDs, and exact change-window start.
2. Announce the short maintenance window, then run `live-enter-maintenance-controlled.sh`. It stops only the old WebUI; DB, Redis, Bifrost, OnlyOffice, and runtimes remain running. Do not allow ordinary traffic between migration and post-deploy smoke acceptance.
3. Run `live-migrate-controlled.sh` with action `upgrade`, the verified fresh manifest, and the exact confirmation phrase embedded in the script. The stopped container remains available as the environment/network source.
4. Verify revision `c0d3b4a5e6f7` before recreating WebUI.
5. Run `live-deploy-controlled.sh` with its exact confirmation phrase. It recreates only `open-webui` using base plus override.
6. Require healthy status, candidate image ID, restart count zero, four real `spawn_main` worker PIDs, `ENABLE_DB_MIGRATIONS=false`, and internal `/health` success.
7. Authenticated API gates:
   - public `/api/config` succeeds and does not expose System Prompts;
   - administrator Chat/Agent profile GET/history/detail succeeds;
   - models, terminals, tools, skills, functions, knowledge, and files routes succeed.
8. Real browser/inference gates:
   - top Chat/Agent selector is visible only before conversation creation;
   - Chat mode binds and produces multi-delta SSE output with intended empty defaults;
   - Agent mode binds and produces commentary -> tool call -> tool output -> final multi-delta output;
   - cancellation and one approval/user-input flow complete;
   - refresh recovers in-flight Agent state;
   - two concurrent SSE streams finish with `[DONE]` and no `runtime_finalization`/`ReadTimeout`.
9. Run the DB resource validator again, then apply the reviewed administrator profile revisions with `live-apply-admin-profiles.py`. It reads current revision IDs immediately before save and performs 16 repeated sanitized head reads; retain the stronger pinned-four-worker probe as the final cache-invalidation evidence before releasing traffic.
   - The backend has no atomic two-mode save API. The helper saves Agent first and Chat second. If the second save fails, remain in maintenance, report the partial revision explicitly, and restore through the immutable history endpoint before releasing traffic; do not hide the failure with automatic compensation.
10. Observe the exact change window for worker exits/respawns, migration/profile errors, SSE failures, CPU/memory, DB connections, and Redis blocked clients. Query Bifrost only by exact request/session/time window if a provider failure needs attribution.

## Rollback boundary

Rollback is not an image-only operation. The old image does not know revision `c0d3b4a5e6f7` while automatic migrations are enabled in the base Compose configuration.

- Before general traffic is released: stop only WebUI, run guarded candidate downgrade `c0 -> f3`, then recreate only WebUI from the base Compose file.
- The downgrade drops new Agent-run and conversation-mode-profile schema/data. The rollback script therefore requires an explicit data-loss acknowledgement.
- After general traffic is released and new feature data exists, do not automatically downgrade. Stop and decide between a forward fix and restoring the pre-cutover dump; report the exact affected interval first.

Rollback acceptance is old image ID `sha256:7ec820b71fa94205b273cb8cd00344a130e1921ae8e643ba6192b0e58933bd45`, healthy, restart count zero, four workers, DB revision `f3a4b5c6d7e8`, and unchanged unrelated services.

## Completion evidence

Record backup manifest/checksum, rehearsal report, pre/post/rollback anchors, exact API and browser results, selected model output, four worker PIDs, resource measurements, anomaly-window logs, administrator profile revision IDs, and final go/no-go decision. Never record tokens, cookies, passwords, API keys, Terminal credentials, private tool content, or System Prompts.
