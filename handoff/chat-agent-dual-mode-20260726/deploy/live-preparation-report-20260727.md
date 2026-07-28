# PR7 Chat/Agent dual-mode live preparation report — 2026-07-27

## Scope

Preparation only. Formal `open-webui` was not rebuilt, recreated, container-restarted, stopped, reconfigured, or migrated. The only formal-data operation was an online consistent `pg_dump`; all restore and migration work targeted disposable infrastructure. However, the same-host disposable restore saturated shared storage and coincided with formal Uvicorn worker deaths/respawns, so this preparation was not operationally impact-free.

Truth surfaces:

- Formal stack: `aiserver:/srv/openwebui-migration`
- Preparation workspace: `aiserver:/home/aiserver/staging/pr7-live-prep-20260727`
- Local release artifacts: `handoff/chat-agent-dual-mode-20260726/deploy/`

## Formal live anchor

- Container ID: `78faa81d479d8c5ef33a85277feeb3dc5de68861c3f25dcaac67285935f9c13e`
- Image ID: `sha256:7ec820b71fa94205b273cb8cd00344a130e1921ae8e643ba6192b0e58933bd45`
- Image: `open-webui:live-f8106c651-to-v0102-pr7-b3-7-onlyoffice-mergefix-slim-20260707013738`
- Health: healthy
- Restart count: 0
- Workers: 4
- Started: `2026-07-07T03:53:51.178582025Z`
- Compose SHA-256: `7fff73a9037687460bd6c27669e9224203241546928173106c9999d6b3425da1`
- Database revision: `f3a4b5c6d7e8`

The master/container anchor stayed unchanged, but worker PIDs changed during preparation. The before workers were `846975`, `877132`, `1130032`, and `1178310`; the final workers are recorded in the after-anchor section.

## Candidate anchor

- Image: `open-webui:pr7-chat-agent-dual-mode-1d8dba8a7-slim`
- Image ID: `sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b`
- Source revision label: `1d8dba8a77e6e8adc5952891bac83a2a7c5a4804`
- Target DB revision: `c0d3b4a5e6f7`

## Verified formal backup

- Manifest: `/home/aiserver/staging/pr7-live-prep-20260727/backups/20260727-live-prep-2/manifest.env`
- Dump: `/home/aiserver/staging/pr7-live-prep-20260727/backups/20260727-live-prep-2/openwebui-live-20260727-live-prep-2.dump`
- Size: `8,039,872,549` bytes (about 7.5 GiB)
- SHA-256: `6d7c5ef1153ff87c3c2d1597860ad9d04f5f3f93f2db7d4746f2df71cd6adb24`
- `pg_restore --list`: passed, 351 entries
- Source revision: `f3a4b5c6d7e8`
- Duration: 1,565 seconds
- Permissions: owner-only dump (`0600`)
- Live container anchor before/after: identical

This backup proves the mechanism and supplies the migration rehearsal. A new backup is still mandatory immediately before a later authorized cutover; the migration guard rejects an archive older than one hour by default.

## Production-shaped migration rehearsal

Status: **PASS** (`20260727-formal-clone-2`).

- Report: `/home/aiserver/staging/pr7-live-prep-20260727/rehearsals/20260727-formal-clone-2/report.env`
- Restore reached `f3a4b5c6d7e8` in 4,610 seconds.
- Candidate upgrade reached `c0d3b4a5e6f7` in 120 seconds through the complete `f3 -> d6 -> e7 -> f8 -> c0` chain.
- Upgrade invariants: `2:2:1:8:1:2:1` for heads/revisions/Chat binding/all new tables/short constraint/deadline columns/deadline index.
- Chat identity signature remained `3382:292770260021304048698`.
- Candidate downgrade returned to `f3a4b5c6d7e8` in 69 seconds; new tables and Chat binding column were absent (`0:0`).
- Before/after schema dumps were byte-identical, SHA-256 `48932a60dd442a7e5849ab8ef082f16dbf82a33b43108663df81f4d6e17346f5`.
- Old image reported `f3a4b5c6d7e8` as both head and current after downgrade.
- Disposable container, internal network, Docker volume, and secret env files were removed. Only owner-only status/report/log/schema evidence remains.

The 77-minute restore is disaster-recovery RTO evidence, not cutover downtime. The measured forward migration itself was two minutes.

## Formal worker-respawn incident

Exact timestamped WebUI lifecycle logs show:

- child deaths from `2026-07-27T13:24:21Z` through `13:26:00Z` (21:24–21:26 local);
- replacement processes did not complete startup until about 21:31–21:32 local;
- one additional replacement child died at 21:31:34 before a stable process started at 21:32:20;
- container `OOMKilled=false`, restart count 0, no Docker event, and no kernel OOM/segfault signal.

