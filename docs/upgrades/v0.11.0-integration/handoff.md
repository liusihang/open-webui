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
