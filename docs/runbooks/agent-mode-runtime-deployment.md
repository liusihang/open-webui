# Agent Mode Runtime Deployment Runbook

Date: 2026-06-18

Status: W12A deployment and acceptance-harness prep. The checked-in fixture
harness validates the 12 MVP scenario contract shape only. W12B live acceptance
is still pending until W9B2 AgentScope runtime adapter and W10A Chat UI
integration are merged and the scenarios are executed against a real stack.

## Runtime Boundary

OpenWebUI is the source of truth for Agent Run records, events, model calls,
tool calls, approvals, artifacts, compaction, and chat messages. AgentScope is
the external runtime that accepts run context and calls back into OpenWebUI.

Do not deploy AgentScope with OpenWebUI user JWTs, provider keys, OAuth tokens,
MCP credentials, terminal credentials, or direct tool/model authority. The
runtime service uses only the service credential configured by
`AGENT_RUNTIME_SERVICE_TOKEN`.

## Required OpenWebUI Config

Set these on the OpenWebUI deployment that will run Agent Mode:

```bash
ENABLE_AGENT_MODE=true
AGENT_RUNTIME_BASE_URL=http://agentscope-runtime:8000
AGENT_RUNTIME_SERVICE_TOKEN=<shared-service-token>
AGENT_RUN_DEFAULT_TIMEOUT_SECONDS=300
AGENT_RUN_MAX_MODEL_CALLS=25
AGENT_RUN_MAX_TOOL_CALLS=50
AGENT_TEAM_MAX_SUBAGENTS=5
AGENT_SUBAGENT_DEFAULT_BUDGET='{"max_model_calls":2,"max_tool_calls":3}'
```

If `ENABLE_AGENT_MODE=false`, OpenWebUI should use the explicit legacy chat
path. If `ENABLE_AGENT_MODE=true` and the runtime is unavailable, the user must
see an Agent Run failure. There is no silent legacy fallback in the MVP.

Persistent DB config can override env-derived config after startup. Before a
live rollout, verify the actual runtime config surface used by the deployed
OpenWebUI process, not only the compose or shell env file.

## Runtime Service Startup

The current W4 service skeleton exposes:

- `GET /health`
- `POST /v1/openwebui/runs`
- `POST /v1/openwebui/runs/{run_id}/cancel`
- `GET /v1/openwebui/runs/{run_id}/status`

The current service is a FastAPI factory in
`services/agentscope-runtime/agentscope_runtime/app.py`. Until W9B2 lands a
stable container entrypoint, a local isolated smoke can instantiate the factory
directly:

```bash
cd /Users/liusihang/openwebui/.worktrees/agent-mode-w12-e2e-harness/services/agentscope-runtime
AGENT_RUNTIME_SERVICE_TOKEN=test-service-token \
OPENWEBUI_BASE_URL=http://127.0.0.1:8080 \
uv run python3 - <<'PY'
import os
import uvicorn
from agentscope_runtime.app import create_app

app = create_app(
    service_token=os.environ["AGENT_RUNTIME_SERVICE_TOKEN"],
    openwebui_base_url=os.environ["OPENWEBUI_BASE_URL"],
    openwebui_service_token=os.environ["AGENT_RUNTIME_SERVICE_TOKEN"],
)
uvicorn.run(app, host="127.0.0.1", port=8097)
PY
```

For compose-based deployment, keep the runtime as a separate service from
OpenWebUI and point OpenWebUI at the service URL:

```yaml
services:
  agentscope-runtime:
    build:
      context: ./services/agentscope-runtime
    environment:
      AGENT_RUNTIME_SERVICE_TOKEN: ${AGENT_RUNTIME_SERVICE_TOKEN}
      OPENWEBUI_BASE_URL: http://open-webui:8080
    expose:
      - "8000"

  open-webui:
    environment:
      ENABLE_AGENT_MODE: "true"
      AGENT_RUNTIME_BASE_URL: http://agentscope-runtime:8000
      AGENT_RUNTIME_SERVICE_TOKEN: ${AGENT_RUNTIME_SERVICE_TOKEN}
      AGENT_TEAM_MAX_SUBAGENTS: "5"
```

