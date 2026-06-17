# W6 Tool Authority Handoff

Date: 2026-06-18

## Goal

Implement the Tool Access Envelope and AgentScope tool-call callback surface.
OpenWebUI remains the Tool Execution Authority: AgentScope receives schemas and
opaque tool ids only, while callables, credentials, MCP sessions, terminal
headers, permissions, idempotency, and normalized results stay inside
OpenWebUI.

## Base

- Worktree: `/Users/liusihang/openwebui/.worktrees/agent-mode-w6-tools`
- Branch: `codex/agent-mode-w6-tools`
- Base commit: `7f20eb854e6a25f3091268202a98d97acbc8f3a3`

## Owned Files

- `backend/open_webui/agent/tool_authority.py`
- `backend/open_webui/agent/service/*tool*`
- scoped changes in `backend/open_webui/utils/tools.py`
- minimal endpoint wiring in `backend/open_webui/routers/agent_service.py`
- focused backend tests for normalized tool results and idempotent execution

## Non-Goals

- Do not implement destructive approval classification; return
  `approval_required` only through a stub/interface if needed.
- Do not modify `backend/open_webui/main.py`.
- Do not touch nested `open-terminal/`.

## Required First Step

Write failing tests first, record the red command/result here, then implement
the minimum code to pass.

## Checkpoints

### 2026-06-18 02:06:51 CST - Start

- Confirmed worktree: `/Users/liusihang/openwebui/.worktrees/agent-mode-w6-tools`
- Confirmed branch: `codex/agent-mode-w6-tools`
- Confirmed HEAD: `7f20eb854`
- Read available W6 handoff file before code exploration.
- Attempted to read the three named context files under this worktree, but they
  were not present:
  - `docs/plans/2026-06-17-openwebui-agent-mode-agentscope-implementation.md`
  - `docs/plans/2026-06-18-openwebui-agent-mode-runtime-contracts.md`
  - `docs/adr/0002-agent-mode-runtime-boundaries.md`
- `rg --files` found only agent-mode handoff files for this branch; no matching
  `docs/plans` or `docs/adr` context files were present.

### 2026-06-18 - Context Correction

- User clarified the planning docs/ADR are read-only context in the root
  checkout, not in this PR7-derived W6 worktree.
- Read these root checkout files as context only:
  - `/Users/liusihang/openwebui/docs/plans/2026-06-17-openwebui-agent-mode-agentscope-implementation.md`
  - `/Users/liusihang/openwebui/docs/plans/2026-06-18-openwebui-agent-mode-runtime-contracts.md`
  - `/Users/liusihang/openwebui/docs/adr/0002-agent-mode-runtime-boundaries.md`
  - `/Users/liusihang/openwebui/handoff-openwebui-general-agent-mode-brainstorming-2026-06-17.md`
- Continuing with edits only in this worktree.

## Verification To Record

- focused pytest for W6 tests
- ruff on touched backend files
- `git diff --check`

## Tests Added First

### Red Run - 2026-06-18

Command:

```bash
WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/agent/test_tool_authority.py
```

Result:

- Exit code: 2
- Failure:
  `ModuleNotFoundError: No module named 'open_webui.agent.tool_authority'`
- This is the expected red failure because the W6 tool authority module and
  callback foundation have not been implemented yet.

## Implementation Checkpoints

### Tool Authority Foundation

- Added `backend/open_webui/agent/tool_authority.py`.
- Added `build_tool_access_envelope(...)` that returns AgentScope-visible tool
  schema entries with opaque ids while keeping callables in a server-side
  registry.
- Added normalized tool result conversion with the contract fields:
  `status`, `content`, `files`, `embeds`, `sources`, `artifacts`,
  `process_refs`, `warnings`, `structured_error`, and `raw`.
- Added terminal `run_command` process-ref extraction for results carrying
  `process_id`.
- Added `AgentToolAuthority.execute_tool_call(...)`, which claims
  `agent_run_operation` before execution, replays successful cached responses,
  rejects request-hash conflicts through the W1 ledger, and stores normalized
  responses after execution.

### Service Wiring

- Added `backend/open_webui/agent/service/tool_call.py` as the domain service
  handler for tool-call callbacks.
- Added `POST /api/agent/service/runs/{run_id}/tool-call` wiring in
  `backend/open_webui/routers/agent_service.py`.
- The endpoint uses the same `X-Agent-Idempotency-Key` / body
  `idempotency_key` matching helper as the existing event and final-delta
  callbacks.
- The endpoint requires OpenWebUI to provide `AGENT_TOOL_AUTHORITY` or
  `AGENT_TOOL_REGISTRY` in app state; no callables or credentials are exposed
  to AgentScope.

## Verification Results

### Focused Pytest

Command:

```bash
WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/agent/test_tool_authority.py backend/open_webui/test/agent/test_events.py backend/open_webui/test/models/test_agent_runs.py
```

Result:

- Exit code: 0
- `33 passed, 1 warning`

### Ruff

Command:

```bash
uv run ruff check backend/open_webui/agent/tool_authority.py backend/open_webui/agent/service/tool_call.py backend/open_webui/routers/agent_service.py backend/open_webui/test/agent/test_tool_authority.py
```

Result:

- Exit code: 0
- `All checks passed!`

### Diff Check

Command:

```bash
git diff --check
```

Result:

- Exit code: 0

### Lockfile Note

- `uv run` rewrote `uv.lock` during initial environment setup.
- Restored `uv.lock`; it is not part of this W6 change.
