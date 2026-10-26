# Knowledge uploads: receive first, process asynchronously

File transfers and indexing have separate completion states. The knowledge UI
transfers at most three files concurrently and stops waiting as soon as each
stored original has a durable file ID and task ID. It snapshots the knowledge
and directory target for the batch. The existing multipart API remains usable
by other clients; synchronous processing remains an explicit option.

Uploaded documents enter `ingestion_job.status=waiting`. These rows contain
descriptors, not document bodies or vectors, and are independent of the
interactive queue's 20-per-user / 200-global admission limits. They do not
expire due to time spent waiting. Upload size and disk headroom checks remain
in force. Two fixed worker slots claim waiting/queued work in creation order,
with at most one running task per user. Running leases, execution timeouts and
explicit cancellation still apply. Automatic document-image children also
wait durably instead of failing because the execution queue is full.

The receipt is committed before the upload response. Closing the browser does
not cancel received work. Files not yet transferred still require the browser;
the UI reports received/total separately and warns before leaving. It does not
automatically repeat an ambiguous transfer. The knowledge pending endpoint
restores waiting, processing and unlinked failed uploads with specific errors,
without loading extracted bodies. Completed files appear in the normal list.

Targets are checked before durable storage and again before processing. Deleted
knowledge bases and revoked access produce explicit errors. Files already
stored are retained if subsequent processing fails.

Deploy Web and both indexers from the same release. No schema/vector migration
is required. Operational idle checks must include `waiting` as outstanding
index work; old queued-only counters are incomplete. The resource settings
(four Web processes / 8 GiB and two isolated indexers) are unchanged.

Regression entry points: `tests/ingestion/sql_cases.py` (SQLite and isolated
PostgreSQL), `tests/ingestion/upload_api_case.py` (150 real multipart requests
with no workers and a full interactive queue), `tests/migration/test_upload_target.py`,
`src/lib/utils/upload-batch.test.ts`, and `tests/frontend/knowledge-upload.test.ts`.
