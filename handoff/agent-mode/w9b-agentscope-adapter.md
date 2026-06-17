# W9B2 AgentScope Runtime Subagent Adapter Handoff

Date: 2026-06-18

## Goal

Implement the AgentScope runtime-side single-level leader -> subagent adapter
on top of integration commit `28830b966`.

This worker owns the external runtime service package and must verify concrete
AgentScope APIs from a clean clone or pinned commit before coding subagent
internals.

## Base

- Worktree:
  `/Users/liusihang/openwebui/.worktrees/agent-mode-w9b-agentscope-adapter`
- Branch: `codex/agent-mode-w9b-agentscope-adapter`
- Base commit: `28830b966`

## Read-Only Context

- `/Users/liusihang/openwebui/docs/plans/2026-06-17-openwebui-agent-mode-agentscope-implementation.md`
- `/Users/liusihang/openwebui/docs/plans/2026-06-18-openwebui-agent-mode-runtime-contracts.md`
- `/Users/liusihang/openwebui/docs/plans/2026-06-17-openwebui-agent-mode-agentscope-design.md`
- `/Users/liusihang/openwebui/docs/adr/0002-agent-mode-runtime-boundaries.md`
- W9B1 interface may be absent at dispatch. If it is absent, first perform API
  verification and build against an explicit local protocol shim; record the
  expected callback contract here.

## Owned Files

- `services/agentscope-runtime/*`
- service-local tests under `services/agentscope-runtime/tests/`
- This handoff.

## Must Not Touch

- `backend/open_webui/agent/*` except through documented callback schemas
- `backend/open_webui/routers/agent_service.py`
- frontend files
- nested `open-terminal/`

## Required First Step

Verify AgentScope APIs before implementation. Use a clean clone or pinned
commit, not the divergent local skill clone. Record:

- clone path or pinned commit;
- files/classes/examples read;
- exact APIs used for leader/subagent orchestration;
- any API gaps and local adapter decisions.

Then write failing service-local tests first and record the red command/result.

Required behavior tests:

- runtime creates at most one level of subagents;
- cap 5 is enforced through the OpenWebUI callback contract;
- subagent model selection goes through OpenWebUI callbacks, not direct provider
  credentials;
- subagent completion and failure emit events with participant attribution;
- cancellation stops the AgentScope loop but does not kill Open Terminal
  processes;
- no OpenWebUI user JWT, provider key, MCP credential, terminal key, or raw tool
  server secret is accepted by the runtime.

## Verification To Record

- AgentScope API verification note with concrete source paths and commit.
- Service-local tests from `services/agentscope-runtime`.
- `git diff --check`
- Restore dependency/lockfile churn unless this worker intentionally owns it.

## Progress Log

- 2026-06-18 03:17 CST: Started W9B2 in assigned worktree on branch
  `codex/agent-mode-w9b-agentscope-adapter`.
- Read required handoff and read-only root docs:
  - `docs/plans/2026-06-17-openwebui-agent-mode-agentscope-implementation.md`
  - `docs/plans/2026-06-18-openwebui-agent-mode-runtime-contracts.md`
  - `docs/plans/2026-06-17-openwebui-agent-mode-agentscope-design.md`
  - `docs/adr/0002-agent-mode-runtime-boundaries.md`
- Confirmed current runtime service is still the W4 skeleton: health,
  run accept, cancel/status, and append-event callback only.
- Confirmed current backend service router has event, final-delta,
  model-call, tool-call, and approval callbacks, but no runtime-facing
  `/model-selection` or `/subagents` callback binding yet. W9B2 will build
  against an explicit local protocol shim and will not edit backend files.

## AgentScope API Verification

- Clean clone path:
  `/tmp/agentscope-w9b2.UJKsxp/agentscope`
- Source:
  `https://github.com/agentscope-ai/agentscope.git`
- Verified commit:
  `c13c3effcb568ef915cbbd0fe900df2f2b9b003c`
- Verified source files and examples read:
  - `src/agentscope/app/_types.py`
  - `src/agentscope/app/_tools/_agent_create.py`
  - `src/agentscope/app/_tools/_team_create.py`
  - `src/agentscope/app/_tools/_team_say.py`
  - `src/agentscope/app/_service/_toolkit.py`
  - `src/agentscope/app/_app.py`
  - `src/agentscope/app/_service/_chat.py`
  - `src/agentscope/agent/_agent.py`
  - `src/agentscope/agent/_config.py`
  - `src/agentscope/model/_base.py`
  - `src/agentscope/model/_model_response.py`
  - `src/agentscope/message/_base.py`
  - `src/agentscope/tool/_base.py`
  - `src/agentscope/tool/_response.py`
  - `examples/agent_service/main.py`
- Concrete APIs verified:
  - `agentscope.app.SubAgentTemplate` is the serializable subagent blueprint
    used by `create_app(custom_subagent_templates=[...])`.
  - Built-in team tools are `TeamCreate`, `AgentCreate`, `TeamSay`, and
    `TeamDelete`, attached by `get_toolkit`; workers get only `TeamSay` when
    `agent_record.source == "team"`.
  - `AgentCreate.__call__(name, description, prompt, subagent_type="default",
    _agent_state=None)` creates a worker `AgentRecord`, worker session, and
    initial inbox message, then wakes the worker through `MessageBus`.
  - `Agent` requires a `ChatModelBase`; `ChatModelBase._call_api(...)` is the
    custom model boundary if OpenWebUI callbacks later need to back an
    AgentScope model without provider credentials.
  - `Msg` uses `TextBlock` content, and `ChatResponse` carries text/tool/data
    blocks with `is_last`.
  - `ToolBase` is the custom tool boundary, and `ToolResponse`/`ToolChunk`
    are the normalized AgentScope tool return containers.