Do not wire this snippet into the main compose file until the W9B2 runtime
entrypoint is finalized and W12B acceptance has real evidence.

## Health And Readiness Checks

Config-only check:

```bash
ENABLE_AGENT_MODE=true \
AGENT_RUNTIME_BASE_URL=http://agentscope-runtime:8000 \
AGENT_RUNTIME_SERVICE_TOKEN=test-service-token \
AGENT_TEAM_MAX_SUBAGENTS=5 \
python3 scripts/agent_mode/healthcheck.py --check-env --skip-runtime
```

Runtime health:

```bash
python3 scripts/agent_mode/healthcheck.py \
  --runtime-base-url "$AGENT_RUNTIME_BASE_URL"
```

Runtime readiness for an accepted run:

```bash
python3 scripts/agent_mode/healthcheck.py \
  --runtime-base-url "$AGENT_RUNTIME_BASE_URL" \
  --service-token "$AGENT_RUNTIME_SERVICE_TOKEN" \
  --readiness-run-id "$AGENT_RUN_ID"
```

The readiness check uses the protected runtime status endpoint. It proves the
service token can access a known runtime session, but it does not prove the
OpenWebUI callback path or frontend event rendering by itself.

## W12 Acceptance Harness

Dry-run mode lists the required observations and does not execute scenarios:

```bash
python3 scripts/agent_mode/acceptance_harness.py dry-run
```

Fixture mode validates the checked-in transcript placeholders:

```bash
python3 scripts/agent_mode/acceptance_harness.py fixture
```

Live W12B mode requires an evidence JSON captured from a real post-W9B2/W10A
stack:

```bash
python3 scripts/agent_mode/acceptance_harness.py live \
  --evidence /path/to/w12b-live-evidence.json
```

Live evidence must use:

```json
{
  "mode": "live",
  "scenarios": [
    {
      "id": "scenario_01_ordinary_qa",
      "status": "live_passed",
      "live_status": "passed",
      "observations": ["event:run.running"]
    }
  ]
}
```

Each scenario must include every required observation defined by
`scripts/agent_mode/acceptance_harness.py`. Non-live evidence is rejected if it
claims `live_status="passed"`.

## MVP Scenario Checklist

| ID | Scenario | W12A Status |
| --- | --- | --- |
| 01 | Ordinary Q&A streams final answer through Agent Mode | Fixture placeholder only |
| 02 | Single OpenWebUI tool call succeeds | Fixture placeholder only |
| 03 | Open Terminal command registers output artifact | Fixture placeholder only |
| 04 | Tmp artifact retained and cleanup-eligible | Fixture placeholder only |
| 05 | Destructive action waits for approval | Fixture placeholder only |
| 06 | Leader creates concurrent subagents up to cap | Fixture placeholder only |
| 07 | Subagent model selection uses `meta.agent_selection` | Fixture placeholder only |
| 08 | SSE reconnect backfills by sequence | Fixture placeholder only |
| 09 | Final deltas only stream in final-answer phase | Fixture placeholder only |
| 10 | Cancel stops runtime loop but not Open Terminal process | Fixture placeholder only |
| 11 | Terminal states trigger compaction | Fixture placeholder only |
| 12 | Runtime unavailable is visible failure when enabled | Fixture placeholder only |

## W12B Live Evidence Rules

For each scenario, capture the smallest evidence that proves the behavior:

- run id, chat id, assistant message id, and final run state;
- relevant ordered Agent Run events with sequence numbers;
- artifact paths and cleanup metadata;
- process refs and whether `kill_process` was called;
- approval state and normalized tool result status;
- subagent participant ids, concurrency, and cap behavior;
- model-selection request/result including `meta.agent_selection`;
- SSE reconnect request with `Last-Event-ID` or `after_seq`;
- visible runtime failure event when `ENABLE_AGENT_MODE=true` and runtime is
  unavailable.

Do not treat fixture mode, dry-run mode, unit tests, or health checks as the 12
MVP scenarios passing. They are prerequisites for live W12B acceptance, not a
replacement for it.
