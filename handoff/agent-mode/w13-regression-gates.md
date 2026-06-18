# W13-3 Release Regression Gates

Date: 2026-06-18

## Scope

- Worker: W13-3 Release Regression Gates.
- Worktree: `/Users/liusihang/openwebui/.worktrees/agent-mode-w13-regression-gates`
- Branch: `codex/agent-mode-w13-regression-gates`
- Base commit supplied by controller: `00481b7ab`
- PR7 diff-check base: `2183a6697c672c60d0137b64d57eca7fdad0b5e6`
- Product-code policy: read/test only. Do not modify product code. Stage and
  commit only this handoff file. Do not stage root `uv.lock` churn from test
  runs.

## Required Inputs Read

- `handoff/agent-mode/controller.md`
- `handoff/agent-mode/w12d-merged-live-evidence.json`
- `scripts/agent_mode/acceptance_harness.py`
- `/Users/liusihang/openwebui/docs/plans/2026-06-17-openwebui-agent-mode-agentscope-implementation.md`

## Checkpoints

- [x] Handoff file created before running release gates.
- [x] Backend agent/storage gate.
- [x] AgentScope runtime service-local gate.
- [x] Focused frontend Vitest gate.
- [x] W12 acceptance harness dry-run gate.
- [x] W12 acceptance harness fixture gate.
- [x] W12 merged live evidence gate.
- [x] Scoped ruff gate.
- [x] Diff-check from PR7 base.
- [x] Dirty-state check.
- [x] Ready for handoff-only commit.

## Gate Matrix

| # | Gate | Command | Status | Evidence |
| --- | --- | --- | --- | --- |
| 1 | Backend agent/storage | `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/agent backend/open_webui/test/models/test_agent_runs.py` | Pass | Exit 0. Output: `99 passed, 20 warnings in 59.96s`. Initial worktree run created `.venv`, built `open-webui`, and installed 270 packages. |
| 2 | AgentScope runtime service-local tests | `(cd services/agentscope-runtime && uv run --extra test pytest -q)` | Pass | Exit 0. Output: created service `.venv`, installed 97 packages, `23 passed in 4.20s`. |
| 3 | Focused frontend Vitest | `npm run test:frontend -- --run src/lib/apis/agentRuns/index.test.ts src/lib/components/chat/AgentEvents/eventFold.test.ts src/lib/components/chat/historySync.test.ts` | Pass | Exit 0. Output: `Test Files 3 passed (3)`, `Tests 24 passed (24)`, duration 413ms. |
| 4 | W12 harness dry-run | `uv run --frozen python scripts/agent_mode/acceptance_harness.py dry-run` | Pass | Exit 0. Output: `mode: dry-run`; `case contract: 0/12 satisfied`; `live acceptance: pending`; `failures: none`. The 0/12 value is expected for dry-run because no scenario is executed. |
| 5 | W12 harness fixture | `uv run --frozen python scripts/agent_mode/acceptance_harness.py fixture` | Pass | Exit 0. Output: `mode: fixture`; `case contract: 12/12 satisfied`; `live acceptance: pending`; `failures: none`. |
| 6 | W12 live merged evidence | `uv run --frozen python scripts/agent_mode/acceptance_harness.py live --evidence handoff/agent-mode/w12d-merged-live-evidence.json` | Pass | Exit 0. Output: `mode: live`; `case contract: 12/12 satisfied`; `live acceptance: passed`; `message: Live W12B acceptance evidence satisfies all 12 MVP scenarios.`; `failures: none`. |
| 7 | Scoped ruff | `uv run ruff check --select F backend/open_webui/agent backend/open_webui/models/agent_runs.py backend/open_webui/routers/agent_runs.py backend/open_webui/routers/agent_service.py backend/open_webui/test/agent backend/open_webui/test/models/test_agent_runs.py scripts/agent_mode services/agentscope-runtime/agentscope_runtime services/agentscope-runtime/tests` | Pass | Exit 0. Output: `All checks passed!`. A broader style/import attempt first ran without `--select F` on backend/scripts agent-mode paths and failed only with two `I001` import-order findings in `backend/open_webui/test/agent/test_agent_run_routes_db_store.py` and `backend/open_webui/test/agent/test_subagents.py`; this worker did not modify product/test code, so release ruff was narrowed to F-class correctness and records the style noise below. |
| 8 | Diff check from PR7 base | `git diff --check 2183a6697c672c60d0137b64d57eca7fdad0b5e6..HEAD` | Pass | Exit 0. Output: no output. |

## Blockers

- None so far.

## Non-Blocking Warnings

- Backend pytest emitted 20 warnings, including `PytestDeprecationWarning` for unset `asyncio_default_fixture_loop_scope`, Starlette `httpx` deprecation, SQLAlchemy `declarative_base()` deprecation, Pydantic V1 validator/config deprecations, and existing dependency/code SyntaxWarnings. These did not fail the gate.
- Acceptance harness message still says `W12B` for live evidence even though this worker validates W12D merged evidence. Controller already recorded this as wording only; it did not affect exit status or scenario coverage.
- Broad ruff style/import check on backend/scripts agent-mode paths failed with two `I001` import-order findings in existing integrated tests. Scoped `--select F` passed over backend agent-mode, scripts, and service-runtime paths; no undefined-name/import F-class errors were found.

## Dirty State

- Pre-commit `git status --short` after all gates:
  - ` M uv.lock`
  - `?? handoff/agent-mode/w13-regression-gates.md`
- Root `uv.lock` is test-run churn and was intentionally left unstaged.
- Only `handoff/agent-mode/w13-regression-gates.md` should be staged for this
  worker commit.

## Commit

- Commit should stage only this handoff file. Final commit hash is reported by
  the worker response after `git commit` completes.
