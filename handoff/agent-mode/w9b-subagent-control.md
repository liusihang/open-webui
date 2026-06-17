# W9B1 OpenWebUI Subagent Control Plane Handoff

Date: 2026-06-18

## Goal

Implement the OpenWebUI-side control plane for Agent Mode subagents on top of
integration commit `28830b966`.

This slice defines durable participant state, subagent cap/budget enforcement,
model-selection callback binding using W9A, and stable subagent event fixtures
for the runtime and frontend workers.

## Base

- Worktree:
  `/Users/liusihang/openwebui/.worktrees/agent-mode-w9b-subagent-control`
- Branch: `codex/agent-mode-w9b-subagent-control`
- Base commit: `28830b966`

## Read-Only Context

- `/Users/liusihang/openwebui/docs/plans/2026-06-17-openwebui-agent-mode-agentscope-implementation.md`
- `/Users/liusihang/openwebui/docs/plans/2026-06-18-openwebui-agent-mode-runtime-contracts.md`
- `/Users/liusihang/openwebui/docs/plans/2026-06-17-openwebui-agent-mode-agentscope-design.md`
- `/Users/liusihang/openwebui/docs/adr/0002-agent-mode-runtime-boundaries.md`
- `/Users/liusihang/openwebui/.worktrees/agent-mode-w9-model-catalog/handoff/agent-mode/w9-model-catalog.md`

## Owned Files

- `backend/open_webui/agent/subagents.py`
- `backend/open_webui/test/agent/test_subagents.py`
- Minimal service binding in `backend/open_webui/routers/agent_service.py` only
  if required to expose model-selection / participant callbacks.
- This handoff.

## Must Not Touch

- `services/agentscope-runtime/*`
- `src/lib/components/chat/Chat.svelte`
- nested `open-terminal/`
- broad `backend/open_webui/utils/middleware.py` or `utils/tools.py` rewrites

## Required First Step

Write failing tests first and record the red command/result here.

Required behavior tests:

- creating subagent participant events includes participant attribution;
- more than 5 subagents is rejected by default;
- each subagent gets its own budget under the aggregate run/team budget;
- model-selection callback uses W9A catalog helper and rejects unauthorized
  explicit model requests;
- `subagent.failed` is evented but does not automatically transition the parent
  run to `failed`;
- emitted fixtures are stable enough for W9B2 and W10A to consume.

## Verification To Record

- Focused pytest:
  `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/agent/test_subagents.py`
- Adjacent gate:
  `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/agent/test_model_catalog.py backend/open_webui/test/agent/test_events.py backend/open_webui/test/agent/test_resources.py backend/open_webui/test/models/test_agent_runs.py`
- Ruff:
  `uv run ruff check backend/open_webui/agent backend/open_webui/routers/agent_service.py backend/open_webui/test/agent`
- `git diff --check`
- Restore `uv.lock` after `uv run` if it changes.

## Progress Log

### 2026-06-18 Red test checkpoint

- Added focused tests in `backend/open_webui/test/agent/test_subagents.py`
  before production code.
- Red command:
  `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/agent/test_subagents.py`
- Red result: expected collection failure because the production helper is
  absent:
  `ModuleNotFoundError: No module named 'open_webui.agent.subagents'`.
- `uv run` created the worker `.venv` and modified `uv.lock`; restore
  `uv.lock` before commit unless a later dependency change is intentionally
  owned by this slice.

### 2026-06-18 Implementation checkpoint

- Implemented `backend/open_webui/agent/subagents.py` with:
  - durable participant/budget updates through an injectable store and a
    default `agent_run` DB adapter;
  - default cap-5 enforcement before model selection or event append;
  - per-subagent step budget reservation capped by aggregate team budget;
  - W9A `AgentModelCatalog` model-selection delegation with requested/completed
    events;
  - `subagent.failed` eventing without parent run state transition;
  - stable subagent event fixtures for W9B2/W10A.
- Added minimal `backend/open_webui/routers/agent_service.py` binding for
  `POST /runs/{run_id}/model-selection`.
- Focused green command:
  `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/agent/test_subagents.py`
- Focused green result after the initial implementation: `7 passed, 2 warnings`.
- Added an extra TDD checkpoint for the service callback error boundary:
  unauthorized W9A explicit-model rejection initially escaped as
  `ModelSelectionNotAllowed`; after adding the router mapping, focused pytest
  returned `8 passed, 2 warnings`.

### 2026-06-18 Verification checkpoint

- Focused pytest rerun:
  `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/agent/test_subagents.py`
  -> `8 passed, 2 warnings`.
- Required adjacent gate command:
  `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/agent/test_model_catalog.py backend/open_webui/test/agent/test_events.py backend/open_webui/test/agent/test_resources.py backend/open_webui/test/models/test_agent_runs.py`
  -> failed with `1 failed, 28 passed, 2 warnings, 6 errors`.
  Failure is the existing combined-import SQLite metadata issue:
  `sqlalchemy.exc.NoReferencedTableError: Foreign key associated with column
  'channel_file.message_id' could not find table 'message'`.
- Isolation evidence:
  - `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/agent/test_model_catalog.py backend/open_webui/test/agent/test_events.py backend/open_webui/test/agent/test_resources.py`
    -> `27 passed, 2 warnings`.
  - `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/models/test_agent_runs.py`
    -> `8 passed, 1 warning`.
  - `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/agent/test_events.py backend/open_webui/test/models/test_agent_runs.py`
    -> `29 passed, 1 warning`.
  - `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/agent/test_resources.py backend/open_webui/test/models/test_agent_runs.py`
    -> `10 passed, 1 warning`.
- Ruff:
  `uv run ruff check backend/open_webui/agent backend/open_webui/routers/agent_service.py backend/open_webui/test/agent`
  -> `All checks passed!`.
- `git diff --check` -> passed.
- Restored `uv.lock` after `uv run` modified it.
