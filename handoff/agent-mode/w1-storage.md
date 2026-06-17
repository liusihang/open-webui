# W1 Agent Run Storage Handoff

Date: 2026-06-18

## Goal

Implement the storage foundation for Agent Mode: `agent_run`,
`agent_run_event`, `agent_artifact`, `agent_run_operation`, state transitions,
event sequencing, artifact registration, and idempotency ledger.

## Owned Files

- `backend/open_webui/models/agent_runs.py`
- Agent-run Alembic migration under `backend/open_webui/migrations/versions/`
- Focused backend tests for storage/state/idempotency/artifacts

## Non-Goals

- Do not edit `backend/open_webui/main.py`.
- Do not implement SSE, tool callbacks, model callbacks, or frontend UI.
- Do not touch nested `open-terminal/`.

## TDD Requirement

Write failing tests first and record the red failure in this handoff before
implementation.

## Status

- Worktree created from PR #7 at
  `2183a6697c672c60d0137b64d57eca7fdad0b5e6`.
- Explorer guidance received:
  - mirror `automations.py`, `calendar.py`, `chat_messages.py`,
    `knowledge_layers.py` for storage/migration style;
  - keep `agent_run_operation` as an explicit idempotency ledger;
  - use `time.time_ns()` and migration parity with the table definitions.
- First red tests should cover creation, legal/illegal state transitions,
  monotonic event seq, idempotent artifact registration, and request-hash
  conflicts.
- 2026-06-18 W1 checkpoint:
  - Verified active worktree branch: `codex/agent-mode-w1-storage`.
  - Verified Alembic current head in this worktree with
    `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run python ...`:
    `e2f3a4b5c7`.
  - Earlier explorer note that the head was likely `461111b60977` is stale for
    this worktree.

## Tests Written First

- Added `backend/open_webui/test/models/test_agent_runs.py`.
- RED command:
  `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/models/test_agent_runs.py`
- RED result:
  - Exit code: 2 during collection.
  - Expected failure: `ModuleNotFoundError: No module named
    'open_webui.models.agent_runs'`.
  - This confirms the storage tests were written before the production model
    module exists.

## Implementation Checkpoints

- Completed: implemented `backend/open_webui/models/agent_runs.py`.
- Completed: added Alembic migration
  `backend/open_webui/migrations/versions/d6e7f8a9b0c1_add_agent_run_tables.py`
  under current head `e2f3a4b5c7`.
- Completed: updated migration graph test to make `d6e7f8a9b0c1` the new
  single head.
- Completed: added helper methods expected by W2's injectable event store:
  `get_run_state`, `has_final_started`, `list_events_after`, and
  `append_final_text_delta`.

## Verification

- `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/models/test_agent_runs.py backend/open_webui/test/util/test_migration_revision_graph.py`
  - Result: `11 passed, 1 warning`.
- `git diff --check`
  - Result: passed.

## Downstream Interface Notes

- Proposed storage entry point for downstream workers:
  `from open_webui.models.agent_runs import AgentRuns`.
- Proposed domain errors expose a stable `.code` such as
  `invalid_state_transition` and `idempotency_conflict` for router HTTP
  mapping.
- `AgentRunTable` exposes the W2 store surface:
  - `get_run_state(run_id)`
  - `has_final_started(run_id)`
  - `append_event(...)`
  - `list_events_after(run_id, after_seq=0)`
  - `append_final_text_delta(run_id, final_stream_id, delta_index, delta)`
- Artifact ORM uses `meta = Column("metadata", JSON, ...)` because SQLAlchemy
  reserves the declarative attribute name `metadata`; `AgentArtifactModel`
  still exposes the API field as `metadata`.
