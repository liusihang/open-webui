# OnlyOffice Terminal Edit Writeback Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable real OnlyOffice editing for terminal-backed Office files while preserving the existing OpenWebUI -> terminals read path.

**Architecture:** Keep the current terminal file proxy for document fetches. Add terminal-only edit session support, signed callback context, callback-driven saveback into the terminal file path, then enable the terminal preview UI to request editable sessions.

**Tech Stack:** FastAPI, aiohttp, PyJWT, Svelte, pytest

---

## Implemented Scope

### Task 1: Isolated Worktree

Status: completed

- Worktree: `/Users/liusihang/openwebui/.worktrees/onlyoffice-terminal-edit-writeback`
- Branch: `codex/onlyoffice-terminal-edit-writeback`
- Base: `origin/codex/merge-v0.8.12` at `654bf1162`

### Task 2: Terminal Edit Session Enablement

Status: completed

- Terminal-backed OnlyOffice sessions can request `mode="edit"`.
- Uploaded-file sessions remain read-only.
- Callback context token includes terminal file identity and session discriminator.

### Task 3: Terminal Callback Writeback

Status: completed

- Save statuses `2` and `6` trigger callback download and terminal writeback.
- Replace flow uses backup-first move semantics:
  - move original to backup
  - move uploaded replacement into original path
  - delete backup after success
- Non-save callbacks still ack.
- Transport failures map to `502`.

### Task 4: Terminal Preview UI Enablement

Status: completed

- Terminal-backed FileNav OnlyOffice preview now opens editable sessions.
- Uploaded-file preview path remains read-only.

### Task 5: Verification And Handoff

Status: partial

- Focused backend verification completed.
- Focused frontend static verification completed.
- Real end-to-end save against the deployed terminals service still pending.

## Verification Record

- `PYTHONPATH=backend /Users/liusihang/openwebui/.venv/bin/pytest backend/open_webui/test/apps/webui/routers/test_onlyoffice_terminal_writeback.py backend/open_webui/test/util/test_terminal_tool_resolution.py backend/open_webui/test/util/test_terminal_ws_proxy.py -q`
  - Result: `14 passed`
- `npx svelte-check --workspace src/lib/components/chat/FileNav --no-tsconfig --diagnostic-sources "svelte" --threshold error`
  - Result: `0 errors`

## Commits Produced

- `8d8c5fd47` `feat: add onlyoffice terminal callback writeback flow`
- `4fd459ba2` `fix: harden onlyoffice terminal saveback reliability`
- `556e454f4` `test: cover onlyoffice terminal save callback status 6`
- `ba8d5c333` `fix: harden terminal callback ack and transport errors`
- `a5ec122c0` `feat: enable terminal-only onlyoffice edit in file preview`

## Remaining Risk

- The saveback path is unit-tested against mocked terminal `upload/move/delete` behavior only.
- Before enabling this broadly in a live environment, run one real edit/save cycle against a terminal-backed Office file and verify the file content changes on disk.
