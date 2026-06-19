# OpenWebUI PR7 Agent Tool Registry Rebuild Handoff

## Scope
- Worktree: `/Users/liusihang/.codex/worktrees/pr7-review-security-fixes/openwebui`
- Starting head: `f2eae24bf58019055eaa7a74f4f7bb868c5e6a26`
- Task: backend/runtime-only fix for Agent Mode tool callbacks when run-scoped in-memory registry is missing.
- Do not deploy.

## Problem
- Isolated deploy at exact head `f2eae24bf` passes ordinary Q&A and explicit subagent flows.
- Tool-backed callbacks can fail with `503 {"detail":"Agent tool registry is not configured"}` after losing the run-scoped in-memory registry.
- Persisted `run.tool_access_snapshot['tools']` contains authorized opaque ids, names, types, and schemas, but no callables.

## Plan
1. Add a focused regression in `backend/open_webui/test/agent/test_tool_authority.py` for rebuilding a missing run registry from a stored builtin snapshot.
2. Implement narrow rebuild logic for builtin and terminal tools, keyed by persisted opaque ids and filtered to snapshot-authorized tool names.
3. Cache rebuilt registry into `request.app.state.AGENT_TOOL_REGISTRIES[run_id]`.
4. Run focused tests and commit only scoped files.

## Checkpoints
- 2026-06-20 04:58 CST: Confirmed main checkout is not the correct surface; target worktree is `pr7-review-security-fixes/openwebui` at `f2eae24bf`.
- 2026-06-20 04:58 CST: Existing worktree has unrelated dirty `uv.lock` and untracked `docs/handoff-pr7-codex-style-activity-ui.md`; leave untouched.
- 2026-06-20 05:00 CST: Added focused builtin rebuild regression and verified it fails with `503: Agent tool registry is not configured`.
- 2026-06-20 05:13 CST: Implemented snapshot rebuild for builtin and terminal tools, cached rebuilt registry under `AGENT_TOOL_REGISTRIES[run_id]`, and added route-level callback coverage.

## Evidence To Fill
- Red test command: `uv run pytest backend/open_webui/test/agent/test_tool_authority.py -k rebuilds_missing_builtin_registry -q`
- Green test command: `uv run pytest backend/open_webui/test/agent/test_tool_authority.py backend/open_webui/test/agent/test_agent_run_routes_db_store.py backend/open_webui/test/agent/test_approval.py -q` -> `23 passed`
- Lint/format command: `uv run ruff check backend/open_webui/routers/agent_service.py backend/open_webui/test/agent/test_tool_authority.py backend/open_webui/test/agent/test_agent_run_routes_db_store.py && uv run ruff format --check backend/open_webui/routers/agent_service.py backend/open_webui/test/agent/test_tool_authority.py backend/open_webui/test/agent/test_agent_run_routes_db_store.py`
- Compile command: `python3 -m py_compile backend/open_webui/routers/agent_service.py`
- Commit: final sha reported in task response after amend.
- Residual limitations: Rebuild is intentionally limited to persisted snapshot tools of type `builtin` and `terminal`; other tool types are not guessed/reconstructed.
