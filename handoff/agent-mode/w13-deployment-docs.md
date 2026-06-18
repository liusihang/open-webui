# W13 Deployment/Runtime Docs Audit

Date: 2026-06-18
Worktree: `/Users/liusihang/openwebui/.worktrees/agent-mode-w13-deployment-docs`
Branch: `codex/agent-mode-w13-deployment-docs`
Base commit checked: `00481b7ab` is an ancestor of HEAD.

## Scope

Audit whether operator-facing deployment/runtime notes are sufficient for
release. This pass was read-mostly and only writes this handoff.

Required inputs read:

- `handoff/agent-mode/controller.md`
- `docs/adr/0002-agent-mode-runtime-boundaries.md`: absent in this worktree
- `services/agentscope-runtime/pyproject.toml`
- `services/agentscope-runtime/README.md`: absent
- `services/agentscope-runtime/*`: no package-local markdown docs found
- `backend/open_webui/config.py` Agent Mode env vars only
- `backend/open_webui/main.py` Agent Mode startup/mount/chat-runtime sections only
- `/Users/liusihang/openwebui/docs/plans/2026-06-17-openwebui-agent-mode-agentscope-design.md`
- `/Users/liusihang/openwebui/docs/plans/2026-06-18-openwebui-agent-mode-runtime-contracts.md`
- `/Users/liusihang/openwebui/docs/plans/2026-06-17-openwebui-agent-mode-agentscope-implementation.md`

## Go/No-Go

No-go for release as operator-facing deployment documentation.

This is not a no-go on the runtime implementation itself. The code and planning
docs show the intended runtime behavior, failure behavior, AgentScope pin, Open
Terminal relationship, and artifact semantics. The release blocker is that a
deployer cannot start and health-check the three-service shape from checked-in
operator docs without reading code.

Go for PR continuation if a narrow package-local runtime/deployment note is
added before release handoff.

## Checklist

- [x] Agent Mode env vars and defaults identified from code.
- [x] AgentScope dependency pin confirmed service-local.
- [x] Backend Agent Mode router mounts and runtime start path identified.
- [x] Runtime service health endpoint identified.
- [x] Planning docs confirm no silent legacy fallback when enabled.
- [x] Code confirms runtime unavailable becomes a failed Agent Run result.
- [x] Planning docs confirm Open Terminal is the user-visible workspace.
- [x] Planning docs confirm outputs/tmp retention and cleanup intent.
- [x] Planning docs confirm cancellation does not kill Open Terminal processes.
- [ ] Operator docs explain how to run AgentScope runtime service.
- [ ] Operator docs list runtime service env/config inputs and defaults.
- [ ] Operator docs define startup order across Open Terminal, AgentScope
      runtime, and OpenWebUI backend.
- [ ] Operator docs define health/readiness checks for OpenWebUI plus runtime.
- [ ] Operator docs explain production cleanup policy ownership for tmp
      artifacts.

## Env Vars And Defaults

Agent Mode backend env vars are defined in
`backend/open_webui/config.py:3154-3210` and assigned into app config in
`backend/open_webui/main.py:1450-1457`.

| Env var | Default | Meaning / operator impact |
| --- | --- | --- |
| `ENABLE_AGENT_MODE` | `False` | Rollout switch. When false, legacy chat path remains selected. |
| `AGENT_RUNTIME_BASE_URL` | empty string | Backend runtime client target. Must point at AgentScope runtime when Agent Mode is enabled. Empty value makes runtime start fail visibly. |
| `AGENT_RUNTIME_SERVICE_TOKEN` | empty string | Bearer token used by backend to call the runtime and by runtime callbacks back into OpenWebUI. Must match both sides in a real deployment. |
| `AGENT_RUN_DEFAULT_TIMEOUT_SECONDS` | `300` | Runtime client request timeout and run budget value. |
| `AGENT_RUN_MAX_MODEL_CALLS` | `25` | Run budget value sent to runtime. |
| `AGENT_RUN_MAX_TOOL_CALLS` | `50` | Run budget value sent to runtime. |
| `AGENT_TEAM_MAX_SUBAGENTS` | `5` | Team cap sent to runtime. |
| `AGENT_SUBAGENT_DEFAULT_BUDGET` | `{"max_model_calls": 8, "max_tool_calls": 16}` | JSON budget used for subagents; invalid JSON silently falls back to the same default dict. |

Runtime-service code also needs startup inputs that are not operator-documented:

- `service_token` for `create_app(...)`, enforced on runtime endpoints at
  `services/agentscope-runtime/agentscope_runtime/app.py:123-128`.
- `openwebui_base_url`, defaulting in code to `http://127.0.0.1:8080` at
  `services/agentscope-runtime/agentscope_runtime/app.py:118-120`.
