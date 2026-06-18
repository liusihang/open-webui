# W12D-3 Subagents / Model Selection Handoff

Date: 2026-06-18

## Goal

Prove live acceptance scenarios 6 and 7 against this worktree, or make only
narrow fixes needed for those scenarios.

Scenarios:

6. Leader creates concurrent subagents up to cap.
7. Subagent model selection uses `meta.agent_selection`.

## Base

- Worktree:
  `/Users/liusihang/openwebui/.worktrees/agent-mode-w12d-subagents`
- Branch: `codex/agent-mode-w12d-subagents`
- Base commit: `78f4cf294`
- Integration target:
  `/Users/liusihang/openwebui/.worktrees/agent-mode-agentscope-pr7`

## Read-Only Context

- Root implementation plan:
  `/Users/liusihang/openwebui/docs/plans/2026-06-17-openwebui-agent-mode-agentscope-implementation.md`
- Runtime contracts:
  `/Users/liusihang/openwebui/docs/plans/2026-06-18-openwebui-agent-mode-runtime-contracts.md`
- Design:
  `/Users/liusihang/openwebui/docs/plans/2026-06-17-openwebui-agent-mode-agentscope-design.md`
- Controller handoff:
  `/Users/liusihang/openwebui/.worktrees/agent-mode-agentscope-pr7/handoff/agent-mode/controller.md`

## Owned Outputs

- Update this handoff with commands, evidence, fixes, and blockers.
- Write live evidence to:
  `handoff/agent-mode/w12d-subagents-evidence.json`
- If code changes are required, keep them limited to OpenWebUI subagent control
  plane, model catalog/selection, or AgentScope runtime subagent adapter and
  commit them on this branch.

## Suggested Ports And Paths

- Backend: `http://127.0.0.1:18103`
- AgentScope runtime: `http://127.0.0.1:8113`
- Data dir: `/private/tmp/openwebui-agent-mode-w12d-subagents-data`
- Static dir: `/private/tmp/openwebui-agent-mode-w12d-subagents-static`
- Service token: `test-service-token`
- Team cap: `5`

Use long-running terminal sessions for backend/runtime.

## Constraints

- Do not fork or rely on the full brainstorming chat.
- Do not edit tool/terminal/frontend layout code unless a direct subagent/model
  selection bug proves it is required.
- Keep the team single-level: leader -> subagents. Do not add nested subagent
  teams for W12D.
- Do not stage root `uv.lock` churn.

## Required Evidence

- Scenario 6: participant/subagent events showing concurrent subagent creation
  up to cap `5`, plus cap rejection or stop behavior beyond the cap.
- Scenario 7: model-selection requested/completed events showing a
  permission-valid selected model id derived from `meta.agent_selection` hints
  or the model catalog helper.
- Budget evidence: per-subagent budget accounting is visible and no subagent can
  exceed its assigned budget silently.

## Current Session Log

### 2026-06-18 - W12D-3 Scope And Evidence Strategy

- Confirmed assigned worktree/branch:
  `/Users/liusihang/openwebui/.worktrees/agent-mode-w12d-subagents` on
  `codex/agent-mode-w12d-subagents`.
- Read the required handoffs/plans before acting:
  - this handoff;
  - controller handoff in
    `/Users/liusihang/openwebui/.worktrees/agent-mode-agentscope-pr7`;
  - root implementation plan;
  - runtime contracts addendum.
- Key finding from W12B evidence: prior subagent/model-selection proof was
  service-local and backend-test evidence only; it explicitly did not capture a
  direct integrated OpenWebUI + AgentScope runtime service execution.
- Key finding in this worktree: the runtime service public `start_run` endpoint
  accepts a run and appends `run.running`, while the subagent adapter lives in
  service code and can drive OpenWebUI callbacks. W12D-3 evidence will therefore
  use a DB-backed OpenWebUI service process plus the runtime adapter/client
  against real callback routes, and will record any missing public runtime
  driver as a blocker rather than widening the feature.
- Assigned local service URLs:
  - backend: `http://127.0.0.1:18103`;
  - AgentScope runtime: `http://127.0.0.1:8113`.

### 2026-06-18 - W12D-3 Live Acceptance And Fixes

- Started DB-backed OpenWebUI on `http://127.0.0.1:18103` and AgentScope
  runtime on `http://127.0.0.1:8113` with:
  - data dir `/private/tmp/openwebui-agent-mode-w12d-subagents-data`;
  - static dir `/private/tmp/openwebui-agent-mode-w12d-subagents-static`;
  - service token `test-service-token`;
  - team cap `5`;
  - seeded admin user `w12d-user@example.test`;
  - seeded models `agent-general` and `agent-python`, both carrying
    `meta.agent_selection`.
- Startup note: the first backend launch failed because `DATA_DIR` was created
  after importing `open_webui.main`; OpenWebUI opens the SQLite DB during import.
  Relaunch used `mkdir -p` before import and `/health` returned
  `{"status": true}`.
