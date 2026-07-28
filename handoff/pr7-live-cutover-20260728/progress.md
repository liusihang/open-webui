# PR7 live cutover progress — 2026-07-28

## Checkpoint 0 — authorization received

- User explicitly authorized starting the live switch after accepting the latest isolated test stack.
- Working interpretation: deploy the exact accepted PR7 candidate and enable four Uvicorn workers on formal live.
- Selected safeguards: `upgrade-live-service` workflow plus file-backed checkpoint logging.
- No formal live mutation has occurred in this cutover task yet.

## Next checkpoint

Complete read-only preflight, resolve the exact deployment/rollback commands, and verify fresh backup prerequisites.

## Checkpoint 1a — artifact orientation

- Located the guarded live release package and exact latest-candidate acceptance package.
- No live command has been run yet in this cutover task.

## Checkpoint 1b — release constraints

- Recovered the prior rehearsal timings and rollback topology.
- Determined that the older release script is safety-reference material only because its image/hash pins predate the latest accepted candidate.
- Next: inspect the current live-preparation artifacts and current repository/runtime state before adapting or invoking any mutating command.

## Checkpoint 1c — repository and package selection

- Verified current branch/HEAD and preserved pre-existing dirty paths.
- Located the newer candidate-specific controlled live deployment package from commit `66db6827f`.
- No formal live command has been run yet.

## Checkpoint 1d — controlled-script review

- Read the full backup, maintenance, migration, deployment, rollback, runtime-verification, and guard-test scripts.
- Confirmed they mutate only the intended WebUI/DB schema surfaces and preserve unrelated services.
- Next: inspect profile/probe helpers, then run fresh read-only remote preflight and safety guards.

## Checkpoint 1e — profile-template regression (red/green in progress)

- Added a focused contract test for the exact profile defaults accepted on the latest immutable candidate.
- RED evidence: the old template failed with `agent_defaults_mismatch` because it selected unaccepted web-crawl and Skill resources.
- Applied the minimal payload change to Agent=`terminals` + `sub_agent`, Skills empty; updated the operational runbook and retained the historical preparation report with an explicit supersession note.
- GREEN evidence: focused template contract passed, changed shell scripts passed `bash -n`, JSON parsed with `jq`, and `git diff --check` passed.

## Checkpoint 1f — first remote read-only preflight

- Confirmed live container/image/start/health/restart anchor, Compose and `.env` hashes, candidate image/revision, DB `f3` head, connection env keys, and core counts.
- Inventory then failed only while formatting a service without a Docker healthcheck. No service, file, database, or configuration was changed.
- Re-run passed end-to-end with null-safe health parsing and recorded all formal anchors, counts, workers, health endpoints, disk, resources, candidate identity, and rehearsal head evidence.
- Live remains untouched. Preflight is not yet closed because AgentScope runtime topology and staged release hashes still require verification.

## Checkpoint 1g — runtime topology blocker

- Proved no formal runtime exists and candidate Compose supplies neither runtime URL nor token.
- Isolated runtime remains healthy but isolated and will not be reused.
- Decision: keep old live serving; repair and test the release package before starting backup or maintenance.

## Checkpoint 1h — runtime release repair (red/green)

- RED: focused release contract failed with `missing_runtime_compose_contract=agentscope-runtime:` against the previous candidate package.
- Implemented the minimum complete topology: dedicated one-worker runtime, owner-only shared-token env, persistent state, formal network, exact image/health/token checks, WebUI runtime wiring, deploy refusal when runtime is unhealthy, and rollback removal.
- GREEN: runtime release contract passed, profile contract still passed, all changed shell scripts passed `bash -n`, and `git diff --check` passed.
- Formal live is still serving the old image; no backup, runtime container, migration, or maintenance action has started.

## Checkpoint 1i — remote release staging

- Preserved the previous staged release at `/home/aiserver/staging/pr7-live-prep-20260727/release-snapshots/before-runtime-fix-20260728-102513` with an owner-only SHA-256 manifest.
- Synced only the reviewed candidate/runtime/profile release artifacts.
- Remote guard suite passed: confirmation guards, candidate Compose render, exact accepted profile contract, runtime release contract, and old live anchor unchanged.
- Local SHA-256 values for all 11 synced files were recorded for an exact remote integrity comparison.

## Checkpoint 1 complete — all preflight gates passed