- `openwebui_service_token`, defaulting to `service_token` at
  `services/agentscope-runtime/agentscope_runtime/app.py:118-121`.

There is no README or service-local markdown explaining how these map to env
vars, uvicorn command arguments, container configuration, or deployment secret
names.

## Startup And Health

Confirmed implementation surfaces:

- Backend mounts terminal APIs and Agent Mode APIs in
  `backend/open_webui/main.py:2031-2035`.
- Backend initializes configured tool servers, then terminal servers, inside the
  tool-server startup block at `backend/open_webui/main.py:1250-1261`.
- Agent Mode product chat is gated by `ENABLE_AGENT_MODE` and chat metadata in
  `backend/open_webui/main.py:2261-2267`.
- Backend calls AgentScope runtime via `AGENT_RUNTIME_BASE_URL` and bearer token
  in `backend/open_webui/main.py:2393-2408`.
- Runtime service exposes unauthenticated `GET /health` returning
  `{"status": "ok"}` at
  `services/agentscope-runtime/agentscope_runtime/app.py:130-134`.
- Runtime service exposes authenticated `POST /v1/openwebui/runs`,
  `POST /v1/openwebui/runs/{run_id}/cancel`, and
  `GET /v1/openwebui/runs/{run_id}/status` at
  `services/agentscope-runtime/agentscope_runtime/app.py:136-190`.

Missing operator doc:

- Exact command or container entrypoint to launch `services/agentscope-runtime`.
- Which service must be reachable from which network namespace:
  OpenWebUI backend -> AgentScope runtime, and AgentScope runtime -> OpenWebUI
  service callbacks.
- Startup order: Open Terminal first if terminal server connections are used,
  AgentScope runtime with callback access to backend, then OpenWebUI with
  `ENABLE_AGENT_MODE=true` and `AGENT_RUNTIME_BASE_URL` set, or a documented
  acceptable variant.
- Health checks:
  - runtime `GET /health`;
  - OpenWebUI `/health` or `/ready`;
  - authenticated smoke of backend -> runtime start path is not documented.
- Expected behavior when terminal server init fails: backend logs warnings at
  startup; operator docs should say Agent Mode terminal tools may be degraded
  rather than implying runtime health covers terminal health.

## Failure Behavior

The planning docs state the required failure contract:

- No silent fallback when Agent Mode is enabled and AgentScope is unavailable is
  a non-goal in
  `/Users/liusihang/openwebui/docs/plans/2026-06-17-openwebui-agent-mode-agentscope-implementation.md:17-27`.
- Rollout behavior says enabled means AgentScope runtime failure is visible
  failure, disabled means explicit legacy chat path, in
  `/Users/liusihang/openwebui/docs/plans/2026-06-17-openwebui-agent-mode-agentscope-design.md:214-223`.

Code matches that contract for runtime start:

- Empty runtime base URL raises `AgentRuntimeUnavailable` in
  `backend/open_webui/agent/runtime_client.py:32-35`.
- Runtime HTTP 5xx becomes unavailable and HTTP 4xx becomes rejected in
  `backend/open_webui/agent/runtime_client.py:40-57`.
- `_start_agent_mode_chat` catches runtime errors, transitions the run from
  `queued` to `failed`, appends `run.failed`, links the assistant message error,
  and returns an error object in `backend/open_webui/main.py:2407-2424`.

Docs are sufficient for design intent, but not sufficient for operators because
there is no runbook line saying `ENABLE_AGENT_MODE=true` with missing or bad
`AGENT_RUNTIME_BASE_URL` intentionally fails chat rather than falling back.

## Open Terminal, Artifacts, Cleanup

Planning docs state the key product semantics:

- Open Terminal is the user-visible workspace while AgentScope runtime workspace
  is not the product artifact source of truth, in
  `/Users/liusihang/openwebui/docs/plans/2026-06-17-openwebui-agent-mode-agentscope-design.md:18-24`.
- Default outputs path is `/workspace/agent-runs/<run_id>/outputs`, tmp path is
  `/workspace/agent-runs/<run_id>/tmp`, outputs are never automatically cleaned,
  tmp is retained after completion and eligible for 7-day cleanup, and deleting
  chat must not delete terminal workspace files, in
  `/Users/liusihang/openwebui/docs/plans/2026-06-17-openwebui-agent-mode-agentscope-design.md:128-141`.
- Runtime contract says outputs files should register artifacts and tmp files
  should register artifacts only when explicitly requested and be marked
  cleanup-eligible, in
  `/Users/liusihang/openwebui/docs/plans/2026-06-18-openwebui-agent-mode-runtime-contracts.md:313-324`.
