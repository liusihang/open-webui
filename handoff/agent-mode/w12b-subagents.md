# W12B-3 Subagent And Model Selection Handoff

Date: 2026-06-18

## Goal

Produce real/local acceptance evidence for:

- scenario 06: leader creates concurrent subagents up to the cap;
- scenario 07: subagent model selection uses `meta.agent_selection`.

## Scope

Owns:

- subagent/model-selection acceptance investigation and narrow fixes required
  for scenarios 06 and 07;
- evidence file `handoff/agent-mode/w12b-subagents-evidence.json`;
- this handoff.

Do not touch:

- terminal/Open Terminal behavior;
- frontend layout/visual polish;
- broad OpenWebUI auth/model permission rewrites.

## Evidence Contract

Only mark a scenario `live_passed` when there is direct evidence from the
integrated services. If that is not possible, write `status: "incomplete"` and
explain why in `evidence.notes`.

Required observations:

- scenario 06: `event:subagent.created`, `subagent_concurrency:observed`,
  `subagent_cap:5`.
- scenario 07: `event:model.selection.requested`,
  `event:model.selection.completed`, `meta.agent_selection`.

## Verification Log

### 2026-06-18 03:54 CST - Read/Scope Checkpoint

- Confirmed worktree and branch:
  `git -C /Users/liusihang/openwebui/.worktrees/agent-mode-w12b-subagents status --short --branch`
  -> `## codex/agent-mode-w12b-subagents`.
- Read first, per assignment:
  - `handoff/agent-mode/w12b-subagents.md`
  - `scripts/agent_mode/acceptance_harness.py`
  - `docs/runbooks/agent-mode-runtime-deployment.md`
  - `backend/open_webui/agent/subagents.py`
  - `backend/open_webui/agent/model_catalog.py`
  - `backend/open_webui/routers/agent_service.py`
  - `services/agentscope-runtime/agentscope_runtime/subagents.py`
  - focused backend/runtime tests for subagents and model catalog.
- Finding: W12 runbook and harness still define fixture/dry-run checks only;
  live W12B acceptance requires direct integrated-service evidence.
- Finding: backend model selection already filters through W9A
  `AgentModelCatalog`, emits `model.selection.requested` and
  `model.selection.completed`, and returns `meta.agent_selection` with a
  permission-valid selected model in focused tests.
- Finding: runtime client expected
  `POST /api/agent/service/runs/{run_id}/subagents`, but
  `backend/open_webui/routers/agent_service.py` had no matching route.
- Finding: `AgentScopeSubagentAdapter.run_subagent_plan` ran specs
  sequentially, so scenario 06 concurrency could not be locally evidenced.

### 2026-06-18 03:59 CST - Red Tests

- Added backend red test for the W9B2 registration callback shape:
  `SubagentRegisterRequest` with `name`, `description`, `task`, `budget`,
  and `metadata`, routed through
  `execute_agent_run_subagent_registration`.
- Red command:
  `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/agent/test_subagents.py::test_subagent_service_callback_requires_authority_and_delegates_registration`
- Red result:
  failed as expected with `assert None is not None` because
  `execute_agent_run_subagent_registration` did not exist.
- Added runtime red test:
  `tests/test_subagents.py::test_plan_runs_subagents_concurrently_up_to_team_cap`.
- Red command:
  `uv run --extra test pytest -q tests/test_subagents.py::test_plan_runs_subagents_concurrently_up_to_team_cap`
  from `services/agentscope-runtime`.
- Red result:
  failed as expected with `TimeoutError` waiting for all five subagent
  executors to be active together; this proved the plan loop was sequential.

### 2026-06-18 04:03 CST - Narrow Fixes

- Added `SubagentRegisterRequest` and
  `AgentSubagentCoordinator.register_subagent(...)` in
  `backend/open_webui/agent/subagents.py`.
  - Registration now matches the runtime client contract.
  - OpenWebUI remains the cap and participant-record authority.
  - Model choice stays in the separate `/model-selection` path; registration
    does not claim scenario 07 by itself.
