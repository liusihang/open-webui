# OpenWebUI AgentScope Runtime

This service is the AgentScope runtime process used by OpenWebUI Agent Mode.
OpenWebUI remains the product authority for users, models, tools, chat
messages, events, artifacts, and approvals. The runtime only orchestrates the
Agent run and calls back to OpenWebUI for model and tool work.

## Topology

An Agent Mode deployment has three runtime surfaces:

- OpenWebUI backend: owns chat, permissions, model/tool execution, Agent Run
  records, events, and artifacts.
- AgentScope runtime service: accepts Agent Run start/cancel/status requests
  from OpenWebUI and sends authenticated callbacks back to OpenWebUI.
- Open Terminal: remains the user-visible workspace for commands, process refs,
  and generated files.

OpenWebUI must be able to reach this runtime service. The runtime service must
be able to reach the OpenWebUI backend callback routes. If Open Terminal tools
are enabled, OpenWebUI must also be able to reach the configured Open Terminal
server.

## Configuration

Configure OpenWebUI with:

```bash
ENABLE_AGENT_MODE=true
AGENT_RUNTIME_BASE_URL=http://127.0.0.1:8097
AGENT_RUNTIME_SERVICE_TOKEN=<shared-service-token>
AGENT_RUN_DEFAULT_TIMEOUT_SECONDS=300
AGENT_RUN_MAX_MODEL_CALLS=25
AGENT_RUN_MAX_TOOL_CALLS=50
AGENT_TEAM_MAX_SUBAGENTS=5
AGENT_SUBAGENT_DEFAULT_BUDGET='{"max_model_calls": 8, "max_tool_calls": 16}'
```

Configure this runtime service with:

```bash
AGENT_RUNTIME_SERVICE_TOKEN=<shared-service-token>
OPENWEBUI_BASE_URL=http://127.0.0.1:8080
OPENWEBUI_SERVICE_TOKEN=<shared-service-token>
AGENT_RUNTIME_STATE_PATH=/var/lib/openwebui-agent-runtime/runtime.sqlite3
AGENT_RUNTIME_AUTO_FINALIZE_ORDINARY_QA=true
AGENT_RUNTIME_MODEL_CALL_CONNECT_TIMEOUT_SECONDS=10
AGENT_RUNTIME_MODEL_CALL_READ_IDLE_TIMEOUT_SECONDS=30
AGENT_RUNTIME_MODEL_CALL_TOTAL_TIMEOUT_SECONDS=300
AGENT_RUNTIME_MAX_CHECKPOINT_BYTES=16777216
AGENT_RUNTIME_TERMINAL_RETENTION_SECONDS=604800
AGENT_RUNTIME_MAX_TERMINAL_EXECUTIONS=10000
AGENT_RUNTIME_TERMINAL_CHECKPOINT_RETENTION_SECONDS=604800
AGENT_RUNTIME_MAX_TERMINAL_CHECKPOINTS=10000
```

`AGENT_RUNTIME_SERVICE_TOKEN` is required by the runtime. OpenWebUI uses the
same token when calling the runtime, and the runtime uses `OPENWEBUI_SERVICE_TOKEN`
when calling OpenWebUI service callbacks. If `OPENWEBUI_SERVICE_TOKEN` is not
set, the runtime reuses `AGENT_RUNTIME_SERVICE_TOKEN`.

`AGENT_RUNTIME_STATE_PATH` is required. It must point to storage that survives
runtime process restarts; the runtime fails startup instead of falling back to
an in-memory execution path when it is missing.

Durable execution currently requires exactly one runtime worker. Keep both
`WEB_CONCURRENCY` and `UVICORN_WORKERS` unset or set to `1`; startup rejects any
other value. The supported launcher validates environment and command-line
worker settings before invoking Uvicorn, preventing Uvicorn's multiprocess
supervisor from entering a child-respawn loop. Each worker also acquires a
non-blocking process lock at
`${AGENT_RUNTIME_STATE_PATH}.lock` during application startup, so command-line
or custom launch paths cannot bypass the single-worker safety boundary.

`AGENT_RUNTIME_AUTO_FINALIZE_ORDINARY_QA` defaults to true. Set it to `false`
only for tests or specialized harnesses that need to keep the runtime session
open after the initial `run.running` event.

Model callbacks use three independent timeout boundaries. The connect timeout
limits establishing the OpenWebUI connection, the read-idle timeout limits the
gap between received stream bytes, and the total timeout is a hard cap for the
whole callback operation. OpenWebUI emits transport-only SSE heartbeats while
an upstream model stream is active, so legitimate silent model work does not
consume the read-idle budget. Heartbeats are not stored as chat content.

