# Bounded indexing repair

Approved: four Live Web processes plus two independent serial indexing containers.
Baseline: main 914f8a38320d; implementation branch codex/bounded-indexing-workers.

Required path: admission -> durable queue -> isolated execution -> bounded candidate
writes -> atomic publication. Preserve uploaded originals, existing vectors and the
previous published version. Do not replay historical pending files.

Implementation checkpoints:

- [x] Streaming upload/hash and progressive admission/spooling (10 focused tests, including killed-child scratch cleanup).
- [x] Durable queue, scoped deduplication, two slots, leases and terminal failures (isolated SQL).
- [x] Batch vector/evidence writes and atomic text/image publication (SQL and real API).
- [x] File/text/indexed-URL/batch/image-retry callers use the queue; SQL association preserves original legacy vector provenance without re-embedding.
- [x] Four-process configuration seeding/startup and real four-Web/two-indexer API regression.
- [x] Isolated memory/timeout/crash/storage/database fault acceptance.
- [ ] Build Slim image and deploy four Web + two indexing processes safely.

No production mutation yet. The two incident files must not be automatically retried.

Current focused acceptance: 10 admission/scratch tests, 12 actual SQLite
queue/publication/config/chunk-boundary cases, 2 production SSE/projection tests,
and 24 native-PDF/upload regression tests. SQL cases run in a separate interpreter
so their bootstrap stubs cannot contaminate other tests.

Real PostgreSQL/API fixture: normal KB ingestion/search, multi-collection search,
image publication across scopes with one encode, queued image retry, cancellation,
rejected numeric input and failed candidate retaining old content/search passed.
Final successful-edit/no-duplicate-embedding coverage is running after removal of
the two legacy update paths that fetched or deleted old vectors in Web.

Actual cgroup OOM killed only the indexing child (oom_kill +1); the parent, peer
indexer and Web survived. Timeout, full 512 MiB scratch filesystem and a 50-second
database pause produced terminal failure without automatic replay; old KB remained
readable after recovery. Each index container has 2 GiB/no swap, one CPU, no Docker
socket, read-only root and no capabilities. Upload-device I/O: read 20/write 10 MiB/s.

Memory growth: 300 chunks -> 279842816-byte child peak RSS; 4800 chunks ->
285200384 bytes. Sixteen times the chunks added 5357568 bytes (about 5.11 MiB).
Embedding batches never exceeded 32. This is synthetic external-encoder acceptance,
not a performance claim for every parser or production document.

Owned isolated source: aiserver:/home/aiserver/staging/ingestion-safety-20261024-jfBEav5x.
Only synthetic users/data; internal Docker network owui-idx-fixture-net at checked
10.253.240.0/28 (host default Docker pools exhausted). No Live config or volume is
mounted. Receipts/logs are in isolated-fixture/. The copied public source files
must be readable to cap-drop workers; private fixture credentials remain private.

Live baseline freshly verified: image v0123-intelligent-ui-914f8a38320d-20261022-slim,
one current Web process, existing compose restart overrides already unless-stopped.
Do not recreate DB/Redis/terminal from historical base compose snapshots. Preserve
the current external encoder, its verified vector space and hybrid-search=false.
Run additive f64b7a2810ce migration once before launch. Rollback uses the old image
with migrations disabled; no database downgrade or vector rebuild is required.
