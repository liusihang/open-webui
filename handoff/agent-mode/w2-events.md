# W2 Agent Event Stream Handoff

Date: 2026-06-18

## Goal

Implement Agent Run protocol/event helpers, user-facing run APIs, SSE backfill,
final-answer delta accumulation, and final-answer phase ordering against the W1
storage contract.

## Owned Files

- `backend/open_webui/agent/protocol.py`
- `backend/open_webui/agent/events.py`
- `backend/open_webui/routers/agent_runs.py`
- Focused backend tests for event append/list/SSE/final deltas

## Non-Goals

- Do not edit `backend/open_webui/main.py`.
- Do not implement model/tool execution authorities.
- Do not implement frontend UI.
- Do not touch nested `open-terminal/`.

## TDD Requirement

Write failing tests first and record the red failure in this handoff before
implementation.

## Status

- Worktree created from PR #7 at
  `2183a6697c672c60d0137b64d57eca7fdad0b5e6`.
- Explorer guidance received:
  - legacy socket/chat paths live in `main.py`, `utils/middleware.py`, and
    `socket/main.py`;
  - W2 should avoid `get_event_emitter` as the primary truth source;
  - keep persist-before-relay, `Last-Event-ID` / `after_seq`, and final-phase
    gating in `agent/events.py`.
- First red tests should cover monotonic event seq, SSE backfill, final delta
  gating, idempotent duplicate final delta, and no post-final tool/subagent
  events.
- Added first focused tests in
  `backend/open_webui/test/agent/test_events.py`.

## Red Test Record

- Command attempted first: `pytest backend/open_webui/test/agent/test_events.py -q`
  - Result: failed before collection because this worktree shell does not have
    bare `pytest` on `PATH` (`bash: pytest: command not found`).
- Effective red command:
  `uv run pytest backend/open_webui/test/agent/test_events.py -q`
  - Result: failed during collection with the expected missing implementation:
    `ModuleNotFoundError: No module named 'open_webui.agent'`.
  - This establishes tests were written before the W2 protocol/event modules.

## Implementation Checkpoints

- [x] Read required implementation plan, runtime contracts, ADR, and this
  handoff.
- [x] Write failing tests first for monotonic/list behavior, SSE formatting,
  finalizing-only deltas, duplicate/gap delta semantics, post-final event
  gating, and no socket emitter dependency.
- [x] Add protocol schemas and pure event helpers around an injectable storage
  protocol.
- [x] Add SSE backfill helpers and thin route shells without `main.py`
  registration changes.
- [x] Run focused verification and document W1/W2 storage interface.

## Changed Files

- `backend/open_webui/agent/__init__.py`
- `backend/open_webui/agent/protocol.py`
- `backend/open_webui/agent/events.py`
- `backend/open_webui/routers/agent_runs.py`
- `backend/open_webui/routers/agent_service.py`
- `backend/open_webui/test/agent/test_events.py`

## Verification

- `uv run pytest backend/open_webui/test/agent/test_events.py -q`
  - Result: `21 passed`
- `uv run ruff check backend/open_webui/agent backend/open_webui/routers/agent_runs.py backend/open_webui/routers/agent_service.py backend/open_webui/test/agent/test_events.py`
  - Result: `All checks passed!`

## W1/W2 Storage Interface

W2 now expects W1 to provide an injectable store object with:

- `get_run_state(run_id) -> AgentRunState`
- `has_final_started(run_id) -> bool`
- `append_event(AgentEventAppend) -> AgentRunEvent`
- `list_events_after(run_id, after_seq=0) -> list[AgentRunEvent]`
- `append_final_text_delta(run_id, final_stream_id, delta_index, delta) -> str`
- optional `get_run(run_id)` for richer detail routes

W2 reads the store from `request.app.state.AGENT_EVENT_STORE` first, then
`request.app.state.agent_event_store`.

## Notes

- `uv run` rewrote `uv.lock`; it was restored so no lockfile churn remains in
  this slice.
- The current route shell is intentionally thin and does not register itself in
  `main.py`.
