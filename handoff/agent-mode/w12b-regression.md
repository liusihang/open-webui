# W12B-5 Regression And Release Readiness Handoff

Date: 2026-06-18

## Goal

Run and document the combined regression/release-readiness gates for the
integrated Agent Mode branch.

## Scope

Owns:

- backend agent/storage regression gate;
- service-local AgentScope runtime tests;
- focused frontend Vitest gate;
- W12 dry-run/fixture/live-evidence validation once scenario workers provide
  evidence fragments;
- ruff and `git diff --check`;
- evidence file `handoff/agent-mode/w12b-regression-evidence.json`;
- this handoff.

Do not touch:

- feature behavior unless a regression gate proves a narrow fix is required;
- scenario-specific implementation owned by W12B-1 through W12B-4.

## Evidence Contract

Record exact commands and pass/fail results. If root `uv.lock` changes from
`uv run`, restore it or record that it is unstaged test churn. Keep
`services/agentscope-runtime/uv.lock` intact.

## Verification Log

2026-06-18 W12B-5 checkpoint:

- Worktree: `/Users/liusihang/openwebui/.worktrees/agent-mode-w12b-regression`
- Branch: `codex/agent-mode-w12b-regression`
- Source context read:
  - `handoff/agent-mode/w12b-regression.md`
  - `scripts/agent_mode/acceptance_harness.py`
  - `docs/runbooks/agent-mode-runtime-deployment.md`
  - controller handoff from
    `/Users/liusihang/openwebui/.worktrees/agent-mode-agentscope-pr7/handoff/agent-mode/controller.md`
- Scope reminder: this worker is recording regression/release-readiness gates only;
  live scenario acceptance remains owned by W12B-1 through W12B-4.

Planned gate commands:

- `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/agent backend/open_webui/test/models/test_agent_runs.py`
- `cd services/agentscope-runtime && uv run --extra test pytest`
- `npm run test:frontend -- --run src/lib/apis/agentRuns/index.test.ts src/lib/components/chat/AgentEvents/eventFold.test.ts src/lib/components/chat/historySync.test.ts`
- `python3 scripts/agent_mode/acceptance_harness.py dry-run`
- `python3 scripts/agent_mode/acceptance_harness.py fixture`
- `uv run ruff check backend/open_webui/agent backend/open_webui/routers/agent_service.py backend/open_webui/test/agent scripts/agent_mode`
- `git diff --check origin/pr/7..HEAD`

Results:

- PASS:
  `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/agent backend/open_webui/test/models/test_agent_runs.py`
  -> `79 passed, 19 warnings in 91.84s`.
- PASS:
  `cd services/agentscope-runtime && uv run --extra test pytest`
  -> `19 passed in 5.80s`.
- PASS:
  `npm run test:frontend -- --run src/lib/apis/agentRuns/index.test.ts src/lib/components/chat/AgentEvents/eventFold.test.ts src/lib/components/chat/historySync.test.ts`
  -> `3 test files passed, 24 tests passed`.
- PASS:
  `python3 scripts/agent_mode/acceptance_harness.py dry-run`
  -> `case contract: 0/12 satisfied`, `live acceptance: pending`,
  `failures: none`.
- PASS:
  `python3 scripts/agent_mode/acceptance_harness.py fixture`
  -> `case contract: 12/12 satisfied`, `live acceptance: pending`,
  `failures: none`.
- PASS:
  `uv run ruff check backend/open_webui/agent backend/open_webui/routers/agent_service.py backend/open_webui/test/agent scripts/agent_mode`
  -> `All checks passed!`.
- PASS:
  `git diff --check origin/pr/7..HEAD`
  -> no whitespace errors reported.

Evidence file:

- `handoff/agent-mode/w12b-regression-evidence.json`

Lockfile handling:

- Root `uv.lock` changed during `uv run` backend and ruff gates and was restored.
- `services/agentscope-runtime/uv.lock` remained unchanged and intact.

Overall W12B-5 gate status: PASS for regression/release-readiness gates.
W12B live scenario acceptance remains pending/not claimed by this worker.

## Notes

You are not alone in the codebase. Other W12B workers may be editing their own
worktrees. Do not revert their changes or broaden your scope.
