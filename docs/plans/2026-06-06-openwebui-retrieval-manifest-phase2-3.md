# OpenWebUI Retrieval Manifest Phase 2/3

## Goal
Continue the chunk-manifest-centered RAG architecture after Phase 1:

- `retrieval_chunk` is the SQL source of truth.
- pgvector `document_chunk` is a derived embedding index.
- OpenSearch is a derived lexical/BM25 index.
- Derived indexes are rebuilt and deleted through durable jobs, not assumed to share a transaction with SQL manifest writes.

## Phase 2 Boundary
In scope:

- Add `retrieval_index_job` as an outbox/job table.
- Add `retrieval_index_state` for explainable index status.
- Wrap existing lexical/full admin reindex in job creation.
- Add an async queue mode for long reindex operations.
- Add a worker hook endpoint to execute a queued job.
- Preserve the old synchronous API response shape by default.

Out of scope:

- Fully moving upload/chunk/embedding work into a background worker.
- Removing the legacy vector-store write path.
- Rebuilding embeddings for `/reindex/full`.

## Phase 3 Boundary
In scope for the first Phase 3 slice:

- Make manifest deactivation fail closed when no scope selector is provided.
- Fetch affected `chunk_uid`s before deactivation.
- Deactivate manifest rows and enqueue OpenSearch physical delete jobs in one SQL transaction.
- Execute OpenSearch delete jobs inline by default after the transaction commits, while keeping the job record retryable.
- Explicitly deactivate all manifest rows for admin vector DB reset, without exposing selector-less generic deactivation.
- Persist chunker and embedding signatures in vector metadata for new writes.

Still deferred:

- A request-free ingestion worker that owns chunking and embedding.
- Automatic embedding rebuild when embedding model/dimension changes.
- Analyzer/mapping signature comparison and automatic lexical blue-green rebuild.
- Old OpenSearch concrete-index retirement policy.
- Rechunk endpoint and chunker-config migration workflow.

## Tables
`retrieval_index_job`:

- `job_id`: job primary key.
- `index_kind`: `embedding`, `lexical`, `full`, `delete`, or `rechunk`.
- `collection_id`, `knowledge_id`, `collection_name`, `file_id`: nullable scope fields.
- `chunker_config_hash`, `target_config_hash`: target signatures.
- `status`: `pending`, `running`, `succeeded`, `failed`, or `cancelled`.
- `payload`, `result`, `error`.
- `retry_count`, `max_retries`.
- `created_at`, `started_at`, `finished_at`, `updated_at`.

`retrieval_index_state`:

- deterministic `state_id` from index kind, scope, chunker hash, and target config hash.
- same scope/signature fields as the job table.
- `status`: `pending`, `indexing`, `ready`, `stale`, `failed`, or `deleted`.
- chunk counts, `last_job_id`, `error`, timestamps.

## API Surface
Existing admin endpoints remain:

- `POST /api/v1/knowledge/reindex/lexical`
- `POST /api/v1/knowledge/reindex/full`
- `GET /api/v1/knowledge/index/status`

`KnowledgeReindexRequest` adds:

- `run_async: bool = false`

Behavior:

- `run_async=false`: create a job, run it immediately, and return the prior result shape.
- `run_async=true`: create a pending job and return `{ queued, job, state }`.

New worker/status endpoints:

- `GET /api/v1/knowledge/index/jobs/{job_id}`
- `POST /api/v1/knowledge/index/jobs/{job_id}/run`

Delete/reset lifecycle helpers run their delete jobs inline by default. The run endpoint is still the minimal worker hook for async reindex jobs, failed delete retries, external schedulers, or admin scripts.

## Write Metadata Contract
New vector writes record:

- `chunk_index`
- `chunk_version`
- `chunker_config`
- `chunker_config_hash`
- `embedding_config`
- `embedding_config_hash`

This makes future chunker and embedding changes detectable without guessing from legacy vector metadata.

## TODO
- [x] Add job/state model and migration.
- [x] Add deterministic target config/state ID helpers.
- [x] Add fail-closed manifest scope helpers.
- [x] Wrap lexical/full reindex in jobs.
- [x] Add async reindex queue mode and job run hook.
- [x] Enqueue lexical delete jobs in the same SQL transaction as manifest deactivation.
- [x] Execute lexical delete jobs inline by default for delete/reset lifecycles.
- [x] Use the latest ready lexical target hash for delete jobs instead of assuming v1.
- [x] Add explicit admin reset-all manifest deactivation plus lexical delete job enqueue.
- [x] Persist chunker/embedding signatures for new writes.
- [ ] Add a real background worker loop.
- [ ] Extract request-free ingestion worker.
- [ ] Implement embedding rebuild jobs.
- [ ] Implement analyzer/mapping signature comparison and old-index retirement.
- [ ] Add real OpenSearch integration smoke tests.
