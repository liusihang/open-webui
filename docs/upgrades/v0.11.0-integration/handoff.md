# Integration handoff

## Current state

- Common integration branch: `codex/v011-upstream-integration-base`.
- Common baseline: `1f93cd9a3b6d8db26f5abbccfd784052ab6e0b9d`.
- Custom first parent: `665221e1910a11cfd20e034d9967c93f5d4025d2`.
- Official donor: `f9590b8017199e56d5e953657e6498e3cef1d246` (`v0.11.0`).
- All four lane commits are merged in dependency order A -> B -> C -> D.
- The only cross-lane textual conflict was `backend/open_webui/utils/timers.py`; it was resolved with a timer-local parent lock so timers no longer depend on the excluded official Sub-agents module.
- Live service and live database remain untouched.

## Thread table

| Lane | Thread | Branch/SHA | Status | Handoff |
|---|---|---|---|---|
| A | `019fa7d9-5757-76b1-a1ee-99d13be838f1` | `codex/v011-integration-lane-a` / `75de63678d2c047b75c209a6b4c0ed971431cd73` | merged | `handoff-lane-a.md` |
| B | `019fa7d9-5758-7cb0-97ca-6a4d6ec436db` | `codex/v011-integration-lane-b` / `b1bbb5ef9fb0349fbb79f30b91b008ba1b5e7d90` | merged | `handoff-lane-b.md` |
| C | `019fa7d9-5758-7cb0-97ca-6a297bd1d218` | `codex/v011-integration-lane-c` / `f30bc600eccf30743eae30ba9f506ed726a8fdd8` | merged | `handoff-lane-c.md` |
| D | `019fa7d9-5758-7cb0-97ca-6a08de923742` | `codex/v011-integration-lane-d` / `875c1f6318a94a92d113d118e8bf6077ac911669` | merged | `handoff-lane-d.md` |

## Combined integration repairs

- Delayed v0.11 chat-variable fallback until the saved chat has passed the custom ownership/profile read path; legacy calls use an owner-gated fallback.
- Initialized the mode-profile revision before metadata processing so early provider/config errors cannot be masked by `UnboundLocalError`.
- Restored v0.11 per-model task-ID preallocation and made internal/external fanout share one fully parameterized `process_chat` coroutine.
- Replaced the normalized-email duplicate preflight's PostgreSQL-only quoted SQL with a SQLAlchemy Core query that explicitly quotes the reserved `user` table for PostgreSQL, SQLite, MySQL, and MariaDB.
- Removed the last official Sub-agents admin-tab icon branch and added a frontend source guard so it cannot return without failing tests.
- Updated custom native-tool tests from the removed v0.10.2 filter lookup to v0.11 `get_filter_functions` and retained tool-round filter-context assertions.
- Updated knowledge, image-asset, multimodal-config, and image-generation tests to the v0.11 request/event, safe-relative-path, runtime-config, and UI-session contracts.
- Added targeted regressions proving authorization precedes chat-variable reads, early model errors are not masked, per-model task IDs are non-empty/unique and propagated unchanged, and internal/external fanout uses the same per-model prompt/parameter path.

## Combined verification

- Exact custom parent, official v0.11 tag, and all four lane commits are ancestors of the integration head.
- Excluded production symbols are absent: `delegate_task`, `list_chat_files`, `grep_chat_files`, `query_chat_files`, `/configs/subagents`, `ENABLE_SUBAGENTS`, `open_webui.utils.subagents`, and `process_pending_internal_messages`. Remaining matches are negative test assertions only.
- Deleted official duplicate surfaces remain untracked: `backend/open_webui/utils/subagents.py`, `Settings/Subagents.svelte`, and `SubagentResultRow.svelte`.
- Fresh isolated SQLite `alembic upgrade head` succeeds and `alembic heads` reports only `a11c0d3f0bd0`.
- Normalized-email migration suite: `9 passed`, including dialect compilation checks for PostgreSQL, SQLite, MySQL, and MariaDB table quoting.
- Chat-entry mode-profile suite: `49 passed`, including both internal and external multi-model fanout.
- Cross-lane focused backend matrix: `242 passed`.
- Full backend suite on the migrated isolated database: `1229 passed`, `72 warnings`.
- Full frontend suite: `35/35` files and `385/385` tests passed.
- Focused frontend exclusion guard after final formatting: `24 passed`.
- Production frontend build: exit 0; Pyodide cache validated/reused; 6408 modules transformed; adapter-static wrote `build`.
- Changed-file `py_compile`, Ruff `F821`, Prettier, and `git diff --check` pass.
- Read-only integration review reported no critical findings and three important findings; all three are repaired and covered by the migration, chat-entry, and frontend guards above.
- Test-harness note: running the full backend suite against a newly created but unmigrated database produces four `no such table: config` setup failures. The same four tests and the full suite pass after `alembic upgrade head`; do not treat an unmigrated test database as a product regression.