- Added `POST /runs/{run_id}/subagents` in
  `backend/open_webui/routers/agent_service.py`.
  - Uses the same service bearer-token and idempotency-key checks as the other
    agent service callbacks.
  - Maps `SubagentError` to structured HTTP 409 details.
- Updated `services/agentscope-runtime/agentscope_runtime/subagents.py` so
  `run_subagent_plan` schedules subagents concurrently up to
  `min(max_concurrency, team_cap, 5)` and preserves result order.
  - Default plan concurrency is the adapter team cap.
  - The existing cancellation test now passes `max_concurrency=1` to keep its
    sequential-loop assertion explicit.

### 2026-06-18 04:04 CST - Green/Acceptance Evidence

- Focused backend green:
  `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/agent/test_subagents.py::test_subagent_service_callback_requires_authority_and_delegates_registration`
  -> `1 passed, 2 warnings in 2.20s`.
- Focused runtime green:
  `uv run --extra test pytest -q tests/test_subagents.py::test_plan_runs_subagents_concurrently_up_to_team_cap`
  from `services/agentscope-runtime`
  -> `1 passed in 0.02s`.
- Backend adjacent green:
  `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/agent/test_subagents.py backend/open_webui/test/agent/test_model_catalog.py`
  -> `13 passed, 2 warnings in 2.20s`.
- Runtime adjacent green:
  `uv run --extra test pytest -q tests/test_subagents.py tests/test_openwebui_client.py`
  from `services/agentscope-runtime`
  -> `11 passed in 0.69s`.
- Harness prerequisite checks:
  `python3 scripts/agent_mode/acceptance_harness.py dry-run && python3 scripts/agent_mode/acceptance_harness.py fixture`
  -> dry-run listed requirements with no failures; fixture contract
  `12/12 satisfied`; live acceptance remains pending.
- Created
  `handoff/agent-mode/w12b-subagents-evidence.json` with only:
  - `scenario_06_subagent_cap_concurrency`
  - `scenario_07_subagent_model_selection`
- Scenario status decision:
  - scenario 06: `status: "incomplete"`, `live_status: "not_proven"`.
    Local service tests prove concurrent cap-5 runtime behavior, but no direct
    integrated OpenWebUI + AgentScope runtime service run was captured.
  - scenario 07: `status: "incomplete"`, `live_status: "not_proven"`.
    Local backend/runtime tests prove permission-valid model selection and
    `meta.agent_selection`, but no direct integrated service run was captured.

### 2026-06-18 04:08 CST - Final Verification Before Commit

- Restored `uv.lock` after the first root `uv run` created a broad resolver
  refresh unrelated to this slice.
- Backend lint:
  `uv run --frozen ruff check backend/open_webui/agent/subagents.py backend/open_webui/routers/agent_service.py backend/open_webui/test/agent/test_subagents.py`
  -> `All checks passed!`.
- Backend focused tests rerun with restored lock:
  `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run --frozen pytest -q backend/open_webui/test/agent/test_subagents.py backend/open_webui/test/agent/test_model_catalog.py`
  -> `13 passed, 4 warnings in 12.50s`.
- Runtime focused tests rerun with restored lock:
  `uv run --frozen --extra test pytest -q tests/test_subagents.py tests/test_openwebui_client.py`
  from `services/agentscope-runtime`
  -> `11 passed in 0.18s`.
- Evidence JSON parse:
  `python3 -m json.tool handoff/agent-mode/w12b-subagents-evidence.json >/dev/null`
  -> passed.
- Whitespace check:
  `git diff --check`
  -> passed.
- Service lint note: `services/agentscope-runtime/pyproject.toml` does not
  declare ruff. A root-config ruff invocation against service files was not
  used as a final gate because it applies root quote-style rules to the
  service package's existing double-quote style. Service-local pytest is green.

## Notes

You are not alone in the codebase. Other W12B workers may be editing their own
worktrees. Do not revert their changes or broaden your scope.
