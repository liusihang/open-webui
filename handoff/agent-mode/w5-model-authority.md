# W5 Model Authority Handoff

Date: 2026-06-18

## Goal

Implement the Agent Mode Model Execution Authority callback. AgentScope may
request model calls for a participant, but OpenWebUI validates the run/user/model
authority, uses the trusted `request.state.agent_internal_model_call` guard, and
reuses OpenWebUI model/provider routing without creating nested Agent Runs.

## Base

- Worktree:
  `/Users/liusihang/openwebui/.worktrees/agent-mode-w5-model-authority`
- Branch: `codex/agent-mode-w5-model-authority`
- Base commit: `0f19ffe78c583943a314cfaa9a36aba6691a7057`

## Owned Files

- `backend/open_webui/agent/model_authority.py`
- `backend/open_webui/agent/service/*model*`
- minimal endpoint wiring in `backend/open_webui/routers/agent_service.py`
- focused backend tests for no nested run, unauthorized model rejection, forged
  guard rejection, and audit metadata

## Non-Goals

- Do not modify `backend/open_webui/main.py` except if a tiny bug is impossible
  to avoid and is documented first.
- Do not implement tool authority, destructive approval, or frontend UI.
- Do not touch nested `open-terminal/`.

## Required First Step

Write failing tests first, record the red command/result here, then implement
the minimum code to pass.

## Checkpoints

### 2026-06-18 02:26 CST - Start

- Confirmed worktree branch: `codex/agent-mode-w5-model-authority`.
- Root planning docs read as read-only:
  - `/Users/liusihang/openwebui/docs/plans/2026-06-17-openwebui-agent-mode-agentscope-implementation.md`
  - `/Users/liusihang/openwebui/docs/plans/2026-06-18-openwebui-agent-mode-runtime-contracts.md`
  - `/Users/liusihang/openwebui/docs/adr/0002-agent-mode-runtime-boundaries.md`
  - `/Users/liusihang/openwebui/handoff-openwebui-general-agent-mode-brainstorming-2026-06-17.md`
- W5 scope is limited to model-call authority/service wiring and focused tests.
- Next action: inspect the W3 guard shape and service router shell, then write
  red tests for no nested run, unauthorized model rejection, forged guard
  rejection, and audit metadata.

### 2026-06-18 02:32 CST - Red tests

- Added focused W5 tests in
  `backend/open_webui/test/agent/test_model_authority.py`.
- Red command:
  `uv run pytest -q backend/open_webui/test/agent/test_model_authority.py`
- Red result:
  collection failed with
  `ModuleNotFoundError: No module named 'open_webui.agent.model_authority'`.
  This is the expected missing W5 implementation surface before adding
  `AgentModelAuthority`, the service handler, and `/model-call` router wiring.

### 2026-06-18 02:43 CST - Implementation and verification

- Implemented `AgentModelAuthority` in
  `backend/open_webui/agent/model_authority.py`.
- Added service wrapper
  `backend/open_webui/agent/service/model_call.py`.
- Added `/runs/{run_id}/model-call` wiring to
  `backend/open_webui/routers/agent_service.py`.
- Green command:
  `uv run pytest -q backend/open_webui/test/agent/test_model_authority.py`
- Green result: `5 passed, 2 warnings`.
- Ruff command:
  `uv run ruff check backend/open_webui/agent/model_authority.py backend/open_webui/agent/service/model_call.py backend/open_webui/routers/agent_service.py backend/open_webui/test/agent/test_model_authority.py`
- Ruff result: `All checks passed!`.
- Adjacent guard command:
  `uv run pytest -q backend/open_webui/test/agent/test_model_authority.py backend/open_webui/test/agent/test_chat_entry_agent_mode.py`
- Adjacent guard result: `11 passed, 19 warnings`.
- `uv run` rewrote `uv.lock`; restored `uv.lock` per task instruction.
- Diff check command: `git diff --check`
- Diff check result: passed with no output.
- Staged diff check command: `git diff --cached --check`
- Staged diff check result: passed with no output.

## Verification To Record

- focused pytest for W5 tests
- ruff on touched backend files
- `git diff --check`
