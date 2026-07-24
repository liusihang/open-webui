# PR7 live-shared database canary plan

## Goal

Keep the current production OpenWebUI and PR7 Agent build online simultaneously while deciding whether PR7 should consume production data as a controlled canary rather than an independent dev database.

## Current Phase

Phase 2 — architecture decision; no live mutation authorized yet.

## Phases

### Phase 1: Discover exact live topology

- [x] Compare container/image/network identities.
- [x] Compare PostgreSQL and Alembic heads.
- [x] Compare Redis, `WEBUI_SECRET_KEY`, and data mounts without exposing secrets.
- [x] Inspect f3→f8 migration scope.
- **Status:** complete

### Phase 2: Choose operating model

- [ ] Choose shared-production canary or isolated refreshed dev database.
- [ ] Decide whether PR7 may write production data or must initially be read-only.
- [ ] Define traffic/users allowed to access the canary.
- [x] For final promotion, recommend upgrading the existing production data plane in place rather than promoting the isolated PR7 database/files.
- [ ] Obtain user authorization for a restored-snapshot rehearsal and maintenance-window cutover.
- **Status:** in_progress

### Phase 3: Prepare rollback-safe configuration

- [ ] Back up the production database and data directory.
- [ ] Restore the backup into a rehearsal database/data snapshot and verify counts/checksums.
- [ ] Create a single migration-owner procedure for f3→f8.
- [ ] Preserve the production secret, Redis, file mount, Bifrost, OnlyOffice, URL, and authentication settings; add only Agent/runtime settings.
- [ ] Add the AgentScope runtime service and persistent runtime-state volume to the production Compose network.
- [ ] Keep WebUI/runtime workers at one for the first release; separately run a four-WebUI-worker cache invalidation, singleton, SSE, and load acceptance before restoring production worker parity.
- [ ] Align PR7 database, Redis, secret key, and file storage with production if canary mode is selected.
- [ ] Keep PR7 Agent runtime state separate.
- [ ] Disable PR7 automatic migrations and prevent duplicate background ownership.
- **Status:** pending

### Phase 4: Staged verification

- [ ] Run schema compatibility probe against a restored production snapshot.
- [ ] Apply f3→f8 to the restored snapshot and measure migration/startup time.
- [ ] Verify login, encrypted valves, files, retrieval/vector data, chats, and Agent runs.
- [ ] Verify OnlyOffice preview/edit/saveback, Bifrost streaming, knowledge retrieval, web search, code execution, and OAuth/login flows.
- [ ] Start both versions and verify cross-instance cache invalidation.
- [ ] Prove old-image-on-f8 compatibility with migrations disabled or declare snapshot restore as the only rollback before reopening traffic.
- [ ] Execute rollback drill before routing users.
- **Status:** pending

### Phase 5: Canary transition

- [ ] Route only selected users/traffic to PR7.
- [ ] Monitor errors, DB locks, cache/config consistency, jobs, and Agent runs.
- [ ] Promote PR7 or return all traffic to production without DB downgrade.
- **Status:** pending

## Key Questions

1. Does “dev” need to write the real production chats/files, or only display a recent copy?
2. Is the intended outcome a short blue-green migration or a long-running experimental environment?
3. Which users are allowed to generate production writes through PR7?

## Decisions Made

| Decision | Rationale |
|---|---|
| Do not directly repoint PR7 during discovery | Current schema, secret, storage, and cache surfaces differ. |
| Treat a shared database deployment as a production canary, not a normal dev sandbox | Every PR7 write would immediately become production data. |
| Keep Agent runtime SQLite state separate | Runtime continuations belong to the PR7 orchestrator and are not part of the shared OpenWebUI relational data plane. |

## Errors Encountered

| Error | Attempt | Resolution |
|---|---:|---|
| Deep data-directory size inventory did not complete within the command window | 1 | Used exact top-level directory evidence; the presence of local `vector_db`, uploads, and cache is sufficient for the architecture decision. |
