# W12D-4 SSE / UI / Compaction Handoff

Date: 2026-06-18

## Goal

Prove live acceptance scenarios 8 and 11 against this worktree, or make only
narrow fixes needed for those scenarios.

Scenarios:

8. SSE reconnect backfills by event sequence.
11. Terminal states trigger compaction.

## Base

- Worktree:
  `/Users/liusihang/openwebui/.worktrees/agent-mode-w12d-sse-ui`
- Branch: `codex/agent-mode-w12d-sse-ui`
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
  `handoff/agent-mode/w12d-sse-ui-evidence.json`
- If code changes are required, keep them limited to Agent Event SSE/API/UI
  rendering, event folding, history sync, or compaction behavior and commit them
  on this branch.

## Suggested Ports And Paths

- Backend: `http://127.0.0.1:18104`
- AgentScope runtime: `http://127.0.0.1:8114`
- Frontend dev server, if needed: `http://127.0.0.1:5174`
- Data dir: `/private/tmp/openwebui-agent-mode-w12d-sse-data`
- Static dir: `/private/tmp/openwebui-agent-mode-w12d-sse-static`
- Service token: `test-service-token`

Use long-running terminal sessions for backend/runtime/frontend. If browser
automation is needed, capture screenshots or DOM evidence paths in this handoff.

## Constraints

- Do not fork or rely on the full brainstorming chat.
- Do not edit backend authority logic, runtime adapter internals, or terminal
  tool execution unless a direct SSE/UI/compaction bug proves it is required.
- Do not show raw reasoning in UI evidence or fixtures.
- Do not stage root `uv.lock` churn.

## Required Evidence

- Scenario 8: reconnect using `Last-Event-ID` or `after_seq`, backfilled events
  by sequence, and no duplicate final text/rendering after reconnect.
- Scenario 11: terminal-state compaction summary reconstructs user-visible
  details, including artifact/process refs required by the UI.
- UI evidence should show user-side necessary information only, not operational
  internals.

## Verification

Run focused frontend/backend tests for any touched code. If you make code
changes, also run:

- `git diff --check HEAD~1..HEAD`
- focused frontend Vitest for Agent Event UI if frontend files change
- focused ruff or `ruff --select F` on changed Python files

## Final Response To Controller

Return:

- evidence file path;
- run ids, event seq ranges, reconnect method, and UI proof used;
- tests run and results;
- commit hash if you changed code;
- blockers, if any.

## Checkpoints

### 2026-06-18 Initial Read / Preflight

- Confirmed worktree/branch:
  `/Users/liusihang/openwebui/.worktrees/agent-mode-w12d-sse-ui` on
  `codex/agent-mode-w12d-sse-ui`.
- Read required context:
  - this handoff;
  - controller handoff through the W12D scenario dispatch checkpoint;
  - root implementation plan;
  - runtime contract addendum.
- Relevant contract points:
  - scenario 8 must prove reconnect via `Last-Event-ID` or `after_seq` with
    sequence backfill and no duplicate rendered final text;
  - scenario 11 must prove terminal states (`completed`, `failed`,
    `cancelled`, `budget_exceeded`) run compaction and retain user-visible
    artifact/process details;
  - SSE is authoritative for messages with `agent_run_id`; socket deltas must
    not duplicate Agent Mode final text.
- Assigned ports checked free before service startup:
  - backend `18104`;
  - AgentScope runtime `8114`;
  - frontend `5174`.
- Planned next step: inspect live evidence schema/startup commands, then run
  integrated services and capture evidence into
  `handoff/agent-mode/w12d-sse-ui-evidence.json`.

### 2026-06-18 Live Evidence / Narrow Fix Checkpoint

- Live stack used assigned ports:
  - backend `http://127.0.0.1:18104`;
  - AgentScope runtime `http://127.0.0.1:8114`;
  - data dir `/private/tmp/openwebui-agent-mode-w12d-sse-data`;
  - static dir `/private/tmp/openwebui-agent-mode-w12d-sse-static`;
  - service token `test-service-token`.
