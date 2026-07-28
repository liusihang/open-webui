# Lane A handoff: security, auth, config, dependencies, and Alembic

## Truth surface and boundaries

- Worktree: `/Users/liusihang/.codex/worktrees/00a3/openwebui`
- Branch: `codex/v011-integration-lane-a`
- Starting commit: `1f93cd9a3b6d8db26f5abbccfd784052ab6e0b9d`
- Starting parents: custom `665221e1910a11cfd20e034d9967c93f5d4025d2`, official v0.11.0 `f9590b8017199e56d5e953657e6498e3cef1d246`
- Shared comparison base: official v0.10.2 `ecd48e2f718220a6400ecf49eafd4867a38feb10`
- No live service, container, remote host, or live database operations are authorized.
- Ownership is limited to Lane A paths from the delegation. Cross-lane needs are recorded, not edited.

## Required exclusions

- No official Sub-agents runtime, `delegate_task`, subagent config key, or subagent config endpoint.
- No stock Agent renderer or protocol changes.
- No `list_chat_files`, `grep_chat_files`, or `query_chat_files` surface.

## Checkpoints

### CP0 - Baseline and instructions

- Status: complete
- Goal: prove the exact worktree, branch base, parent order, cleanliness, integration policy, and ownership.
- Evidence: clean detached worktree at `1f93cd9`; base branch points to the same commit; switched to `codex/v011-integration-lane-a`; read `README.md`, `interfaces.md`, and `TODO.md`.

### CP1 - Owned-path upstream audit and RED tests

- Status: complete
- Goal: enumerate every official v0.10.2..v0.11.0 change in owned paths, classify baseline state as present/missing/adapted/excluded, and add focused failing tests for missing behavior before production edits.
- Evidence: exact `git diff ecd48e2..f9590b8` audit found 34 official-changed Lane A files. Non-conflict manifests, locks, env, database, OAuth/security middleware, owned auth/user/access models, and official migrations were already present. RED run produced 6 expected failures: non-persistent config seeding, missing `IntegrityError` import, missing group placeholder substitution, official subagent config exposure, two Alembic heads, and stale external-slim dependency pins.

### CP2 - Security/auth/config implementation

- Status: complete
- Goal: integrate official non-subagent behavior while preserving custom authentication and AgentScope authority.
- Evidence: retained atomic multi-worker config seeding while filtering non-persistent keys; restored trusted-header race handling import; completed lazy group-header substitution; removed only official `subagents.*` variables/defaults and `/configs/subagents` endpoints. Custom `agent.team.max_subagents` and `agent.subagent.default_budget` remain.

### CP3 - Dependency and lock reconciliation

- Status: complete
- Goal: reconcile Python, Node, Docker, and example-env dependency/security deltas, including official removals, with reproducible lock/manifests.
- Evidence: official `.env.example`, Docker arbitrary-UID static permission block, `pyproject.toml`, `uv.lock`, `backend/requirements.txt`, `backend/requirements-min.txt`, `package.json`, and `package-lock.json` were already present/equivalent. Updated the custom external-services slim manifest for v0.11 multipart, uvicorn, orjson, joserfc, Redis/hiredis, aiodns, regex, lxml and related auth foundation pins; removed `python-jose`; kept local OCR excluded while the full profile uses `rapidocr==3.9.2`; Pyodide remains `314.0.3` in package and lock.

### CP4 - Alembic reconciliation and model drift audit

- Status: complete
- Goal: merge official head `f0bd01a18a3d` and custom head `f8a9b0c1d2e3` into one explicit head; document normalized-email duplicate preflight; verify upgrade/downgrade structure and owned model/schema agreement without touching live data.
- Evidence: added no-op merge revision `a11c0d3f0bd0` over actual current heads `c0d3b4a5e6f7` and `f0bd01a18a3d`. `c0d3b4a5e6f7` is the base branch's post-`f8a9b0c1d2e3` custom descendant, so joining it preserves the full custom lineage. Alembic reports exactly `a11c0d3f0bd0 (head)`. A fresh isolated SQLite database upgraded through the complete graph to that head. The normalized-email index preflight returned zero duplicates; index count was 1 after upgrade, 0 after downgrade to `959eaac8f909`, and 1 after re-upgrade.
- During rollback verification, official `f0bd01a18a3d` was found to use inspector reflection that skips SQLite expression indexes, making downgrade/retry unsafe. A RED round-trip test reproduced the failure. The migration now queries `sqlite_master` for SQLite and retains inspector behavior elsewhere; the round-trip/fail-closed suite passes. The index expression now uses a SQLAlchemy function expression so DDL compiles as PostgreSQL/SQLite `(lower(email))` and MySQL `((lower(email)))`.
- Model drift check: `auths.py`, `users.py`, `oauth_sessions.py`, and `access_grants.py` are byte-for-byte equal to official v0.11. `models/config.py` preserves the custom atomic multi-worker insert while restoring the official persistence filter; its table columns are unchanged. The functional normalized-email index remains migration-managed exactly as in official v0.11.

### CP5 - Fresh verification and commit

