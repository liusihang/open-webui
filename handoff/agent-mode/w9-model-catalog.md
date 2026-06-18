# W9A Model Catalog Handoff

Date: 2026-06-18

## Goal

Implement the Agent Mode model catalog helper for subagent model selection. The
runtime should be able to ask OpenWebUI which models a user/run may use for a
subagent request, and receive permission-filtered choices plus a deterministic
selection when the user request is fuzzy.

## Base

- Worktree:
  `/Users/liusihang/openwebui/.worktrees/agent-mode-w9-model-catalog`
- Branch: `codex/agent-mode-w9-model-catalog`
- Base commit: `1ef1194eeeb224a7d3da8a7697eae9cbb499c157`

## Owned Files

- `backend/open_webui/agent/model_catalog.py`
- focused model catalog tests under `backend/open_webui/test/agent/`
- optional service wrapper under `backend/open_webui/agent/service/` if useful

## Shared-File Constraints

- Do not implement the real AgentScope subagent adapter in this slice.
- Do not edit W7 approval files or W8 terminal artifact/process files.
- Avoid `backend/open_webui/routers/agent_service.py` unless a tiny catalog
  callback shell is essential; prefer helper-level tests first.
- Do not modify model authority behavior unless a bug is proven and documented
  in this handoff.

## Non-Goals

- No frontend UI.
- No subagent spawning loop or AgentScope template work.
- No budget execution accounting beyond catalog response metadata needed by a
  later adapter.

## Required First Step

Write failing tests first and record the red command/result here before
implementing production code.

Required behavior tests:

- catalog filters out models the user/run cannot access;
- fuzzy subagent request can select a permission-valid model;
- explicit unauthorized model request is rejected or omitted with an audit
  warning;
- response includes `meta.agent_selection` with reason, source request, and
  selected model id;
- no nested Agent Run or provider call is made by catalog selection.

## Progress Log

### 2026-06-18 W9A red test checkpoint

- Added focused red tests in
  `backend/open_webui/test/agent/test_model_catalog.py`.
- Red command:
  `uv run pytest backend/open_webui/test/agent/test_model_catalog.py -q`
- Red result: expected collection failure because production helper is absent:
  `ModuleNotFoundError: No module named 'open_webui.agent.model_catalog'`.
- Next checkpoint: implement the minimal helper in
  `backend/open_webui/agent/model_catalog.py` without adding provider calls,
  nested Agent Run creation, W7 approval behavior, or W8 terminal artifacts.

### 2026-06-18 W9A implementation checkpoint

- Implemented `backend/open_webui/agent/model_catalog.py`.
- Helper behavior:
  - loads the current OpenWebUI model catalog for the run user;
  - filters choices by the existing model access checker;
  - rejects explicit unauthorized model requests with an audit warning;
  - selects a deterministic permission-valid model for fuzzy/default requests;
  - returns `meta.agent_selection.reason`, `source_request`, and
    `selected_model_id`;
  - does not create nested Agent Runs or execute provider model calls.
- Green command:
  `uv run pytest backend/open_webui/test/agent/test_model_catalog.py -q`
- Green result: `4 passed, 2 warnings`.

## Verification To Record

- Focused model catalog pytest:
  `uv run pytest backend/open_webui/test/agent/test_model_catalog.py -q`
  -> `4 passed, 2 warnings`.
- Adjacent model authority pytest:
  `uv run pytest backend/open_webui/test/agent/test_model_authority.py -q`
  -> `5 passed, 2 warnings`.
- Ruff on touched backend files:
  `uv run ruff check backend/open_webui/agent/model_catalog.py backend/open_webui/test/agent/test_model_catalog.py`
  -> `All checks passed!`.
- `git diff --check` -> passed after verification and handoff updates.
- `uv.lock` was modified by `uv run` and restored with `git checkout -- uv.lock`.