- Rebuilt frontend with `npm run build`; build exited 0 with pre-existing
  Svelte warnings. Backend was restarted on port `18104` after the source and
  frontend API route fixes.
- Narrow fixes made in this worktree:
  - `src/lib/apis/agentRuns/index.ts` now targets backend-mounted
    `/api/agent/runs` rather than `/api/v1/agent/runs`.
  - `backend/open_webui/models/agent_runs.py` now builds a compacted summary
    from persisted events/artifacts when transitioning to a terminal state and
    no summary was supplied.
  - `backend/open_webui/agent/compaction.py` now prefers ORM `meta` before the
    SQLAlchemy declarative `metadata` attribute when compacting artifacts.
- Scenario 8 live proof:
  - run id `11a09238-0455-48af-b235-4a1a0b1a88ce`;
  - chat id `w12d-sse-chat-1781750237-ab23b64b`;
  - event seq range `1..6`;
  - reconnect method: live SSE
    `GET /api/agent/runs/{run_id}/events` with `Last-Event-ID: 3`;
  - JSON `after_seq=3` and SSE `Last-Event-ID: 3` both returned seq
    `[4, 5, 6]`;
  - duplicate final-delta retry returned existing seq `5` and did not append
    extra final text.
- Scenario 8 UI proof:
  - browser loaded real chat route
    `/c/w12d-sse-chat-1781750237-ab23b64b` after signin;
  - frontend resource proof:
    `/api/agent/runs/11a09238-0455-48af-b235-4a1a0b1a88ce/events/list?after_seq=0`
    then
    `/api/agent/runs/11a09238-0455-48af-b235-4a1a0b1a88ce/events?after_seq=6`;
  - DOM proof after reload: 5 Agent Run event rows, 1 final answer container,
    and final text `SSE reconnect final answer arrived once.` appears once;
  - screenshot:
    `handoff/agent-mode/w12d-sse-ui-proof.png`.
- Scenario 11 live proof:
  - terminal state run ids:
    - `completed`: `8dcb910c-5f0d-4fd2-bd35-12742e1932f3`;
    - `failed`: `fb600ee5-3c55-42ac-92bb-e6215a0340de`;
    - `cancelled`: `c39b6ffe-78e8-4d7a-bdf2-a3f99b3b7929`;
    - `budget_exceeded`: `fd02f780-d7a6-4023-9b33-851b98de8f29`.
  - Each terminal transition populated `summary.ui.tools`,
    `summary.ui.artifacts`, and `summary.ui.process_refs`.
  - Duplicate terminal transition returned the same summary and state version
    for all four states, proving compaction ran once.
- Evidence written to
  `handoff/agent-mode/w12d-sse-ui-evidence.json`.
- Full `acceptance_harness.py live` was run against this two-scenario fragment
  and failed as expected with `case contract: 2/12 satisfied`; missing
  scenarios are outside W12D-4 scope.
- Root `uv.lock` changed during tool/test execution and was restored before
  staging.
- Final focused verification:
  - `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run --frozen pytest -q backend/open_webui/test/models/test_agent_runs.py backend/open_webui/test/agent/test_agent_run_routes_db_store.py`
    -> `18 passed, 4 warnings`;
  - `npm run test:frontend -- --run src/lib/apis/agentRuns/index.test.ts src/lib/components/chat/AgentEvents/eventFold.test.ts src/lib/components/chat/historySync.test.ts`
    -> `3 passed / 24 tests passed`;
  - `uv run --frozen ruff check backend/open_webui/agent/compaction.py backend/open_webui/models/agent_runs.py backend/open_webui/test/models/test_agent_runs.py`
    -> `All checks passed!`;
  - `git diff --check` -> passed;
  - local evidence assertion script for scenarios 8 and 11 -> passed.
- `python3 scripts/agent_mode/acceptance_harness.py live --evidence handoff/agent-mode/w12d-sse-ui-evidence.json`
  exits `1` because this fragment covers only W12D-4 scenarios, with harness
  output `case contract: 2/12 satisfied`.
- Commit is still pending at this checkpoint.