- All 11 staged artifact hashes matched local committed files.
- Remote guard, merged Compose, exact accepted profile, runtime release contract, current old-image runtime verification, DB-backed resource validation, and formal-runtime-absence checks passed.
- Formal live remains original image/container, healthy, DB `f3`, four workers, restart 0.
- Proceeding to a new verified online backup; no maintenance or migration yet.

## Checkpoint 2a — fresh backup started

- Run ID: `20260728-live-cutover-102856`.
- Initial state: `running / pg_dump`.
- Initial live observation: original container/image, healthy, restart 0, four workers; WebUI ~1% CPU and DB ~70% CPU.
- Backup is detached and status-file driven. No runtime, migration, or maintenance action will begin until state is `complete / verified` and manifest integrity is rechecked.

## Checkpoint 2 complete — fresh rollback backup verified

- Run ID: `20260728-live-cutover-102856`.
- Manifest: `/home/aiserver/staging/pr7-live-prep-20260727/backups/20260728-live-cutover-102856/manifest.env`.
- Dump: 8,040,589,539 bytes; SHA-256 `3517c13e35520c7e6d0d1735ab20545e08cfc7eb3f22c8d8e5da5572e8e171ec`.
- `pg_restore --list` passed; source `f3`; Compose hash exact; duration 2,254 seconds.
- Live remained original/healthy/restart 0/four workers throughout. DB load returned to baseline after completion.

## Checkpoint 3a — runtime prepare stopped before creation

- Guarded runtime preparation failed at host state-directory creation under `/srv/openwebui-migration/data`.
- No formal runtime container was created; old live and isolated runtime anchors remained unchanged.
- Cutover remains outside maintenance. Next action is read-only path/permission diagnosis, not privilege escalation.
- Diagnosis confirmed root-owned `/srv` is intentionally not writable, while the 0700 prep root is writable. RED test `runtime_state_default_not_owner_writable` captured the bad default; state default was moved to prep-root persistent storage without sudo/chown.

## Checkpoint 3b — dedicated formal runtime ready

- Re-staged the path fix and reran remote guards; all passed with old live unchanged.
- Created `openwebui-agentscope-runtime` only on the formal Compose network.
- Runtime container `2f96c76d...`, exact image ID `sha256:f7396...`, running healthy, restart 0.
- Runtime env is owner-only at `/home/aiserver/staging/pr7-live-prep-20260727/private/runtime.env`; state is under the owner-only prep root. Tokens were not emitted.
- Old WebUI and isolated runtime anchors remained byte-for-byte unchanged. The existing `openwebui-frpc-preview` orphan warning was observed but not acted on.
- Next gate: real selected-provider response through old live, then maintenance/migration/deploy.

## Checkpoint 3c — provider gate passed

- Model: `bifrostapi.Cliproxy/gpt-5.5`.
- Result: HTTP 200, SSE content type, 17 data lines, 12 content deltas, `[DONE]`, marker present, 2.553 seconds.
- Initial probe false-negative was isolated to raw-frame marker search; delta reassembly fixed the probe and the rerun passed.
- Old live anchor remained unchanged. Entering the authorized maintenance window next.

## Checkpoint 3d — candidate runtime cutover complete

- Maintenance: 11:17:26–11:21:46 +08:00.
- Migration reached `c0d3b4a5e6f7`; candidate container `ae1b8583...` became healthy with restart 0 and four workers `115305 115306 115308 115309`.
- Dedicated runtime remained healthy/restart 0; WebUI -> runtime health passed. DB/Redis/Bifrost/OnlyOffice/isolated-runtime anchors were unchanged.
- First candidate acceptance invocation stopped before token issuance because the reusable script hard-coded the isolated admin ID. Added a red test and runtime-configurable admin input; no profile or request mutation happened in the failed invocation.
- Second invocation saved the intended profiles, then a retained worker-pinned socket expired before the catalog request. Exact evidence proved no worker death or restart. RED fake-session regression reproduced the stale socket; pinning now requires final liveness and repins expired sockets.

## Checkpoint 4a — core live candidate acceptance passed