## Residual deployment gates

- No production clone or live database was used. Run Lane A's normalized-email duplicate preflight and migration rehearsal on an authorized clone before deployment.
- Validate the functional email index on the deployment's actual MariaDB/MySQL/PostgreSQL engine; MariaDB may require the documented generated-column alternative.
- Run authenticated browser acceptance and isolated multi-worker runtime acceptance on a disposable integration environment before any separately authorized live cutover.
- `npm ci` reports 23 findings in the locked dependency tree (2 low, 11 moderate, 9 high, 1 critical); they are inherited from the integrated lock and were not auto-fixed because that would be a separate dependency change.
- Existing repository-wide Svelte strict-type/a11y warnings remain; the production build and full frontend tests pass, but this integration does not claim a warning-free `svelte-check` baseline.

## Independent review follow-up (2026-07-28)

### Repair tasks

| Repair | Thread | Worktree | Scope | Status |
|---|---|---|---|---|
| A | `019fa8e1-7325-7230-a1d6-5925284c7f5c` | `/Users/liusihang/.codex/worktrees/fc9a/openwebui` | Restore v0.11 HTTP orjson activation and Chat/ChatMessage Alembic metadata imports | merged as `8b30479f2` |
| B | `019fa8e1-7328-7d60-ae12-156dd036a773` | `/Users/liusihang/.codex/worktrees/9d34/openwebui` | Enforce Agent run owner/admin reads and missing-run 404 contracts | merged as `e70cb6a1f` |
| C | `019fa8e1-7326-7250-847e-b82616a7e495` | `/Users/liusihang/.codex/worktrees/2df6/openwebui` | Remove excluded official Sub-agents locale residue and strengthen the guard | merged as `93f0d831f` |

- All repair tasks start from integration HEAD `51ac3be552df87c9a87bd3f647905a47b4588ee1` and own disjoint files.
- Each task must use test-first RED/GREEN evidence, commit its changes, and maintain a repair-specific handoff.
- The root checkout remains outside the repair truth surface.
- All three repair commits are now integrated. Their recorded RED/GREEN evidence is preserved in `handoff-review-fix-a.md`, `handoff-review-fix-b.md`, and `handoff-review-fix-c.md`.

### Isolated test-stack preflight

- Scope: only `aiserver:/home/aiserver/staging/openwebui-pr7-eea11194ed-test`, service `open-webui-pr7`; formal `open-webui` remains read-only.
- Preflight time: `2026-07-28T21:20:18+08:00`.
- Test rollback container/image:
  - container ID `715d9301220d94b8e4bb1d58a01b67c17358fca7d7bb1ad2465885b2b22af714`
  - tag `open-webui:pr7-chat-agent-dual-mode-1d8dba8a7-slim`
  - image ID `sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b`
  - `running`, `healthy`, restart count `0`; `/health` and `/health/db` both returned true.
- Formal live anchor: container `open-webui`, the same image ID, `running`, `healthy`, restart count `0`. It must remain unchanged before and after the isolated cutover.
- AgentScope runtime anchor: `openwebui-pr7-agentscope-runtime`, image ID `sha256:f7396ba23e49f934216ba8fc4b38c695b7f639722d852b44234769c66ca7f6e9`, `running`, `healthy`, restart count `0`.
- The test WebUI was created from this exact Compose layer order; the base file alone is not the runtime truth:
  1. `compose.yaml`
  2. `compose.webui-rebuild-eaff69b0d317.yaml`
  3. `compose.webui-eaff69-no-migrations.yaml`
  4. `compose.webui-4a4e43e206.yaml`
  5. `compose.agent-runtime-742f686182.yaml`
  6. `compose.latest-candidate.yaml`
