# W11 Resource Lifecycle And Compaction Handoff

Date: 2026-06-18

## Goal

Implement Agent Run resource lifecycle and terminal-state compaction helpers.
Terminal states clean run-owned in-memory resources, resolve pending waits,
retain artifact/process metadata, do not kill Open Terminal processes, and
write a compacted user/audit summary that can reconstruct the expandable UI.

## Base

- Worktree: `/Users/liusihang/openwebui/.worktrees/agent-mode-w11-lifecycle`
- Branch: `codex/agent-mode-w11-lifecycle`
- Base commit: `7f20eb854e6a25f3091268202a98d97acbc8f3a3`

## Owned Files

- `backend/open_webui/agent/resources.py`
- `backend/open_webui/agent/compaction.py`
- focused backend tests for heartbeat timeout, cleanup-once semantics,
  retained process refs, tmp cleanup eligibility, and compacted UI data

## Non-Goals

- Do not implement chat entry or model/tool authority.
- Do not kill Open Terminal processes on cancellation.
- Do not touch nested `open-terminal/`.

## Required First Step

Write failing tests first, record the red command/result here, then implement
the minimum code to pass.

## Checkpoints

### 2026-06-18 Start

- Confirmed worktree:
  `/Users/liusihang/openwebui/.worktrees/agent-mode-w11-lifecycle`.
- Confirmed branch: `codex/agent-mode-w11-lifecycle`.
- Root checkout planning docs were read read-only because they are not present
  in this PR7-derived worktree:
  - `/Users/liusihang/openwebui/docs/plans/2026-06-17-openwebui-agent-mode-agentscope-implementation.md`
  - `/Users/liusihang/openwebui/docs/plans/2026-06-18-openwebui-agent-mode-runtime-contracts.md`
  - `/Users/liusihang/openwebui/docs/adr/0002-agent-mode-runtime-boundaries.md`
  - `/Users/liusihang/openwebui/handoff-openwebui-general-agent-mode-brainstorming-2026-06-17.md`
- W11 boundaries confirmed: implement only run-owned resource lifecycle,
  heartbeat timeout, terminal-state cleanup, tmp cleanup eligibility, and
  compaction helpers. Do not implement chat entry, model/tool authority, or
  nested `open-terminal/` changes.
- Next checkpoint: write focused failing backend tests before production code.

## Red Tests

- Command:
  `uv run pytest -q backend/open_webui/test/agent/test_resources.py backend/open_webui/test/agent/test_compaction.py`
- Result: RED as expected.
- Failure:
  - `ModuleNotFoundError: No module named 'open_webui.agent.resources'`
  - `ModuleNotFoundError: No module named 'open_webui.agent.compaction'`
- Note: first `uv run` created the worktree `.venv` and modified `uv.lock`.
  Treat the `uv.lock` change as environment churn and restore or leave
  unstaged before final status.

## Implementation Notes

- Added `backend/open_webui/agent/resources.py`.
  - `AgentRunResourceManager` tracks run-owned in-memory resources by
    `run_id/resource_type/resource_key`.
  - Terminal cleanup closes run-owned MCP/session-style resources once,
    resolves pending approval waits with the terminal result, stops SSE tails,
    and runs the compaction callback once.
  - Open Terminal process refs are retained and their optional `kill` callback
    is intentionally not called by default.
  - Stale runtime heartbeat sweep marks non-terminal runs `failed` with
    `agent_runtime_lost`, then runs cleanup once.
- Added `backend/open_webui/agent/compaction.py`.
  - Builds compacted `agent_run.summary` data from durable run/event/artifact
    inputs.
  - Retains expandable UI data for actions, tool results, approvals,
    subagents, artifacts, process refs, budget, errors, and warnings.
  - Prunes noise such as final token deltas, runtime heartbeats, and
    intermediate start/progress events from long-term UI summary.
  - Marks only `/workspace/agent-runs/<run_id>/tmp/*` artifacts as
    cleanup-eligible after 7 days; outputs are not auto-cleaned.
- Added focused tests:
  - `backend/open_webui/test/agent/test_resources.py`
  - `backend/open_webui/test/agent/test_compaction.py`
- `uv.lock` churn from initial `uv run` was restored.

## Verification To Record

- focused pytest for W11 tests
- ruff on touched backend files
- `git diff --check`

## Verification Results

- GREEN:
  `uv run pytest -q backend/open_webui/test/agent/test_resources.py backend/open_webui/test/agent/test_compaction.py`
  - `4 passed in 0.09s`
- GREEN broader focused backend group:
  `uv run pytest -q backend/open_webui/test/agent/test_events.py backend/open_webui/test/agent/test_resources.py backend/open_webui/test/agent/test_compaction.py backend/open_webui/test/models/test_agent_runs.py`
  - `33 passed, 1 warning in 11.30s`
  - Warning: existing SQLAlchemy `declarative_base()` deprecation warning.
- GREEN ruff:
  `uv run ruff check backend/open_webui/agent/resources.py backend/open_webui/agent/compaction.py backend/open_webui/test/agent/test_resources.py backend/open_webui/test/agent/test_compaction.py`
  - `All checks passed!`
- GREEN:
  `git diff --check`