- Formal provider/model: `bifrostapi.Cliproxy/gpt-5.5`.
- Four real container workers `11,12,13,14` were each pinned by distinct sockets; all exposed the model and identical Chat/Agent private/public revisions/defaults, with no public System Prompt exposure.
- Chat SSE: 12 content deltas, 16 data events, `[DONE]`, marker present, 2.423 seconds.
- Native Agent: `run.running -> final.started -> final.delta -> run.completed`, marker present, completed in 8.551 seconds.
- Accepted profile heads: Chat `4cc013ae-...`, Agent `226ea134-...`; Agent defaults are Terminal `terminals`, tool `sub_agent`, Skills empty.
- Candidate container remained healthy/restart 0 before and after.
- Next: run the safe cross-worker approval/user-input/tool sequence and exact cleanup, then startup/anomaly/load gates.

## Checkpoint 4b — interaction and cancellation passed

- Full cross-worker interaction probe passed in 72.911 seconds and deleted its temporary tool.
- Approval accepted: `tool.requested -> approval.requested -> approval.completed -> tool.completed -> final.started -> final.delta -> run.completed`; refresh recovered the waiting state on all four workers; duplicate decision returned historical completion.
- Approval rejected completed without tool execution; duplicate decision was idempotent.
- User input accepted crossed workers, refreshed successfully, called the safe tool, and completed; user input cancelled completed without a tool.
- Separate run cancellation reached `run.running -> user_input.requested -> run.cancelled` in 4.116 seconds.
- Interaction probe required an explicit dual-mode binding fix; RED request-body test then GREEN verified `chat_mode=agent` plus current Agent revision.

## Checkpoint 4c — concurrent live load passed

- Final corrected run: 28/28 successful, error rate 0%.
- Models 8 concurrent: p50 308.13 ms, p95/max 755.98 ms.
- Knowledge list 4 concurrent: p50 46.24 ms, p95/max 50.07 ms.
- Knowledge search 4 concurrent: p50 34.12 ms, p95/max 58.19 ms.
- Files list 4 concurrent: p50 63.03 ms, p95/max 68.41 ms.
- Files count 4 concurrent: p50 27.64 ms, p95/max 28.53 ms.
- Chat SSE 2 concurrent: 2/2 HTTP 200 and `[DONE]`, p50 2100.97 ms, p95/max 2178.79 ms.
- WebUI/runtime remained healthy with restart 0; post-batch memory was about 5.48 GiB and 106 MiB.

## Checkpoint 4d — singleton/anomaly and cleanup passed

- Startup counts: four server processes; three singleton skips; dependency owner/tool init/scheduler/terminal init each exactly one; dependency skips three.
- Zero child death, startup failure, UniqueViolation, WebUI/runtime Traceback, runtime ReadTimeout, and runtime_finalization ReadTimeout.
- DB connections 92; Redis connected clients 41 and blocked clients 0 at sample time.
- Exact cleanup removed six Agent runs and their 48 operations/36 events/4 decision executions, two temporary bindings, ten chat messages, and five chats. Final verification `0:0:0:0` also confirms no temporary interaction tool remains.
- Probe hardening committed as `18fa0157f`; runtime release fix is `552fb40e8`; accepted profile fix is `89265f241`.

## Checkpoint 5a — recurrent pgvector disconnect isolated; hotpatch test red/green

- The bounded post-cutover log window found two independent `PgvectorClient.get()` failures at 11:46 and 11:50 +08:00: PostgreSQL closed a pooled connection during a read-only `document_chunk` query.
- DB/WebUI/runtime containers and all four Uvicorn worker PIDs remained healthy and restart-free; PostgreSQL logged no restart/shutdown error. The candidate and old image contain the same `pgvector.py`, so this is not a PR7 source regression.
- User explicitly selected a no-rebuild/no-recreate path. Scope is therefore one source file plus a verified sequential worker reload only.
- RED: a focused fake-session test proved an invalidated `OperationalError` returned `None` without retry; a non-invalidated database error remained a no-retry case.
- GREEN: `get()` now rolls back, removes the invalid scoped session, and retries exactly once only when SQLAlchemy marks the connection invalidated. The focused pair passed `2 passed, 1 deselected`.
- The first test command failed during collection because the lightweight local environment lacked the runtime `pgvector` package; the same test was rerun with an ephemeral `uv --with pgvector` dependency. No project dependency metadata was changed.

## Checkpoint 5b — hotpatch/reload mechanics verified before live mutation

