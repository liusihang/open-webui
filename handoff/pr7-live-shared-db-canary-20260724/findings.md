# Findings: PR7 and production shared database

## Requirements

- Keep production and PR7 online simultaneously.
- Use PR7 as a test/transition version.
- Evaluate whether both can use one database without damaging live data.

## Current truth surfaces

| Surface | Production | PR7 dev |
|---|---|---|
| Container | `open-webui` / `78faa81d...` | `open-webui-pr7` / `df1ba2b4...` |
| Image | `sha256:7ec820b7...` | `sha256:fd6145b0...` |
| Port | host `80` | host `18085` |
| Compose root | `/srv/openwebui-migration` | `/home/aiserver/staging/openwebui-pr7-eea11194ed-test` |
| PostgreSQL | `openwebui-db`, database `webui_db` | `openwebui-pr7-db`, database `webui_pr7` |
| DB/code Alembic head | `f3a4b5c6d7e8` | `f8a9b0c1d2e3` |
| Redis | network-local `redis:6379/0` on `openwebui-migration_default` | network-local `redis:6379/0` on `openwebui-pr7_default` |
| `WEBUI_SECRET_KEY` | hash prefix `16128a9809590ca6` | hash prefix `217b9358bf111682` |
| Data mount | `/srv/openwebui-migration/data/openwebui` | `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/data/openwebui` |
| Automatic migrations | enabled | disabled |

The Redis URL strings match, but they resolve to different containers because the applications are on separate Docker networks.

## Schema compatibility

The PR7-only chain from production head `f3` to PR7 head `f8` is additive:

1. `d6e7f8a9b0c1`: creates Agent run/event/artifact/operation tables.
2. `e7f8a9b0c1d2`: creates the Agent decision-execution table.
3. `f8a9b0c1d2e3`: adds user-input deadline fields/index to `agent_run`.

It does not alter existing production chat/user/config tables. This makes an f3 application likely able to continue operating after the database is upgraded to f8, but that must be verified on a restored production snapshot before live migration.

## Why changing only `DATABASE_URL` is unsafe

- PR7 cannot use encrypted production valves/config with its different `WEBUI_SECRET_KEY`.
- Production files and local vector data live under a different bind mount; shared DB rows would reference content PR7 cannot access.
- Separate Redis instances mean cache invalidations, distributed locks, websocket state, and background ownership are not coordinated.
- Both versions would become simultaneous writers. “Dev testing” would create or change real production users, chats, files, functions, configuration, and Agent state.
- PR7 expects Agent tables at f8, while the current production DB is f3.

## Feasible operating models

### Recommended for ordinary development

Keep separate databases. Refresh `webui_dev` from a production backup on demand or on a schedule, and mount a snapshot/copy of production files. This permits destructive tests and schema experiments without production writes.

### Feasible for transition: shared-production canary

Both versions may share the production data plane only as a controlled blue-green/canary deployment:

- migrate the production DB once from f3 to f8 after backup and snapshot verification;
- keep `ENABLE_DB_MIGRATIONS=false` on PR7 and nominate one migration owner;
- use the production `WEBUI_SECRET_KEY` in PR7 without printing or committing it;
- attach PR7 to the production network and use the production PostgreSQL and Redis services;
- mount the same production data directory, or first move files/vector storage to a concurrency-safe shared service;
- retain a separate PR7 Agent runtime SQLite volume;
- restrict PR7 access to selected canary users and treat every write as production;
- verify background jobs, config/function cache invalidation, retrieval/vector concurrency, and rollback before opening traffic.

## Recommendation

Use the shared-production model only for a short migration canary. For a long-running dev environment, use a refreshed clone rather than a shared writable database.

## Production promotion readiness assessment

If the goal is to replace production with PR7, promote the existing production
data plane in place. Do not copy the isolated PR7 database or data directory
over production.

- Production image `sha256:7ec820b71f...` is based on `f8106c651`; that commit
  is an ancestor of target WebUI source `4a4e43e206...`. The target also
  contains the later OnlyOffice integration/fix commits.
- Target WebUI `sha256:fd6145b041f...` successfully imported against the real
  production database with migrations disabled and read all 40 users / 3350
  chats. Production is at `f3`; target migration head is `f8`.
- Production PostgreSQL is 24 GB and the OpenWebUI bind mount is 32 GB. The
  host has about 959 GB free, so a database restore rehearsal and at least one
  file snapshot fit locally. Bifrost owns another 43 GB but is not part of the
  WebUI image swap and should remain untouched except for a scoped config
  backup.
- Production currently uses four Uvicorn workers and about 8.1 GiB in the
  sampled container. The accepted PR7 configuration uses one WebUI worker and
  one runtime worker. A representative concurrency/load test is mandatory;
  do not carry four workers into PR7 until cross-worker config/function cache
  invalidation is implemented and reverified.
- Preserve the production `WEBUI_SECRET_KEY`, PostgreSQL, Redis, data mount,
  public `WEBUI_URL`, OAuth/login settings, Bifrost, and OnlyOffice services.
  Add only the Agent runtime service, its persistent SQLite volume, shared
  service token, budgets, and Agent enablement. Do not copy test-only
  OpenSearch/PaddleOCR/retrieval credentials from the isolated PR7 Compose.
- Run `f3 -> f8` as one explicit migration-owner job, then keep WebUI automatic
  migrations disabled. Start the runtime first and the WebUI second.
- Rollback has two boundaries: before reopening traffic, restore the f3
  database/files snapshot and old image; after writes resume, avoid snapshot
  restore and use either a forward fix or a separately rehearsed old-image-on-f8
  rollback with migrations disabled.