- Status: complete
- Goal: run focused auth/config/migration/dependency tests plus Alembic heads/history checks, verify exclusions and worktree scope, then commit all Lane A work.
- Evidence: final focused pytest matrix `36 passed, 5 warnings`; warnings are existing SQLAlchemy/ldap3/SWIG deprecations. `alembic heads` reports only `a11c0d3f0bd0`; isolated SQLite full upgrade/current and normalized-email downgrade/re-upgrade completed. `uv lock --check` resolved 356 packages. With Node `v22.22.0`, `npm ci --dry-run --ignore-scripts --no-audit --no-fund` completed, and `npm ls --package-lock-only` reports `pyodide@314.0.3`. Changed-file `py_compile`, Ruff `F821`, Ruff formatter check, and `git diff --check` pass. Tracked static/Pyodide files deleted or truncated by test tooling were restored from the proven-clean starting commit and are absent from the final diff.

## Final verification commands

- `pytest -q` over Lane A config/auth/header/dependency tests plus integration/custom migration tests: `36 passed, 5 warnings`.
- `alembic -c alembic.ini heads`: `a11c0d3f0bd0 (head)`.
- Isolated SQLite: full `upgrade head`; `downgrade c0d3b4a5e6f7`; `downgrade f0bd01a18a3d-1`; `upgrade head`; index counts `1 -> 0 -> 1`; duplicate count `0`.
- `uv lock --check`: 356 packages resolved, exit 0.
- Node 22 `npm ci --dry-run --ignore-scripts --no-audit --no-fund`: exit 0.
- `npm ls pyodide --package-lock-only`: `pyodide@314.0.3`.
- `python -m py_compile` for all changed Python files: exit 0.
- Ruff `--select F821`: pass. Ruff formatter check for new/edited tests and migrations: pass.
- `git diff --check`: pass.
- Exact blob comparison against official v0.11 for non-conflict owned paths and owned auth/user/oauth/access models: exit 0.

## Audit ledger

- Conflict-adapted files: `config.py`, `models/config.py`, `routers/auths.py`, `routers/configs.py`, `utils/audit.py`, `utils/auth.py`, `utils/headers.py`, and `Dockerfile` were audited with the merge remerge diff plus official v0.10.2..v0.11.0 patches.
- Missing official behavior repaired: persistent-config filtering, trusted-header `IntegrityError`, group placeholders.
- Official behavior deliberately removed: stock `subagents.*` settings and config endpoints.
- Official migration hardening added after isolated repro: SQLite expression-index detection and dialect-correct functional-index DDL.
- Custom behavior retained: startup singleton migration/config protections, AgentScope budgets, announcement/config additions, conversation-mode profile routes, audit redaction, terminal auth helpers, forwarded-user mapping support, external-services slim Docker profile, and OpenShift static-directory permissions.
- Owned models `auths.py`, `users.py`, `oauth_sessions.py`, and `access_grants.py` match official v0.11 blobs exactly; `models/config.py` differs only for custom atomic seeding and related custom persistence behavior, with the official non-persistent-key rule restored.

## Normalized-email deployment preflight

Before running `f0bd01a18a3d` against any deployment clone, execute this read-only query and require zero rows:

```sql
SELECT lower(email) AS normalized_email, count(*) AS duplicate_count
FROM "user"
WHERE email IS NOT NULL
GROUP BY lower(email)
HAVING count(*) > 1
ORDER BY lower(email);
```

If rows exist, merge or remove the duplicate user records through an explicitly authorized data-remediation plan before migration. The migration itself fails closed with the duplicate values and counts; it does not mutate conflicting users. Its downgrade removes only `uq_user_email_lower`. The integration merge revision has no schema operations in either direction.

## Cross-lane integration notes

- Baseline `backend/open_webui/tools/builtin.py` references `Literal` without importing it. Importing `routers/configs.py` therefore fails during test collection through `utils/tools.py`; Lane A tests isolate the owned router source rather than editing that cross-lane file.
- Official `delegate_task` runtime wiring remains in `main.py`, `tools/builtin.py`, `utils/subagents.py`, `utils/tools.py`, and `utils/timers.py`; owners of those paths must remove it while preserving custom `open_webui.agent.subagents` and AgentScope service routes.
- Official chat-files trio remains in `tools/builtin.py`, `utils/tools.py`, and `utils/middleware.py`; Lane C/B must remove registry/capability/middleware wiring.
- Official subagent frontend API/settings remain under `src/`; Lane D must remove them. Lane A has removed the backend config keys/endpoints they called.

## Residual risks

- No production clone or live database was accessed. Before any deployment, run the documented duplicate-email preflight and full upgrade/rollback on an authorized production clone.
- PostgreSQL and MySQL DDL compile checks pass, but no real PostgreSQL/MySQL database was mutated in this lane. MariaDB documentation does not expose direct functional key parts in `CREATE INDEX`; MariaDB must be validated on an isolated clone before deployment and may need a generated-column strategy if its existing case-insensitive unique email constraint is insufficient.
- Full import of `routers/configs.py` and tool-backed auth/terminal tests remain blocked by the cross-lane missing `Literal` import until that owner integrates its fix.