- Live remains container `ae1b8583...`, image `sha256:ab6d8f...`, healthy, restart 0, with original host worker PIDs `115305 115306 115308 115309`.
- Installed Uvicorn is 0.41.0. Its HUP path is sequential `terminate -> join -> start`, but does not wait for each replacement to complete application startup before moving to the next process.
- To preserve serving capacity, the selected path replaces the four original workers one at a time through graceful `SIGTERM`, waits for the supervisor-created replacement plus an additional `Application startup complete` marker, four-worker count, container health, and `/health` before continuing.
- A continuous host-port health monitor will run through all four replacements. Any anchor/hash/health/count failure stops the sequence; no container recreate or image rebuild is part of the procedure.
- The local patched file SHA-256 is `11af129ee58ac85002dbf1764aae648adf3f4c001c3ed9af4374ab5ad808fade`; installation is guarded by the exact original SHA-256 `c5520a79...` and creates an owner-only host backup/manifest before replacement.

## Checkpoint 5c — file installed; first worker replaced without downtime

- The guarded single-file install passed. Run ID `20260728T041200Z`; owner-only backup `/home/aiserver/staging/pr7-live-prep-20260727/hotpatches/20260728T041200Z/pgvector.py.before`; installed SHA-256 `11af129e...`.
- Container/image/health/restart anchors remained exact after the file copy. Existing workers continued running the already-imported old module until individually replaced.
- The first rotation invocation stopped before worker signals because the slim image lacks `ps`; PID discovery was changed to `/proc` parsing and a verify-only remote run proved master PID 1 and workers 11/12/13/14.
- Original worker 11 was then gracefully replaced by PID 2067 while workers 12/13/14 continued serving. Uvicorn did not emit `Application startup complete` for 2067 within the conservative log-marker timeout, so the guard stopped before touching worker 12.
- Exact socket-to-PID evidence then proved PID 2067 was already serving real `/health` requests. A targeted rerun also covered all four current PIDs 12/13/14/2067 in 36 requests. Therefore the missing log line is not a readiness failure; the gate is changed from log presence to direct targeted request proof.
- No container restart/recreate occurred, and the remaining three original workers are still untouched pending the revised guard.

## Checkpoint 5d — all workers hot-reloaded; broader read-path defect found

- Revised targeted-PID rotation completed for old workers 12/13/14. Final container PIDs are 2067/5347/5633/5811; every replacement accepted a directly mapped `/health` request before the next signal.
- During the final three replacements, the continuous host `/health` monitor recorded 441/441 HTTP 200 responses, with zero failures. Container/image/start time stayed exact, health remained healthy, and restart count remained 0.
- A one-off live semantic probe terminated only its own checked-out read connection and proved the installed `get()` retry recovers and returns a row.
- Post-hotpatch concurrency passed 28/28 with zero errors: models 8, knowledge list 4, knowledge search 4, files list 4, files count 4, and two concurrent completed Chat SSE streams.
- Final log audit found two traceback blocks from one `PgvectorClient.search()` OperationalError at 12:13:17, after the file copy but before any worker had reloaded it. This is not a new post-reload failure, but proves the root defect affects multiple idempotent pgvector read methods, not only `get()`.
- Decision: do not declare the first patch complete. Add a failing `search()` invalidated-connection test and centralize one-retry handling across idempotent pgvector reads; writes remain non-retried.

## Checkpoint 5 complete — final hotpatch accepted

- RED/GREEN expanded the retry from `get()` to all idempotent pgvector read paths. Five focused invalidated-connection tests passed; non-invalidated errors and the one-retry cap remain covered.
- The complete pgvector test file initially exposed a pre-existing merge regression: the HNSW underfill exact-scan test survived, but its implementation did not. Restored the prior bounded fallback; final file result is `7 passed, 1 warning`.
- Final installed source SHA-256 is `2ce35641...`; owner-only backup run is `20260728T045907Z`.
- Final four replacement workers are host PIDs `520375 521504 525837 528771`; every one accepted a targeted real request. Rotation monitor: 781 total `/health`, 0 failures; container/image/start time unchanged, restart 0.
- Final live reconnect probe passed both `get()` and `search()` after terminating only two probe-owned checked-out read connections.
- Final concurrency: 28/28, zero errors; two SSE streams completed. Final six-minute observation: 13/13 stable samples. Exact final log gate: four planned replacements/starts, zero traceback, zero pgvector read error, zero runtime/read timeout.
- Runtime decision: retain current live at four workers. Durability decision: do not recreate from the old image; build a future immutable image from `8a9395179` before any planned restart/recreate.
