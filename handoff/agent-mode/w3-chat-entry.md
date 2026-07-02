# W3 Agent-First Chat Entry Handoff

Date: 2026-06-18

## Goal

Implement the Agent Mode chat-entry skeleton behind deployment config. When
enabled, product chat creates one Agent Run, links the assistant message to
`agent_run_id`, starts the external runtime through a client boundary, and
reports runtime-unavailable as a visible run failure. When disabled, the legacy
chat path remains explicit.

## Base

- Worktree: `/Users/liusihang/openwebui/.worktrees/agent-mode-w3-chat`
- Branch: `codex/agent-mode-w3-chat`
- Base commit: `7f20eb854e6a25f3091268202a98d97acbc8f3a3`

## Owned Files

- `backend/open_webui/main.py`
- `backend/open_webui/config.py`
- `backend/open_webui/agent/runtime_client.py`
- focused backend tests for rollout flag, run creation, runtime failure, and
  internal model-call guard spoofing

## Non-Goals

- Do not implement model/tool execution callbacks.
- Do not implement frontend UI.
- Do not touch nested `open-terminal/`.

## Required First Step

Write failing tests first, record the red command/result here, then implement
the minimum code to pass.

## Checkpoints

### 2026-06-18 Start

- Worker: W3 chat entry.
- Confirmed worktree: `/Users/liusihang/openwebui/.worktrees/agent-mode-w3-chat`.
- Confirmed branch: `codex/agent-mode-w3-chat`.
- `git status --short` at start showed this handoff as untracked:
  `?? handoff/agent-mode/w3-chat-entry.md`.
- Read the existing handoff. The three requested context files were missing at
  the exact specified paths, and a narrow filename search inside this worktree
  found no matching files:
  - `docs/plans/2026-06-17-openwebui-agent-mode-agentscope-implementation.md`
  - `docs/plans/2026-06-18-openwebui-agent-mode-runtime-contracts.md`
  - `docs/adr/0002-agent-mode-runtime-boundaries.md`
- Scope remains the W3-owned files plus focused tests only. Root checkout and
  nested `open-terminal/` are out of scope.
- Next step: inspect existing chat/run/config/runtime boundaries, then write
  failing focused tests before production changes.

### 2026-06-18 Context Correction

- User clarified that the missing planning docs/ADR are root-checkout local
  planning files, not files expected in this PR7-derived worktree.
- Read these root files as read-only context only:
  - `/Users/liusihang/openwebui/docs/plans/2026-06-17-openwebui-agent-mode-agentscope-implementation.md`
  - `/Users/liusihang/openwebui/docs/plans/2026-06-18-openwebui-agent-mode-runtime-contracts.md`
  - `/Users/liusihang/openwebui/docs/adr/0002-agent-mode-runtime-boundaries.md`
  - `/Users/liusihang/openwebui/handoff-openwebui-general-agent-mode-brainstorming-2026-06-17.md`
- W3 requirements confirmed from those docs:
  - `ENABLE_AGENT_MODE=false`: explicitly use legacy chat behavior.
  - `ENABLE_AGENT_MODE=true`: product chat creates one Agent Run and does not
    call provider chat directly.
  - Runtime-unavailable must visibly fail the run, not silently fall back.
  - Assistant message stores `agent_run_id` as the Agent Run Reference.
  - Trusted recursion guard is only `request.state.agent_internal_model_call`;
    user-supplied metadata/headers must not bypass Agent Mode.
- Continue editing only inside this worktree.

### 2026-06-18 Red Tests

- Added focused tests in
  `backend/open_webui/test/agent/test_chat_entry_agent_mode.py`.
- Initial test authoring run exposed an invalid import (`from open_webui import
  main` imported a package-level function, not the module). Corrected the test
  to import `open_webui.main` through `importlib.import_module`.
- Final red command:
  `uv run pytest -q backend/open_webui/test/agent/test_chat_entry_agent_mode.py`
- Final red result:
  - 4 failed, 2 passed, 8 warnings.
  - Missing rollout config:
    `assert 'ENABLE_AGENT_MODE = ConfigVar(' in config_text`.
  - Enabled path did not create an Agent Run:
    `assert len(runs) == 1`, got `0`.
  - Runtime-unavailable path did not create/fail an Agent Run:
    `assert len(runs) == 1`, got `0`.
  - Forged metadata/header internal guard still followed legacy behavior:
    `assert len(runs) == 1`, got `0`.
  - Passing controls: disabled flag uses legacy path, and trusted
    `request.state.agent_internal_model_call = True` uses the legacy model path.
- Next step: implement only enough config, runtime client boundary, and
  `main.py` Agent Mode branch to pass these tests.

### 2026-06-18 Implementation Checkpoint

- Added rollout/runtime config in `backend/open_webui/config.py`:
  `ENABLE_AGENT_MODE`, `AGENT_RUNTIME_BASE_URL`,
  `AGENT_RUNTIME_SERVICE_TOKEN`, `AGENT_RUN_DEFAULT_TIMEOUT_SECONDS`,
  `AGENT_RUN_MAX_MODEL_CALLS`, `AGENT_RUN_MAX_TOOL_CALLS`,
  `AGENT_TEAM_MAX_SUBAGENTS`, and `AGENT_SUBAGENT_DEFAULT_BUDGET`.
- Added `backend/open_webui/agent/runtime_client.py` as the external runtime
  client boundary for `POST /v1/openwebui/runs`.
- Updated `backend/open_webui/main.py` so product chat, after existing
  model/chat/assistant-placeholder setup:
  - keeps the legacy path when `ENABLE_AGENT_MODE` is false;
  - keeps the legacy path only for trusted
    `request.state.agent_internal_model_call`;
  - ignores forged internal headers and user metadata;
  - creates one `AgentRun` when enabled;
  - writes `agent_run_id` to the assistant message;
  - starts the external runtime through `AgentRuntimeClient`;
  - transitions the run to `running` on runtime accept;
  - transitions the run to `failed` and writes assistant-message error content
    on runtime unavailability.
- `uv run` changed `uv.lock` during first environment setup; restored
  `uv.lock` because W3 does not own dependency changes.

### 2026-06-18 Verification

- Focused pytest:
  `uv run pytest -q backend/open_webui/test/agent/test_chat_entry_agent_mode.py`
  - Result: 6 passed, 8 warnings.
- New-file ruff:
  `uv run ruff check backend/open_webui/agent/runtime_client.py backend/open_webui/test/agent/test_chat_entry_agent_mode.py`
  - Result: all checks passed.
- Required touched-file ruff:
  `uv run ruff check backend/open_webui/main.py backend/open_webui/config.py backend/open_webui/agent/runtime_client.py backend/open_webui/test/agent/test_chat_entry_agent_mode.py`
  - Result: failed with 568 errors from existing `main.py`/`config.py` lint
    debt, including import sorting/unused imports, long lines, duplicate
    imports, and existing complexity warnings. New files pass the focused ruff
    command above.
- Whitespace check:
  `git diff --check`
  - Result: passed.

## Verification To Record

- focused pytest for W3 tests
- ruff on touched backend files
- `git diff --check`