The runtime store schema is versioned and migrated at startup. Checkpoints are
bounded by `AGENT_RUNTIME_MAX_CHECKPOINT_BYTES`; terminal execution journal rows
are pruned by age and capped by `AGENT_RUNTIME_MAX_TERMINAL_EXECUTIONS` to avoid
unbounded local state growth. Applied executions whose checkpoint still has a
pending continuation are excluded from that cleanup. Terminal checkpoints have
their own age and row limits through
`AGENT_RUNTIME_TERMINAL_CHECKPOINT_RETENTION_SECONDS` and
`AGENT_RUNTIME_MAX_TERMINAL_CHECKPOINTS`; active, waiting, and
continuation-pending checkpoints are never cleanup candidates. The runtime
requires Linux or another Unix platform providing `fcntl.flock`.

Wait checkpoints are committed before the runtime requests approval or user
input. The exact integer `checkpoint_version` is sent with the initial tool or
user-input callback. Initial tool callbacks never send
`X-Agent-Decision-Execution-ID`; that header is reserved for an approved
decision execution replay, while `external:*` identities remain internal
journal keys. On process startup, recoverable checkpoints with
`continuation_pending=true` are dispatched once without requiring another
backend activation request. Run cancellation first persists the cancelled
checkpoint, then cancels tracked activation and continuation tasks so an
in-flight HTTP tool callback cannot later write an applied/completed outcome.

The backend tool service owns authoritative `tool.completed` and `tool.failed`
events when its operation outcome is committed. The runtime emits the request
boundary but does not synthesize a second terminal tool event from the callback
response.

## Install And Start

Install dependencies from the service-local package:

```bash
cd services/agentscope-runtime
uv sync
```

Start the runtime:

```bash
cd services/agentscope-runtime
AGENT_RUNTIME_SERVICE_TOKEN=<shared-service-token> \
OPENWEBUI_BASE_URL=http://127.0.0.1:8080 \
AGENT_RUNTIME_STATE_PATH=/var/lib/openwebui-agent-runtime/runtime.sqlite3 \
uv run python -m agentscope_runtime.launcher --host 127.0.0.1 --port 8097 --workers 1
```

Do not invoke Uvicorn directly in deployment commands. The launcher is the
supported entry point; the application lifespan lock remains a fail-closed
backstop for custom or accidental direct invocation.

This package pins AgentScope in `pyproject.toml` and keeps its dependency state
in the service-local `uv.lock`. The root OpenWebUI dependency set does not own
the AgentScope runtime dependencies.

## Startup Order

Use this order for a local or single-host deployment:

1. Start Open Terminal if terminal tools are configured.
2. Start OpenWebUI so callback routes are reachable by the runtime.
3. Start this AgentScope runtime with `OPENWEBUI_BASE_URL` pointing at
   OpenWebUI and `AGENT_RUNTIME_SERVICE_TOKEN` matching OpenWebUI.
4. Enable Agent Mode in OpenWebUI with `ENABLE_AGENT_MODE=true` and
   `AGENT_RUNTIME_BASE_URL` pointing at this runtime.

Container deployments may start services concurrently, but readiness checks
must still prove both network directions:

- OpenWebUI -> AgentScope runtime
- AgentScope runtime -> OpenWebUI callback routes

## Health Checks

Runtime health:

```bash
curl -fsS http://127.0.0.1:8097/health
```

Expected response:

```json
{"status":"ok"}
```

OpenWebUI should also pass its normal health/readiness check before Agent Mode
traffic is routed to it. Runtime `/health` only proves this service is alive; it
does not prove Open Terminal availability or that OpenWebUI service callbacks
are reachable with the configured token.

## Failure Behavior

When `ENABLE_AGENT_MODE=true`, OpenWebUI does not silently fall back to the
legacy chat path if the runtime is unavailable. Runtime start failures are
recorded as failed Agent Runs and shown as visible chat failures. To use legacy
chat explicitly, disable Agent Mode.

## Artifacts And Processes

Open Terminal is the user-visible workspace. Default Agent Run paths are:

```text
/workspace/agent-runs/<run_id>/outputs
/workspace/agent-runs/<run_id>/tmp
```

Files in `outputs` are user-visible outputs and are not automatically cleaned.
Files in `tmp` are retained after run completion for debugging and may be
marked cleanup-eligible by OpenWebUI metadata. A production cleanup job for tmp
files should treat cleanup eligibility as policy input and must not remove
`outputs`.

Cancelling an Agent Run stops the AgentScope runtime loop but does not
automatically kill Open Terminal processes started by the run. OpenWebUI records
process refs so operators or users can inspect and stop surviving processes
explicitly.
