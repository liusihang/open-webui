# PR7 live cutover findings — 2026-07-28

## Prior accepted facts requiring fresh verification

- Isolated candidate was reported healthy at four workers with image ID `sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b`.
- Formal live was reported healthy on image ID `sha256:7ec820b71fa94205b273cb8cd00344a130e1921ae8e643ba6192b0e58933bd45`.
- These are orientation only, not current truth; Phase 1 must re-read them.

## Decisions

- Reuse the exact already-built candidate image; do not rebuild during cutover.
- Keep the rollback point immutable and recreate only the OpenWebUI service.

## Artifact discovery

- The repository contains an earlier guarded release package at `handoff/pr7-four-worker-acceptance-20260724/release/` with release, rehearsal, PID-probe, and test scripts.
- The exact latest-candidate acceptance artifacts are under `handoff/pr7-latest-test-stack-20260728/`, including preflight, backup, cutover, rollback, runtime-check, and acceptance scripts.
- Prior memory explicitly warns that a successful rehearsal is not authorization; this task now has explicit user authorization, but still requires fresh preflight and backup.
- Prior memory also warns that four-worker startup can legitimately take several minutes during singleton-owned warmup, so health will be condition-polled rather than judged from the first transient state.

## Release constraints recovered

- The old guarded package targets image revision `5b35e9f1b`; it cannot be executed unchanged against the newer accepted image `1d8dba8a7`.
- Its safety model remains applicable: hash-pinned preflight, fresh backup no older than six hours, explicit migration owner, WebUI-only recreation, exact worker PID proof, and fail-closed rollback commands.
- Normal rollback is the old live image on the migrated database; full database restore is DR only. The rehearsal measured roughly 28 minutes for the production dump and 81 minutes for full restore.
- The latest candidate database head is `c0d3b4a5e6f7`, whereas formal live was previously `f3a4b5c6d7e8`; current heads and migration ancestry must be rechecked before selecting the release path.
- Latest isolated acceptance has two known product gaps: no `get-available-resources` skill in the isolated DB and no bundled `crawl4ai`, so the accepted Agent defaults are terminal plus `sub_agent`, not web crawl.

## Current repository state

- Branch: `codex/pr7-chat-agent-dual-mode-20260726`; HEAD: `8fdd8fd2ab5523cce13f32c7e5f8221eee0a57ff`.
- Product image source revision is still `1d8dba8a7`; later commits are deployment/handoff changes.
- Existing unrelated dirty artifacts remain in the earlier dual-mode handoff and browser output; they must be preserved and excluded from cutover commits.
- Commit `66db6827f` introduced a newer controlled live-upgrade package specifically for this candidate. Its runbook/scripts supersede adapting the older `5b35e9f1b` package if current checks pass.

## Controlled-script contract review

- Backup is online and fail-closed: it validates the `f3` head, snapshots the Compose file, creates a compressed custom-format dump, validates `pg_restore --list`, hashes the dump, and rejects any live-anchor drift.
- Maintenance stops only `open-webui`; migration runs from the candidate image against the existing shared DB network with application startup migrations disabled.
- Candidate deployment merges the immutable base Compose with one six-line override, recreates only `open-webui`, waits up to five minutes, and verifies image ID, four workers, environment, and internal health.
- Rollback before traffic release is schema-aware: stop WebUI, downgrade `c0 -> f3` with explicit data-loss acknowledgement, then recreate the old WebUI from base Compose only.
- The scripts intentionally fail closed and do not auto-rollback. A failed cutover must be diagnosed at the failed gate and then invoke the reviewed rollback command explicitly.
- Fresh-backup guard is one hour. Since the backup may take about 26–30 minutes, the provider smoke and maintenance transition must follow promptly after backup completion.

## Profile payload mismatch found before cutover

- The staged profile template still proposes Agent tools `web_search_and_crawl` + `sub_agent` and Skill `get-available-resources`.
- The exact accepted latest-image test stack instead uses Terminal `terminals`, tool `sub_agent`, and no Skills because `crawl4ai` is absent from the immutable image and the isolated DB lacked that Skill.
- Applying the stale template would deploy behavior that was not accepted and could select a tool whose dependency is absent. The live profile payload must be reduced to the exact accepted defaults before staging or application.
- The four-worker probe is suitable for final cache proof because it pins real keep-alive sockets to four worker PIDs, performs an Agent profile mutation, proves private/public convergence, then restores the prior profile.

## Fresh live preflight — 2026-07-28 10:13 +08:00

- Formal WebUI anchor still exactly matches the prepared old container/image/start time: `78faa81d...`, image ID `sha256:7ec820...`, healthy, restart 0.
- Base Compose and `.env` hashes still match `7fff73...` and `419b00...`; DB remains `f3a4b5c6d7e8`.
- Candidate image identity is exact: `sha256:ab6d8f...`, source label `1d8dba8a7...`.
- Live core counts are `users=40`, `chats=3389`, `files=8605`, `knowledge=70`, `functions=13`, `tools=8`; retain for post-cutover comparison.
- Existing WebUI has four stable worker PIDs `1007971`, `1011557`, `1011836`, `1034666`; `/health` and `/health/db` both passed.
- DB, Redis, Bifrost, OnlyOffice, and WebUI container anchors were recorded; all have restart count 0 and all health-enabled services are healthy.
- Available disk is about 942 GB; current resource snapshot is WebUI 3.415 GiB, DB 1.075 GiB, Redis 9.203 MiB.
- The merged candidate resolves to the exact image, `UVICORN_WORKERS=4`, and `ENABLE_DB_MIGRATIONS=false`.
- Critical open question: base Compose reports no AgentScope runtime service even though the runbook refers to one. Before backup/cutover, verify whether an externally managed runtime container exists and whether the candidate's resolved runtime endpoint can reach it.

