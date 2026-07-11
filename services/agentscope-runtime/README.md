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
AGENT_RUNTIME_AUTO_FINALIZE_ORDINARY_QA=true
AGENT_RUNTIME_MODEL_CALL_CONNECT_TIMEOUT_SECONDS=10
AGENT_RUNTIME_MODEL_CALL_READ_IDLE_TIMEOUT_SECONDS=30
AGENT_RUNTIME_MODEL_CALL_TOTAL_TIMEOUT_SECONDS=300
```

`AGENT_RUNTIME_SERVICE_TOKEN` is required by the runtime. OpenWebUI uses the
same token when calling the runtime, and the runtime uses `OPENWEBUI_SERVICE_TOKEN`
when calling OpenWebUI service callbacks. If `OPENWEBUI_SERVICE_TOKEN` is not
set, the runtime reuses `AGENT_RUNTIME_SERVICE_TOKEN`.

`AGENT_RUNTIME_AUTO_FINALIZE_ORDINARY_QA` defaults to true. Set it to `false`
only for tests or specialized harnesses that need to keep the runtime session
open after the initial `run.running` event.

Model callbacks use three independent timeout boundaries. The connect timeout
limits establishing the OpenWebUI connection, the read-idle timeout limits the
gap between received stream bytes, and the total timeout is a hard cap for the
whole callback operation. OpenWebUI emits transport-only SSE heartbeats while
an upstream model stream is active, so legitimate silent model work does not
consume the read-idle budget. Heartbeats are not stored as chat content.

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
uv run uvicorn 'agentscope_runtime.app:create_app_from_env' --factory --host 127.0.0.1 --port 8097
```

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
