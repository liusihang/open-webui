# W4 AgentScope Runtime Service Handoff

Date: 2026-06-18

## Goal

Create a minimal external AgentScope runtime service skeleton that can accept
OpenWebUI run starts, validate service credentials, append `run.running`, expose
health/status, and accept cancellation. Real model/tool execution remains out of
scope for this slice.

## Owned Files

- `services/agentscope-runtime/*`
- Focused service tests for health, run accept, bad credential rejection, and
  cancel behavior

## Non-Goals

- Do not call OpenWebUI model or tool authorities directly beyond callback
  client stubs.
- Do not implement subagent templates yet.
- Do not touch nested `open-terminal/`.

## TDD Requirement

Write failing tests first and record the red failure in this handoff before
implementation.

## Status

- Worktree created from PR #7 at
  `2183a6697c672c60d0137b64d57eca7fdad0b5e6`.
- W4 must verify concrete AgentScope/AgentScope Runtime APIs against a clean
  clone or pinned package before relying on signatures.
- Explorer guidance received:
  - no tracked `services/` tree exists yet;
  - create a service-local `services/agentscope-runtime/` package with its own
    tests and dependencies;
  - keep the runtime skeleton narrow: health, run accept, cancel, status.
- First red tests should cover auth rejection, accepted run creation, cancel,
  status, and callback client request shape.
- Ready for implementation worker dispatch.

## Checkpoint 1: Test-First Red

Goal: define the W4 runtime service skeleton behavior before production code.

Actions:

- Added focused service-local tests under `services/agentscope-runtime/tests/`
  for health, service-token auth, run acceptance, `run.running` callback,
  callback failure surfacing, cancel/status behavior, unknown status, and
  `OpenWebUIClient.append_event()` transport shape.
- Added `services/agentscope-runtime/pyproject.toml` with service-local runtime
  and test dependencies; no root lockfiles edited.

Red command and result:

```bash
cd /Users/liusihang/openwebui/.worktrees/agent-mode-w4-runtime/services/agentscope-runtime
uv run --extra test pytest -q
```

Result: expected red collection failure because the package is not implemented
yet.

```text
ModuleNotFoundError: No module named 'agentscope_runtime'
ERROR tests/test_app.py
ERROR tests/test_openwebui_client.py
```

Earlier environment check:

```bash
pytest -q
```

Result: `/bin/bash: pytest: command not found`, so the valid red run uses
service-local `uv run --extra test`.

## Checkpoint 2: Runtime Skeleton Implemented

Goal: deliver the minimal W4 AgentScope runtime service skeleton without
touching OpenWebUI backend/frontend/storage/open-terminal code.

Implemented:

- `services/agentscope-runtime/agentscope_runtime/app.py`
  - FastAPI app factory.
  - Unauthenticated `GET /health`.
  - Service-token auth for `/v1/openwebui/*` endpoints.
  - `POST /v1/openwebui/runs` creates an in-memory runtime session, returns
    `202`, and synchronously appends `run.running` to OpenWebUI.
  - `POST /v1/openwebui/runs/{run_id}/cancel` marks the in-memory session
    `cancelled` and `cancel_requested=true`; it does not touch terminal
    processes.
  - `GET /v1/openwebui/runs/{run_id}/status` returns session state.
- `services/agentscope-runtime/agentscope_runtime/openwebui_client.py`
  - `OpenWebUIClient.append_event()` sends bearer auth,
    `X-Agent-Idempotency-Key`, and a structured event body containing the same
    idempotency key.
  - Non-2xx callback responses raise `RuntimeError`, and start-run surfaces the
    failure as `502 openwebui_callback_failed`.
- `services/agentscope-runtime/agentscope_runtime/schemas.py`
  - Service-local Pydantic schemas for run start/status and append-event body.
- `services/agentscope-runtime/pyproject.toml`, `uv.lock`, `.gitignore`
  - Service-local package/dependency definition and lockfile; root lockfiles
    were not edited.

Verification commands:

```bash
cd /Users/liusihang/openwebui/.worktrees/agent-mode-w4-runtime/services/agentscope-runtime
uv run --extra test pytest -q
```

Result:

```text
Using CPython 3.12.13
Creating virtual environment at: .venv
Installed 23 packages in 27ms
........                                                                 [100%]
8 passed in 0.72s
```

```bash
cd /Users/liusihang/openwebui/.worktrees/agent-mode-w4-runtime
git diff --check -- services/agentscope-runtime handoff/agent-mode/w4-runtime.md
```

Result: exit 0, no whitespace errors.

```bash
rg -n "agentscope(_runtime)?|AgentApp|ReActAgent" services/agentscope-runtime
```

Result: only service-local package references are present; no upstream
AgentScope or AgentScope Runtime API is imported or called.

Changed files:

- `handoff/agent-mode/w4-runtime.md`
- `services/agentscope-runtime/.gitignore`
- `services/agentscope-runtime/agentscope_runtime/__init__.py`
- `services/agentscope-runtime/agentscope_runtime/app.py`
- `services/agentscope-runtime/agentscope_runtime/openwebui_client.py`
- `services/agentscope-runtime/agentscope_runtime/schemas.py`
- `services/agentscope-runtime/pyproject.toml`
- `services/agentscope-runtime/tests/test_app.py`
- `services/agentscope-runtime/tests/test_openwebui_client.py`
- `services/agentscope-runtime/uv.lock`

API uncertainty notes:

- I read the local AgentScope skill and `references/deployment_guide.md`. The
  guide says AgentScope Runtime provides `AgentApp`, while also allowing a
  custom FastAPI server.
- This W4 skeleton deliberately does not depend on concrete upstream
  AgentScope/AgentScope Runtime signatures. Real AgentScope orchestration
  remains behind the future runtime adapter boundary.
- When W9 or a later runtime worker adds real execution, it should verify a
  clean/pinned `agentscope` and/or `agentscope-runtime` source before importing
  `AgentApp`, session, sandbox, or agent APIs.

Current worktree state summary:

- Only owned W4 paths are changed/untracked: `services/agentscope-runtime/*`
  and `handoff/agent-mode/w4-runtime.md`.
- Local `.venv`, `.pytest_cache`, and `__pycache__` outputs were removed after
  verification.