## Blocking runtime topology defect

- Formal live has no AgentScope runtime container or runtime environment configuration.
- The only runtime container is `openwebui-pr7-agentscope-runtime` on the isolated `openwebui-pr7_default` network; it must not be shared with formal live.
- The current candidate merge resolves `AGENT_RUNTIME_BASE_URL=null` and no service token. Direct image cutover would ship a broken Agent mode even though Chat could start.
- Root cause is a release-package omission: the older guarded package defined a production runtime service/token/state/network, but the newer candidate-specific six-line override retained only the WebUI image/workers/migration fields while its runbook incorrectly assumed a runtime already existed.
- Required fix before any backup or maintenance: add a formal-live-specific runtime service, owner-only token env, exact image/health/network verification, deploy dependency guard, and rollback removal path. Never attach the isolated runtime to live.

## Runtime release design

- Runtime starts before maintenance while old WebUI continues serving; this moves image/startup/network risk outside downtime.
- The formal runtime uses image ID `sha256:f7396...`, exactly one worker, a 0600 env file under the private prep directory, and persistent state at `/srv/openwebui-migration/data/agentscope-runtime`.
- The shared token is generated remotely and never emitted. Deploy compares the token inside WebUI and runtime without printing it.
- Candidate deployment remains WebUI-only and refuses to proceed unless runtime is healthy, restart-free, on the formal network, and reachable from WebUI.
- Pre-traffic rollback restores old WebUI/schema and removes only the new formal runtime container while preserving private env/state for diagnosis.

## Runtime preparation result

- Formal runtime health and exact image checks passed after about 50 seconds.
- Creating the runtime did not recreate, restart, or reconfigure old WebUI; it also did not touch the isolated runtime.
- Compose reported an existing orphan `openwebui-frpc-preview`. It is unrelated and was preserved; no `--remove-orphans` action is authorized or needed.

## Remote guard result

- `guard_tests=passed`
- `compose_override=passed`
- `profile_template=accepted_latest_stack_defaults`
- `agent_runtime_release=passed`
- `live_anchor_unchanged=yes`

## Live acceptance summary

- Candidate and runtime are operational on formal live with no rollback gate failure.
- Four-worker identity and cross-worker mode/profile/model convergence were proven with real pinned sockets.
- Chat SSE, native Agent, approval, user input, cancellation, refresh recovery, tool output, idempotency, and concurrent read/SSE traffic all passed.
- All test-only tool/run/chat/binding records were precisely removed after evidence capture. Administrator Chat/Agent profile heads remain intentionally persistent.

## User-directed hotpatch boundary

- Do not rebuild or recreate the running live container to address recurrent pgvector connection closures.
- A permissible path is a tested in-place source hotpatch plus Uvicorn's verified graceful sequential worker reload, with container identity and service availability preserved.
- If the installed Uvicorn supervisor does not prove that signal behavior, do not signal live workers.

## Pgvector disconnect root cause and repair boundary

- `pool_pre_ping=True` validates a connection only when it is checked out; it cannot prevent PostgreSQL/network closure after checkout and before/during the query.
- The observed SQLAlchemy `OperationalError` marks the connection invalidated, but `PgvectorClient.get()` previously caught every exception, logged a traceback, and returned `None`. That converts a transient stale-connection event into a degraded knowledge read.
- The smallest safe recovery is limited to the idempotent read method: rollback, discard the invalid scoped session, and retry once. Non-invalidated errors are not retried, and write paths remain unchanged.
- Empty non-encrypted reads now also close their read-only transaction before returning `None`; this matches the successful-read transaction cleanup contract.

## Reload decision

- Uvicorn 0.41.0 HUP is process-sequential but not readiness-gated: it starts one replacement and immediately proceeds to terminate the next original worker.
- A manually readiness-gated worker rotation is lower risk for this live service: each original worker receives graceful `SIGTERM`; the supervisor's five-second health loop detects exit and starts exactly one replacement; the next original worker is left untouched until four workers, a new startup-complete log marker, container health, and the host `/health` endpoint all converge.
- The expected `Child process [...] died` supervisor messages are planned replacements, not a respawn loop. Exactly four and only within the controlled timestamp window are acceptable.

## Readiness truth surface correction

- Uvicorn's `Application startup complete` log marker was absent for the first replacement even after it had begun accepting requests. Treating the log as the sole readiness truth produced a safe false negative.
- The stronger truth surface is an established keep-alive socket mapped through `/proc/<pid>/fd` and `/proc/<pid>/net/tcp`, followed by a second HTTP 200 `/health` on that same socket. PID 2067 passed this direct proof; one targeted sample also reached all current workers 12/13/14/2067.
- Subsequent replacements are gated on the newly created PID accepting a mapped real request, plus four process entries, container health/restart/hash anchors, and host `/health`. Missing log text no longer blocks an otherwise proven-ready worker.