- API gaps and local adapter decisions:
  - AgentScope's stock team tools can create teams/subagents, but they do not
    encode OpenWebUI's MVP constraints by themselves: single-level
    `leader -> subagent`, default cap 5, OpenWebUI model-selection authority,
    OpenWebUI tool authority, and no raw credentials in runtime.
  - W9B2 will therefore implement a runtime-side adapter boundary that maps
    a leader subagent intent to OpenWebUI callback calls and events. It will
    not directly expose AgentScope's unconstrained team tools to OpenWebUI.

## Expected Local Callback Shim

Until W9B1 provides concrete backend bindings, W9B2 expects these callbacks:

- `POST /api/agent/service/runs/{run_id}/subagents`
  - idempotency key: `subagent:<run_id>:<participant_id>:create`
  - request: `run_id`, `parent_participant_id`, `participant_id`, `name`,
    `description`, `task`, `budget`, `metadata`, `idempotency_key`
  - response: `status`, `participant_id`, `team_cap`, `remaining_slots`,
    `warnings`
  - expected authority: OpenWebUI enforces the cap and participant record.
- `POST /api/agent/service/runs/{run_id}/model-selection`
  - idempotency key:
    `modelsel:<participant_id>:<selection_id>:<attempt>`
  - request: `run_id`, `participant_id`, `selection_id`,
    `requested_model_id`, `fuzzy_request`, `source_request`,
    `idempotency_key`
  - response: `selected_model_id`, `choices`, `meta`, `warnings`
  - expected authority: OpenWebUI chooses a permission-valid model.
- `POST /api/agent/service/runs/{run_id}/events`
  - already present; W9B2 uses it for `subagent.created`,
    `subagent.completed`, `subagent.failed`, and cancellation-visible events.

## Tests Added First

- Added first:
  - `services/agentscope-runtime/tests/test_subagents.py`
  - Updates to `services/agentscope-runtime/tests/test_openwebui_client.py`
  - Update to `services/agentscope-runtime/tests/test_app.py`
- Initial command `uv run pytest` was not a valid red test because this service
  only installs pytest through the optional `test` extra.
- Valid red command:
  - `cd services/agentscope-runtime && uv run --extra test pytest`
- Valid red result:
  - collection failed with
    `ModuleNotFoundError: No module named 'agentscope_runtime.subagents'`
  - this is the expected missing-implementation failure for the new adapter
    tests.

## Implementation Checkpoints

- Added `services/agentscope-runtime/agentscope_runtime/subagents.py`.
  - `AgentScopeSubagentAdapter` maps leader subagent intents to OpenWebUI
    callbacks.
  - Enforces single-level hierarchy locally by accepting only
    `parent_participant_id == "leader"`.
  - Registers participants through the local `/subagents` callback shim so
    OpenWebUI remains the cap/participant authority.
  - Selects subagent models only through the local `/model-selection` callback
    shim.
  - Emits `subagent.created`, `subagent.completed`, `subagent.failed`, and
    cancellation-visible `run.cancelled` events with participant attribution.
  - Cancellation stops the adapter plan loop and does not call the injected
    terminal process kill hook.
  - Subagent execution context exposes `openwebui_credentials={}` only.
- Extended `OpenWebUIClient` with:
  - `register_subagent(...)`
  - `select_model(...)`
- Extended runtime schemas with:
  - `SubagentRegisterRequest`
  - `ModelSelectionRequest`
  - recursive rejection of explicit raw credential fields in `RunStartRequest`.
- No dependency or lockfile changes were made.

## Verification Commands And Results

- `cd services/agentscope-runtime && uv run --extra test pytest`
  - result: `15 passed in 0.53s`
- `git diff --check`
  - result: passed with no output

## Files Changed

- `handoff/agent-mode/w9b-agentscope-adapter.md`
- `services/agentscope-runtime/agentscope_runtime/subagents.py`
- `services/agentscope-runtime/agentscope_runtime/openwebui_client.py`
- `services/agentscope-runtime/agentscope_runtime/schemas.py`
- `services/agentscope-runtime/tests/test_subagents.py`
- `services/agentscope-runtime/tests/test_openwebui_client.py`
- `services/agentscope-runtime/tests/test_app.py`

## Downstream Interface Notes

- W9B1/backend still needs concrete service bindings for:
  - `POST /api/agent/service/runs/{run_id}/subagents`
  - `POST /api/agent/service/runs/{run_id}/model-selection`
- Current W9B2 tests define the request/response shape expected by the runtime.
- Existing `/events` callback is enough for lifecycle event emission.

## Unresolved Risks Or Questions

- This slice does not add the `agentscope` package as a runtime dependency.
  The adapter boundary is built from verified AgentScope APIs, but actual
  AgentScope `Agent`/`ChatModelBase` instantiation should be wired in a later
  integration slice once W9B1 callback routes exist.
- W9B1 should decide whether subagent registration emits its own events or
  treats W9B2's explicit lifecycle event callbacks as authoritative.
