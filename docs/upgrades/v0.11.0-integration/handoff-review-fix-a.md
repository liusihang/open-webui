# v0.11 integration review fix A handoff

## Truth surface and boundaries

- Worktree: `/Users/liusihang/.codex/worktrees/fc9a/openwebui`
- Branch: `codex/v011-review-fix-a`
- Required starting commit: `51ac3be552df87c9a87bd3f647905a47b4588ee1`
- Base branch: `codex/v011-upstream-integration-base` (verified at the required starting commit)
- Official reference: `f9590b8017199e56d5e953657e6498e3cef1d246` (`v0.11.0`)
- Scope: restore the pre-app `apply_orjson_http_json()` call and merge `ChatMessage`/`Chat` Alembic metadata imports while retaining `AgentRun`/`Calendar` imports.
- Excluded: Agent run behavior, i18n, deployment, containers, live services, live databases, and the root checkout.

## Checkpoints

### CP0 - Baseline and task branch

- Status: complete
- Goal: prove the exact worktree, clean starting state, base ref, official donor object, and task boundary before edits.
- Evidence: worktree started clean and detached at `51ac3be552df87c9a87bd3f647905a47b4588ee1`; `codex/v011-upstream-integration-base` resolves to the same SHA; official donor object exists locally; created `codex/v011-review-fix-a` in this worktree only.

### CP1 - RED regression tests

- Status: complete
- Goal: add focused tests for call ordering and Alembic metadata imports, then observe failures caused by the two missing integration behaviors.
- RED command: `uvx --from pytest pytest -q test/test_v011_review_fix_a_contracts.py`.
- RED evidence: `2 failed in 0.04s`; the first failure reported no module-scope `apply_orjson_http_json()` call, and the second reported the missing `ChatMessage` Alembic model import. Both failures were assertions caused by the intended missing behavior, not collection or environment errors.
- Harness note: the first project-level `uv run` attempt was stopped during large dependency setup; the source-only tests were placed in the existing top-level `test/` directory so isolated pytest could execute them without importing the OpenWebUI package.

### CP2 - Minimal implementation and GREEN

- Status: complete
- Goal: make only the two required production edits and rerun the focused tests.
- Implementation: restored the official opt-in ORJSON comment and module-scope call immediately before `app = FastAPI(...)`; added official `ChatMessage` and `Chat` model imports to the Alembic env without changing the existing `AgentRun` or `Calendar` imports.
- GREEN command: `uvx --from pytest pytest -q test/test_v011_review_fix_a_contracts.py`.
- GREEN evidence: `2 passed in 0.03s`.

### CP3 - Verification and scope audit

- Status: complete
- Goal: run related tests, Python/static checks, `git diff --check`, and inspect the exact changed-file set.
- Related test command: `PYTHONPATH=backend /Users/liusihang/openwebui/.venv/bin/pytest -q test/test_v011_review_fix_a_contracts.py backend/open_webui/test/util/test_v011_dependency_contracts.py backend/open_webui/test/util/test_migration_revision_graph.py`.
- Related test evidence: `8 passed, 1 warning in 2.26s`; warnings were existing pytest-asyncio loop-scope configuration and SQLAlchemy `declarative_base()` deprecation notices.
- `python -m py_compile` for `main.py`, `migrations/env.py`, and the new test: exit 0.
- Ruff on the complete new test file: `All checks passed!`.
- Ruff `--select F821` on all three changed Python files: `All checks passed!`.
- `git diff --check`: exit 0.
- Scope: two production files contain six inserted official lines total; the remaining tracked task artifacts are the focused test and this handoff. No Agent run behavior, i18n, deployment, live, or root-checkout files were edited.
- Baseline note: unrestricted whole-file Ruff found 19 pre-existing issues in `main.py` and its formatter would rewrite unrelated code, so no broad lint/format changes were applied. An early minimal-environment migration-graph attempt also stopped before assertions on a missing `markdown` dependency; the supplied reusable project venv then ran the relevant combined matrix successfully.

### CP4 - Commit

- Status: complete
- Goal: commit only this task's files with a clear message and record the final SHA.
- Evidence: staged scope contained exactly `main.py`, `migrations/env.py`, the focused contract test, and this handoff; staged `git diff --check` passed. Created the task commit with message `fix: restore v0.11 startup and migration metadata`, then amended only this CP4 completion readback. The authoritative final SHA is the branch HEAD reported by `git rev-parse HEAD` because a commit cannot embed its own SHA in its contents.

## Resume instructions

Continue only in the worktree above. First run `git status --short --branch` and `git rev-parse HEAD`, then resume the first incomplete checkpoint. Do not touch the root checkout or any live surface.
