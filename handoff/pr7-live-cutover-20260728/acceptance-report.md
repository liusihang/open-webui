# PR7 formal live cutover and hotpatch acceptance — 2026-07-28

## Decision

- **GO — retain the currently running formal live PR7 service at four workers.**
- **NO-GO — do not recreate/restart from the current image until a durable image is built from commit `8a9395179`.** The running container contains a verified single-file hotpatch that is not present in image `sha256:ab6d8f...`.
- No image rebuild or container recreate was performed for the hotpatch, per user direction.

## Final formal live anchors

- WebUI container: `ae1b858332b7bbe252359d46e610a7b595fa6bad36b459187955737cb386e255`
- Image: `open-webui:pr7-chat-agent-dual-mode-1d8dba8a7-slim`
- Image ID: `sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b`
- Started at: `2026-07-28T03:19:21.700659218Z`; health `healthy`; restart count `0`
- Final host worker PIDs: `520375 521504 525837 528771`
- Database revision: `c0d3b4a5e6f7`
- Formal AgentScope runtime: container `2f96c76d...`, image `sha256:f7396...`, healthy, restart `0`
- Base Compose SHA-256: `7fff73a9037687460bd6c27669e9224203241546928173106c9999d6b3425da1`
- Candidate override SHA-256: `7e7d3681f403142a6d97cd74052e49290a4241da540c2506b09be32b419a0152`
- Base `.env` SHA-256: `419b002b069c62d7ff2978bcf9b4a005dedde0a8d7de17df34a3e8d7d14583f0`
- Installed pgvector source SHA-256: `2ce356413ce67047739487fc0833c69c912cef0fb456b2f58bc9bd35b543f156`

## Acceptance matrix

| Area | Result | Evidence |
| --- | --- | --- |
| Four real workers | PASS | Distinct worker PIDs were pinned by established keep-alive sockets and real HTTP requests. Final hotpatch rotation targeted each replacement PID before moving to the next. |
| Cross-worker config/model consistency | PASS | All four formal-live workers returned the same model catalog and Chat/Agent private/public profile revisions and defaults. |
| Function/tool/module/content cache | PASS | Isolated four-worker acceptance created, updated, and deleted temporary functions/tools; every PID converged, including valves and model visibility. Exact cleanup returned temporary rows to zero. |
| Startup singleton | PASS | Initial formal startup: four server processes, three singleton skips, one dependency owner, one scheduler, one tool initialization, and one terminal initialization; no startup failure or respawn loop. |
| Chat SSE | PASS | Formal Chat stream returned 12 content deltas, 16 data events, `[DONE]`, and the marker in 2.423 s. Final post-hotpatch concurrency returned 2/2 completed SSE streams. |
| Native Agent sequence | PASS | `run.running -> final.started -> final.delta -> run.completed`; full interaction additionally proved commentary/tool/approval/tool output/final ordering. |
| Approval/user input/cancel/refresh | PASS | Approval accepted/rejected, user input accepted/cancelled, duplicate decision idempotency, four-worker refresh recovery, and explicit `run.cancelled` all passed. |
| Concurrent read/SSE load | PASS | Final run: 28/28 success, 0 errors. Models 8, knowledge list 4, knowledge search 4, file list 4, file count 4, Chat SSE 2. |
| pgvector invalidated connection | PASS | Probe terminated only its own two checked-out read connections; `get()` and `search()` each logged one bounded retry and returned one row. Writes are not retried. |
| Hotpatch availability | PASS | Final worker rotation recorded 781/781 host `/health` responses, zero failures, four targeted replacement proofs, no container recreate/restart. |
| Final stability/log gate | PASS | 13 samples at 30 s intervals: worker PIDs unchanged, WebUI/runtime healthy, restart 0. Exact final window: four planned child replacements, four starts, zero traceback, zero pgvector read error, zero runtime/read timeout. |
| Isolation boundary | PASS | Dedicated formal runtime was used; isolated runtime stayed at the same container/image/start/health/restart anchor. |

## Final post-hotpatch performance

- Models, concurrency 8: 8/8; p50 2769.04 ms; p95/max 4012.45 ms.
- Knowledge list, concurrency 4: 4/4; p50 55.12 ms; p95/max 60.57 ms.
- Knowledge search, concurrency 4: 4/4; p50 30.83 ms; p95/max 41.27 ms.
- Files list, concurrency 4: 4/4; p50 56.43 ms; p95/max 58.37 ms.
- Files count, concurrency 4: 4/4; p50 25.74 ms; p95/max 28.67 ms.
- Chat SSE, concurrency 2: 2/2 HTTP 200 and done; p50 2298.04 ms; p95/max 2661.42 ms.
- Final point-in-time resource snapshot: WebUI 31.78% CPU/about 1.50 GiB, runtime 0.17%/about 107 MiB, DB 2.75%/about 2.31 GiB; DB connections 54 total, 1 active, 53 idle, below max 200. CPU is an instantaneous sample taken immediately after acceptance traffic, not a sustained average.

## Root causes and fixes

1. A pooled PostgreSQL connection can be closed after checkout; `pool_pre_ping` cannot prevent a close between checkout and query. Pgvector read methods previously logged and returned an empty result. Commit `137018355` centralizes one retry only for SQLAlchemy `connection_invalidated` across idempotent reads (`search`, `hybrid_search`, `query`, `get`, `has_collection`). Writes remain fail-fast/non-retried.
2. Merge commit `45e1b16ba` retained the HNSW underfill regression test but dropped its exact-scan implementation. Commit `8a9395179` restores the prior bounded exact-scan fallback. The complete pgvector test file is now `7 passed, 1 warning`.
3. Uvicorn 0.41.0 HUP is sequential but not readiness-gated. Live rotation therefore used graceful one-worker-at-a-time replacement and direct socket-to-PID HTTP proof, preserving serving capacity.

## Backups and rollback

- Fresh database backup manifest: `/home/aiserver/staging/pr7-live-prep-20260727/backups/20260728-live-cutover-102856/manifest.env`
- Dump: 8,040,589,539 bytes; SHA-256 `3517c13e35520c7e6d0d1735ab20545e08cfc7eb3f22c8d8e5da5572e8e171ec`; `pg_restore --list` passed.
- Original image pgvector file: `/home/aiserver/staging/pr7-live-prep-20260727/hotpatches/20260728T041200Z/pgvector.py.before`
- Final pre-patch backup: `/home/aiserver/staging/pr7-live-prep-20260727/hotpatches/20260728T045907Z/pgvector.py.before`
- Normal application rollback remains the reviewed old-image-on-migrated-schema path. Full database restore is disaster recovery, not the fast path.

## Isolated stack status

- The original four-worker acceptance restored its original isolated WebUI image/one-worker configuration and left formal live untouched.
- A later explicit user request created the current persistent latest-image test stack. It intentionally remains available on port `18085` with four workers (`2038078 2038079 2038080 2038081`) and health true; this is not an un-restored acceptance override.

## Durability action

The current runtime is accepted and should be retained. Before any planned container recreate/restart, build a new slim image from commit `8a9395179`, run the focused pgvector reconnect/full-file tests and the existing four-worker smoke, then update the candidate override to that immutable image. Until then, treat the current container as hotpatched and do not recreate it.