- First live adapter run exposed event append concurrency failure:
  concurrent model-selection callbacks hit duplicate `agent_run_event`
  `(run_id, seq)` and returned HTTP 500. Added red test
  `test_append_event_handles_concurrent_writers`, then fixed
  `AgentRuns.append_event` to retry on `IntegrityError` from the DB uniqueness
  guard.
- After the event fix, live run
  `92e54947-fcf6-4b86-880f-0d168834e528` proved scenario 7 but failed scenario
  6: concurrent `/subagents` callbacks used read-modify-write on
  `agent_run.participants` and lost updates. Only one of five concurrent
  subagents remained in the DB, so the over-cap registration was incorrectly
  accepted.
- Added red test
  `test_db_backed_subagent_registration_preserves_concurrent_participants`.
  Fixed DB-backed `AgentRunSubagentStore` with atomic registration:
  - SQLite uses `BEGIN IMMEDIATE` before reading/updating the run row;
  - other DBs use `SELECT ... FOR UPDATE`;
  - cap and budget allocation are computed from the locked current row.
- Final live evidence was written to
  `handoff/agent-mode/w12d-subagents-evidence.json`.
  - Run id: `b9c75c49-0cfe-4a98-bbc1-179b2159c661`.
  - Runtime session id:
    `rt_b9c75c49-0cfe-4a98-bbc1-179b2159c661_FvcfsTDWeYw`.
  - Scenario 6: `live_passed`.
    - 5 subagent participants persisted.
    - 5 `subagent.created` and 5 `subagent.completed` events persisted.
    - executor-observed concurrency peak was `5`.
    - 6th registration returned HTTP `409` with
      `subagent_cap_exceeded`.
  - Scenario 7: `live_passed`.
    - 5 `model.selection.completed` events persisted.
    - selected model ids were all `agent-python`.
    - fuzzy request was `data`, matching `agent-python`
      `meta.agent_selection.skills`.
  - Budget evidence:
    `team.max_subagents=5`, `team.max_steps=20`, `team.used_steps=10`,
    `team.remaining_steps=10`, and each subagent has
    `max_steps=2/remaining_steps=2`.
- Evidence limitation, not a blocker for W12D-3: the runtime HTTP service
  exposes run start/cancel/status, but no public subagent-plan endpoint. The
  proof uses public runtime start first, then drives
  `AgentScopeSubagentAdapter` directly against live OpenWebUI callback routes.
- Focused verification so far:
  - Red before fix:
    `test_append_event_handles_concurrent_writers` failed on duplicate/lost
    concurrent event sequencing.
  - Green after fix:
    `test_append_event_handles_concurrent_writers` -> `1 passed`.
  - Red before fix:
    `test_db_backed_subagent_registration_preserves_concurrent_participants`
    failed with only one persisted subagent.
  - Green after fix:
    `test_db_backed_subagent_registration_preserves_concurrent_participants`
    -> `1 passed`.
  - Adjacent gate:
    `pytest -q backend/open_webui/test/models/test_agent_runs.py
    backend/open_webui/test/agent/test_subagents.py
    backend/open_webui/test/agent/test_model_catalog.py`
    -> `23 passed`.
  - Full backend agent/storage gate:
    `pytest -q backend/open_webui/test/agent
    backend/open_webui/test/models/test_agent_runs.py`
    -> `92 passed`.
  - Focused ruff:
    `ruff check --select F backend/open_webui/models/agent_runs.py
    backend/open_webui/agent/subagents.py
    backend/open_webui/test/models/test_agent_runs.py
    backend/open_webui/test/agent/test_subagents.py`
    -> passed.
  - `git diff --check` -> passed.

### 2026-06-18 - Harness Scenario List Follow-Up

- Controller review found that
  `handoff/agent-mode/w12d-subagents-evidence.json` had detailed W12D-3 proof
  but no top-level `scenarios` list, so `acceptance_harness.py live` saw zero
  satisfied scenarios.
- Evidence-only follow-up:
  - added top-level `scenarios` entry
    `scenario_06_subagent_cap_concurrency` with status `live_passed`,
    `live_status=passed`, and observations:
    `event:subagent.created`, `subagent_concurrency:observed`,
    `subagent_cap:5`;
  - added top-level `scenarios` entry
    `scenario_07_subagent_model_selection` with status `live_passed`,
    `live_status=passed`, and observations:
    `event:model.selection.requested`, `event:model.selection.completed`,
    `meta.agent_selection`;
  - kept existing detailed proof at the top level and added concise per-scenario
    `evidence` summaries.
- Validation:
  `uv run --frozen python scripts/agent_mode/acceptance_harness.py live
  --evidence handoff/agent-mode/w12d-subagents-evidence.json`
  exited `1` as expected for subset evidence and reported:
  `case contract: 2/12 satisfied`.

## Verification

Run focused tests for any touched code. If you make code changes, also run:

- `git diff --check HEAD~1..HEAD`
- focused ruff or `ruff --select F` on changed Python files
- service-local runtime tests if you touch `services/agentscope-runtime/*`

## Final Response To Controller

Return:

- evidence file path;
- run ids, participant ids, selected model ids, service URLs used;
- tests run and results;
- commit hash if you changed code;
- blockers, if any.
