# W7 Destructive Approval Handoff

Date: 2026-06-18

## Goal

Implement the Agent Mode destructive-action classifier and approval gate. Normal
read-only tool calls should execute without approval; delete/overwrite style
actions must pause the run in `waiting_approval`, emit approval events, and
resume or reject with a normalized tool result.

## Base

- Worktree:
  `/Users/liusihang/openwebui/.worktrees/agent-mode-w7-approval`
- Branch: `codex/agent-mode-w7-approval`
- Base commit: `056560b2f965b54861f7c0bc88ebce84c9a36e13`

## Owned Files

- `backend/open_webui/agent/destructive.py`
- `backend/open_webui/agent/approval.py` if a separate approval state helper is
  useful
- focused approval tests under `backend/open_webui/test/agent/`
- minimal approval endpoint wiring in `backend/open_webui/routers/agent_service.py`

## Shared-File Constraints

- Do not edit W8-owned terminal artifact/process helpers.
- Do not edit model authority files.
- Keep `backend/open_webui/routers/agent_service.py` changes limited to approval
  request/decision endpoints or dependency getters.
- Prefer new `backend/open_webui/agent/*` helpers over broad changes in
  middleware or tool execution internals.

## Non-Goals

- No frontend `Chat.svelte` work.
- No real Open Terminal process changes.
- No standalone task-center or detached-run behavior.
- No raw reasoning UI or debug trace exposure.

## Required First Step

Write failing tests first and record the red command/result here before
implementing production code.

Required behavior tests:

- read-only tool calls bypass approval;
- delete/overwrite style tool calls return/emit `approval_required` and move the
  run to `waiting_approval`;
- approval resume returns a normal tool result and returns the run to `running`;
- approval rejection returns a normalized `approval_rejected` tool result;
- approval decisions are idempotent and conflict-safe.

## Progress

### 2026-06-18 Checkpoint 1 - scope/read-in

- Confirmed worktree:
  `/Users/liusihang/openwebui/.worktrees/agent-mode-w7-approval`
- Confirmed branch: `codex/agent-mode-w7-approval`
- Current HEAD: `056560b2f965b54861f7c0bc88ebce84c9a36e13`
- Existing tool authority already normalizes tool results and caches completed
  `tool.call` operations, but executes the callable immediately after claiming
  the operation.
- Existing run storage already supports legal `running -> waiting_approval ->
  running` transitions and approval event types.
- Planned W7 seam: add a small destructive classifier and approval coordinator;
  wire the service `tool-call` callback so read-only calls continue directly and
  destructive calls create/return an approval wait before executing.

### 2026-06-18 Checkpoint 2 - red tests

- Added focused approval tests in
  `backend/open_webui/test/agent/test_approval.py`.
- Red command:
  `uv run pytest -q backend/open_webui/test/agent/test_approval.py`
- Red result:
  - Exit code: 2
  - Failure: `ModuleNotFoundError: No module named 'open_webui.agent.approval'`
  - This is the expected pre-implementation failure because the W7 approval
    helper module has not been created yet.
- First `uv run` in this worktree modified `uv.lock`; treat it as environment
  churn and restore before completion.

### 2026-06-18 Checkpoint 3 - implementation

- Added `backend/open_webui/agent/destructive.py`.
  - Classifies read-only calls as approval-free.
  - Flags direct delete/remove tool names, write/replace/upload/apply-patch
    tool names, explicit destructive `operation`/`action` arguments, and
    obvious shell delete/overwrite patterns in `run_command`.
- Added `backend/open_webui/agent/approval.py`.
  - Creates deterministic approval ids as
    `approval:<run_id>:<tool_call_id>`.
  - Stores approval-request and approval-result side effects through the
    existing operation ledger to avoid duplicate transitions/events.
  - Moves `running -> waiting_approval` on destructive requests and emits
    `approval.requested`.
  - Moves `waiting_approval -> running` on approval decisions and emits
    `approval.completed`.
  - Executes the saved resume callback only after approval, or returns a
    normalized `approval_rejected` tool result on rejection.
- Updated `backend/open_webui/routers/agent_service.py`.
  - `tool-call` now asks the approval coordinator before executing tools.
  - Added approval coordinator dependency.
  - Added a minimal approval decision endpoint for
    `/runs/{run_id}/approvals/{approval_id}/decision`.
- Focused green command:
  `uv run pytest -q backend/open_webui/test/agent/test_approval.py`
- Focused green result:
  - Exit code: 0
  - `5 passed, 2 warnings`

## Verification To Record

- focused approval pytest;
- adjacent authority/resource pytest;
- ruff on touched backend files;
- `git diff --check`;
- note whether `uv.lock` was restored after `uv run`.

## Verification Results

### Focused Approval Pytest

Command:

```bash
uv run pytest -q backend/open_webui/test/agent/test_approval.py
```

Result:

- Exit code: 0
- `5 passed, 2 warnings`

### Adjacent Authority/Resource Pytest

Command:

```bash
uv run pytest -q backend/open_webui/test/agent/test_approval.py backend/open_webui/test/agent/test_tool_authority.py backend/open_webui/test/agent/test_resources.py
```

Result:

- Exit code: 0
- `11 passed, 2 warnings`

### Ruff

Command:

```bash
uv run ruff check backend/open_webui/agent/destructive.py backend/open_webui/agent/approval.py backend/open_webui/routers/agent_service.py backend/open_webui/test/agent/test_approval.py
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

- `uv run` modified `uv.lock` during first environment setup.
- Restored `uv.lock`; it is not part of W7.

### Final Pre-Commit Rerun

- Re-ran the focused approval pytest after the handoff update:
  `5 passed, 2 warnings`.
- Re-ran the adjacent approval/tool-authority/resources pytest:
  `11 passed, 2 warnings`.
- Re-ran touched-file ruff: `All checks passed!`.
- Re-ran `git diff --check`: exit code 0.