- Deployment will preserve the first five layers, add a new v0.11 WebUI-only override, and recreate only `open-webui-pr7` with `--no-deps --force-recreate`.

### Planned rebuild/deployment parameters

- Source ref: pending merged and verified repair commit on `codex/v011-upstream-integration-base`.
- Build input: clean `git archive` of that exact commit.
- Build host: `aiserver`.
- Image tag: pending `open-webui:v011-test-<short-sha>`.
- Profile: external-services slim, matching the isolated stack's current WebUI profile.
- Proxy/mirror: Clash `http://192.168.2.201:7897` and the already validated domestic base-image mirrors when required.
- Acceptance: image inspect, Alembic/boot completion, container healthy with restart count zero, `/health`, `/health/db`, `/api/version`, four-worker process evidence, AgentScope runtime preservation, focused feature probes, and formal-live before/after identity.
- Rollback: recreate only `open-webui-pr7` with the recorded previous image and Compose layer set if health, migration, logs, or probes fail.

### Migration preflight checkpoint

- The isolated PostgreSQL database is `webui_pr7`, currently at Alembic revision `c0d3b4a5e6f7`, size `421 MB`.
- Data anchors before deployment: `334` chats, `455` Agent runs, and `4011` Agent run events.
- Normalized-email duplicate preflight returned zero rows, so the v0.11 unique lower-email index is not blocked by current test data.
- The five official target columns and three target indexes checked by the v0.11 migration branch are not yet present, which is consistent with the current custom-only revision.
- The integrated migration graph has one merge head `a11c0d3f0bd0`, merging current custom head `c0d3b4a5e6f7` with official branch head `f0bd01a18a3d`.
- Before touching the test database, create and checksum a PostgreSQL custom-format backup, restore it into a disposable rehearsal database, and run the candidate image's Alembic upgrade there first.

### Build preflight checkpoint

- Remote builder `codex-pr7-slim-cache` is running BuildKit `v0.31.2` with the `docker-container` driver.
- The validated local BuildKit cache root is `/home/aiserver/.cache/openwebui-pr7-slim-buildx` (`13 GB`) with a current cache pointer available.
- Docker Hub through the local Clash proxy failed its TLS probe, so the build must not depend on direct Docker Hub metadata access.
- The following Daocloud mirror manifests resolved successfully and will be used only in the remote staged Dockerfile:
  - Dockerfile frontend: `sha256:87999aa3d42bdc6bea60565083ee17e86d1f3339802f543c0d03998580f9cb89`
  - Node 22 Alpine: `sha256:2289fb1fba0f4633b08ec47b94a89c7e20b829fc5679f9b7b298eaa2f1ed8b7e`
  - Python 3.11 slim Bookworm: `sha256:b18992999dbe963a45a8a4da40ac2b1975be1a776d939d098c647482bcad5cba`
- The integration worktree has a `58 MB` generated `static/pyodide` cache matching the current lock; it may be overlaid onto the clean archive as a validated dependency seed, while the committed source remains the image revision authority.

### Post-merge verification checkpoint

- The three repair commits and their handoffs are present together on the integration branch.
- Focused Agent run read/event/main-route slice: `103 passed`, `35 deselected`, `12 warnings`.
- Startup/orjson/Alembic/dependency/migration-graph contracts: `8 passed`, `1 warning`.
- A fresh SQLite database upgraded from empty to `a11c0d3f0bd0`; `alembic current` and `alembic heads` both report the same single merge head.
- Full backend suite on that migrated isolated database: `1256 passed`, `15 warnings` in `29.79s`.
- Full frontend suite with Node `v22.22.0`: `35/35` files and `386/386` tests passed.
- Production frontend build with Node `v22.22.0` and an 8 GB heap: Pyodide `314.0.3` cache reused, `6408` modules transformed, adapter-static wrote `build`, and the build completed in `1m11s`.
- Changed Python files compile; Ruff `F821`, Prettier for the locale/test scope, and `git diff --check` pass. Prettier emits only the repository's existing `pluginSearchDirs` deprecation warning.
- The source worktree remained clean after the full backend suite and production frontend build.
