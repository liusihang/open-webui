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