This period overlaps the heaviest disposable restore I/O. The most likely explanation is Uvicorn worker-healthcheck expiry under prolonged shared-storage stalls, but the old image logs do not record the child exit signal, so this remains an evidence-backed inference rather than a proven signal cause.

Operational decision: never run the full restore rehearsal on the formal live host/storage again. Use a separate host/storage snapshot or explicit I/O isolation. The normal live cutover does not restore the dump; it performs the measured two-minute Alembic migration only.

After the disposable restore and clone cleanup, the observation window from 22:34:05 through 22:52:00 local recorded no additional worker lifecycle line, container lifecycle/OOM event, or kernel OOM/segfault signal. Final workers are `1007971`, `1011557`, `1011836`, and `1034666`; the container is healthy with restart count zero and remains on the old image/`f3` schema.

## Staged administrator defaults

The proposal is deliberately minimal and remains editable before save:

| Mode | Terminal | Tools | Skills | Filters/features | System Prompt |
| --- | --- | --- | --- | --- | --- |
| Chat | explicit disabled | explicit empty | explicit empty | inherit | empty |
| Agent | `terminals` | `web_search_and_crawl`, `sub_agent` | `get-available-resources` | inherit | empty |

DB-backed validation proved each selected resource exists and is active. The template contains no model or Reasoning Depth field. The guarded profile-apply helper reads current heads immediately before save and never emits prompts or bearer tokens.

## Prepared control package

- Exact candidate/four-worker/migrations-disabled Compose override
- Online backup plus status/checksum/list verification
- Detached long-task launcher with file status/log supervision
- Disposable formal-backup migration rehearsal
- Explicit maintenance stop of only `open-webui`
- Guarded forward migration and WebUI-only deploy
- Schema-aware guarded rollback to old image
- Read-only runtime/image/revision/four-worker verification
- Sanitized DB/API resource inventory and profile-template validation
- Guarded administrator profile application and repeated convergence reads
- Full forward/smoke/observation/rollback runbook

Safety tests prove every mutating helper rejects missing confirmation and the merged Compose configuration resolves to the expected candidate with four workers and migrations disabled. The immutable formal container/image/config/DB anchors remained unchanged, but formal worker processes respawned during shared-storage pressure as documented above.

## Cutover remains separately gated

No formal upgrade is authorized by this preparation. A later cutover still requires:

1. explicit user authorization and maintenance window;
2. a new under-one-hour backup with checksum/list success;
3. a real selected-model provider response immediately before cutover;
4. real Chat plus native Agent/SSE browser/API smoke after deploy;
5. pinned-four-worker profile convergence and startup-singleton evidence;
6. rollback before general traffic if any migration, health, worker, cache, inference, or SSE gate fails.

## Preparation decision

The migration/release package is complete and technically ready for a separately authorized controlled cutover. The preparation also exposed a real operational flaw in the rehearsal method: same-host full restore can destabilize current workers. Therefore readiness is conditional on never repeating that restore on formal live storage; only the fresh dump and measured two-minute forward migration belong in the cutover window.

The prepared smoke model is `gpt-5.5`; it passed isolated real inference but must be revalidated against the formal provider route immediately before cutover. Model selection remains outside Chat/Agent profile defaults.

## Cutover payload supersession — 2026-07-28

The historical staged-default table above records the 2026-07-27 preparation state. Before the authorized cutover, latest-image acceptance proved that `web_search_and_crawl` is not reproducible because the immutable candidate lacks `crawl4ai`; the isolated database also lacked `get-available-resources`. The reviewed live payload is therefore superseded by the exact accepted defaults: Chat empty capabilities, Agent Terminal `terminals` plus tool `sub_agent`, no Skills, empty prompts, and inherited filter/feature defaults.

The 2026-07-28 read-only live preflight also proved that formal live had no AgentScope runtime and that the six-line candidate override supplied no runtime URL/token. The controlled package was corrected before cutover to create a dedicated `openwebui-agentscope-runtime` on the formal network with an owner-only token env and persistent state. The isolated runtime remains isolated and is never reused by live.

The first guarded runtime-prepare attempt then proved `/srv/openwebui-migration/data` is root-owned and not writable by the Docker-operator account. It stopped before token generation or container creation. The reviewed default state path was moved to owner-only persistent storage at `/home/aiserver/staging/pr7-live-prep-20260727/runtime-state`; no sudo, chmod, or chown was applied to `/srv`.
