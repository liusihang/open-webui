# PR7 live cutover plan — 2026-07-28

## Goal

Switch only the formal OpenWebUI service at `aiserver:/srv/openwebui-migration` to the exact PR7 candidate accepted in the isolated stack, run it with four Uvicorn workers, verify real runtime behavior, and retain an immediate rollback path.

## Fixed scope and truth surfaces

- Development truth: `/Users/liusihang/.codex/worktrees/d790/openwebui`
- Accepted isolated stack: `/home/aiserver/staging/openwebui-pr7-eea11194ed-test`, container `open-webui-pr7`
- Formal live stack: `/srv/openwebui-migration`, container/service `open-webui`
- Candidate image expected from prior acceptance: `open-webui:pr7-chat-agent-dual-mode-1d8dba8a7-slim`
- Do not restart or recreate unrelated live services.
- Never record credentials, tokens, cookies, or unredacted user content.

## Rollback gate

Before mutation, record the live compose checksum, container ID, image/tag/digest, health, restart count, worker count, database revision, database/Redis endpoints without credentials, and create a database backup plus a compose/config backup. Roll back immediately on unhealthy state, crash/respawn loop, migration failure, wrong image, fewer than four stable workers, authentication failure, or critical Agent/SSE regression.

## Phases

1. **Preflight and ancestry** — complete
   - Re-read release preparation and isolated acceptance artifacts.
   - Confirm local HEAD/status and candidate image identity.
   - Re-read live compose/environment and exact rollback scripts.
   - Correct the stale administrator profile template to the exact accepted latest-stack defaults and rerun its focused safety validation.
   - Repair and verify the missing formal AgentScope runtime topology before declaring preflight complete.
2. **Backup and immutable rollback point** — complete
   - Validate the prepared backup/rehearsal evidence is current enough.
   - Take fresh live DB and compose/config backups without exposing secrets.
   - Verify backup integrity before cutover.
3. **Controlled cutover** — complete
   - Change only the OpenWebUI service image/worker configuration.
   - Recreate only `open-webui` with no dependency restart.
4. **Runtime acceptance** — complete
   - Confirm exact image ID, health, restart count, four stable worker PIDs, and DB revision.
   - Verify authentication, Chat, Agent, SSE, and critical read paths against formal live.
   - Inspect only exact deployment-time log windows; no broad Bifrost log scans.
5. **Observation and decision** — complete
   - Observe health, workers, resource use, errors, and restarts for a bounded window.
   - Roll back on any gate failure; otherwise retain the new live release.
   - Per user direction, do not rebuild/recreate the running service for the pgvector issue. Prepare a tested single-file hotpatch and only use a verified graceful sequential worker reload; otherwise stop before signalling workers.
6. **Documentation and handoff** — complete
   - Record commands, evidence, backups, and final state.
   - Commit only verified handoff/script changes; do not push.

## Errors encountered

| Time | Error | Resolution |
|---|---|---|
| 10:12 | Read-only service inventory failed on a container without `.State.Health` | Switch heterogeneous container inventory to null-safe jq parsing and rerun from the beginning; no state changed |
| 11:08 | Runtime state directory under `/srv/openwebui-migration/data` could not be created by the login user | Diagnose exact path permissions; use an owner-only persistent prep-root state directory if confirmed; runtime was not created |
| 11:14 | Provider smoke reported marker false despite HTTP 200, 13 deltas and `[DONE]` | Probe incorrectly searched raw SSE frames; reconstruct delta text before marker check and rerun |
| 11:22 | Candidate acceptance rejected a formal admin ID different from the isolated hard-coded ID | Make the reusable acceptance script read `ADMIN_USER_ID` from a controlled environment variable; no request/profile mutation occurred |
| 11:25 | Pinned keep-alive socket closed on `/api/models?refresh=true` | Check exact worker PIDs/lifecycle window before changing probe; profiles saved, inference not started |
| 11:31 | Interaction probe returned ordinary Chat `task_ids` instead of `agent_run_id` | Old probe omitted explicit dual-mode binding; add failing request-body test, bind `chat_mode=agent` and current Agent revision, rerun; temporary tool was deleted |
| 11:36 | Concurrency p95 could be lower than p50 for two samples | Use nearest-rank `ceil(n*0.95)-1`, rerun short batch; requests themselves were 28/28 successful |
| 11:49 | Final observation counted two WebUI tracebacks with stable containers/PIDs | Sanitize timestamps and exception classes, correlate to probe windows, then decide retain/rollback |
| post-11:49 | A second pgvector OperationalError recurred | Treat as live retrieval defect; locate second timestamp/path and determine rollback versus tested image rebuild |

## Current decision

Formal live is accepted and retained on PR7 with four workers. The running container is hotpatched and must not be recreated from the current image; build an immutable image from commit `8a9395179` before the next planned restart/recreate.