- Cancellation stops the runtime loop but does not kill Open Terminal
  processes; remaining processes should be visible, in
  `/Users/liusihang/openwebui/docs/plans/2026-06-17-openwebui-agent-mode-agentscope-design.md:170-177`
  and acceptance scenario lines 233-238.

Code sends the default paths to runtime at
`backend/open_webui/main.py:2299-2309`.

Missing operator doc:

- Who owns the 7-day tmp cleanup job, where it runs, and how to disable or
  tune it. The docs say "eligible" but not "implemented by X".
- Operational warning that cancelling an Agent Run does not clean up or kill
  Open Terminal processes, so process inspection/kill is an explicit follow-up
  action.
- Whether `/workspace/agent-runs` must be a persistent volume shared with Open
  Terminal, and whether AgentScope runtime needs any direct mount or only sees
  paths through OpenWebUI callbacks.

## Dependency Pin

The AgentScope dependency is service-local and pinned in
`services/agentscope-runtime/pyproject.toml:1-12`:

```text
agentscope[service] @ git+https://github.com/agentscope-ai/agentscope.git@c13c3effcb568ef915cbbd0fe900df2f2b9b003c
```

The root OpenWebUI dependency set is not touched by this package-local audit.
This should be called out in release notes so operators know the AgentScope
runtime has a separate lock/install lifecycle.

## Severity

### Release-blocking

1. No operator-facing startup recipe for `services/agentscope-runtime`.
   Evidence: `services/agentscope-runtime/README.md` is absent and no package
   markdown docs exist. A deployer cannot know the uvicorn entrypoint, required
   service token, callback base URL, port, or container/env mapping from docs.

2. No three-service deployment topology/runbook.
   Evidence: code requires OpenWebUI -> runtime calls and runtime -> OpenWebUI
   callbacks, while Open Terminal remains the user-visible workspace. The
   planning docs describe ownership, but no operator note states network,
   startup order, shared volume expectations, or terminal degraded behavior.

3. No cleanup/process-retention runbook.
   Evidence: planning docs define outputs/tmp retention and cancellation
   semantics, but no operator doc says whether tmp cleanup exists, how it is
   scheduled, or how operators should handle surviving Open Terminal processes.

### PR-blocking before release handoff

1. Add a narrow service-local doc rather than broad product docs:
   `services/agentscope-runtime/README.md` or
   `docs/agent-mode/deployment-runtime.md`.

2. Include a minimal verified launch recipe, for example:
   - install with `cd services/agentscope-runtime && uv sync`;
   - start with a documented `uvicorn` app factory command or add an explicit
     module entrypoint if needed;
   - configure `AGENT_RUNTIME_SERVICE_TOKEN` on both services;
   - configure runtime callback base URL for OpenWebUI;
   - configure OpenWebUI `ENABLE_AGENT_MODE=true` and
     `AGENT_RUNTIME_BASE_URL=http://<runtime-host>:<port>`.

3. Include health and smoke checks:
   - `GET <runtime>/health`;
   - OpenWebUI `/health` or `/ready`;
   - one intentional unavailable-runtime smoke proving visible failure, or one
     authenticated runtime-start smoke proving backend/runtime credentials.

### Follow-up

1. Document the exact production implementation of the 7-day tmp cleanup once
   it exists, including manual cleanup command/path.

2. Add a compact release-note paragraph: no silent fallback under
   `ENABLE_AGENT_MODE=true`; disabling Agent Mode is the explicit legacy path.

3. Add an operator note that Open Terminal process lifetime is separate from
   Agent Run cancellation and must be inspected/stopped explicitly.

## Proposed Narrow Doc Patch

Patch only a small deployment note. Suggested location:
`services/agentscope-runtime/README.md`.

Suggested sections:

1. Purpose and topology:
   OpenWebUI backend, AgentScope runtime service, and Open Terminal are separate
   runtime surfaces. OpenWebUI remains the authority and artifact source of
   truth.
2. Install/start:
   service-local `uv` commands and an explicit `uvicorn` command or module
   entrypoint.
3. Configuration:
   backend env vars and runtime service env vars, with defaults and which ones
   must match.
4. Startup order and health:
   runtime `/health`, OpenWebUI health, terminal server initialization warning,
   and a minimal authenticated smoke.
5. Failure and cleanup semantics:
   no silent fallback, outputs vs tmp retention, tmp cleanup eligibility, and
   cancellation does not kill terminal processes.

## Final Decision

No-go for release docs until the narrow operator deployment note exists.
PR can continue if this is tracked as a PR-blocking documentation follow-up
before release handoff, not as another broad product-doc rewrite.
