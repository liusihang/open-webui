# Progress: PR7 live-shared database canary

## 2026-07-24 discovery

- **Status:** architecture discovery complete; no live changes made.
- Verified exact production and PR7 container, image, Compose root, network, database, Redis, secret-key fingerprint, migration, and storage surfaces.
- Production is at code/DB head `f3a4b5c6d7e8`; PR7 is at `f8a9b0c1d2e3`.
- Confirmed f3→f8 migrations add only Agent-mode tables and Agent-table fields.
- Confirmed the two deployments currently use different PostgreSQL databases, Docker networks/Redis containers, secret keys, and host data directories.
- Confirmed production data includes local `vector_db`, uploads, cache, and a legacy `webui.db` file under its bind mount; sharing the relational DB alone would not provide a coherent data plane.
- Decision pending from user: short shared-production canary versus isolated periodically refreshed dev database.

## Verification evidence

| Check | Result |
|---|---|
| Production health | healthy, zero restarts |
| PR7 health | healthy, zero restarts |
| Production DB/code head | `f3a4b5c6d7e8` |
| PR7 DB/code head | `f8a9b0c1d2e3` |
| PostgreSQL image compatibility | both use the same pgvector PostgreSQL 16 image |
| Secret keys | different fingerprints |
| Data mounts | different host paths |
| Redis | same URL text, different network-local containers |

## 2026-07-24 production-promotion readiness check

- **Status:** preparation assessment complete; production remains unchanged.
- Verified production Compose root `/srv/openwebui-migration`, port 80, image
  `sha256:7ec820b71f...`, database head `f3a4b5c6d7e8`, four WebUI workers,
  production PostgreSQL/Redis/Bifrost/OnlyOffice membership, and exact mounts.
- Verified target WebUI `sha256:fd6145b041f...` and Agent runtime
  `sha256:f7396ba23e...` remain healthy in the isolated stack with zero restarts.
- Target-image read probe against the live production database passed without
  migrations. The `f3 -> d6 -> e7 -> f8` chain is additive and creates only
  Agent-mode state plus Agent user-input deadline fields.
- Production scale snapshot: 24 GB PostgreSQL, 32 GB OpenWebUI data mount, 40
  users, 3350 chats, 8502 files, 69 knowledge bases, 13 functions, and 8 tools.
  No retrieval job was currently running; historical states were succeeded or
  failed only.
- Production has about 959 GB free disk. A full restore rehearsal, load test,
  rollback drill, final write freeze, and explicit migration-owner procedure
  remain required before cutover.

## 2026-07-24 multi-worker correction

- Corrected the earlier wording that cross-worker invalidation was not fixed.
  Target source `4a4e43e206...` already contains Redis/versioned invalidation
  for config, function, model, and tool caches plus startup singleton
  coordination. The deployed PR7 image contains matching file hashes.
- Fresh focused verification passed: `10 passed, 1 warning` across
  `test_cache_invalidation.py` and `test_startup_singleton.py`.
- The remaining gap is deployment acceptance, not implementation: the isolated
  PR7 WebUI is still configured with `UVICORN_WORKERS=1`, so no real four-worker
  routing/invalidation/load test has been run on the currently deployed image.
