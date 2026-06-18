# W13 PR Package

Date: 2026-06-18

## Status

Go for human review from the integration branch after W13 release-readiness
fixes and final gates.

Integration target:

- Worktree:
  `/Users/liusihang/openwebui/.worktrees/agent-mode-agentscope-pr7`
- Branch: `codex/agent-mode-agentscope-pr7`
- PR7 base: `2183a6697c672c60d0137b64d57eca7fdad0b5e6`
- Final checked commit at package time: `07b6420cf`

## What Landed

- Agent Run storage, state transitions, idempotent operation ledger, events,
  artifacts, and compaction.
- Agent Run user/service routers, SSE event stream, reconnect/backfill, and
  final-answer phase ordering.
- Agent-first product chat path with visible runtime failure and no silent
  fallback while `ENABLE_AGENT_MODE=true`.
- AgentScope runtime service package with pinned service-local AgentScope
  dependency and env-driven `uvicorn --factory` startup.
- OpenWebUI model authority, tool authority, destructive approval, artifact
  registration, Open Terminal process refs, cancellation semantics, subagent
  control plane, model catalog, AgentScope subagent adapter, frontend Agent Run
  event UI, and W12 acceptance harness.
- W13 release fixes:
  - `services/agentscope-runtime/README.md` operator runbook.
  - `create_app_from_env()` runtime factory.
  - product Agent Mode chat now resolves tools before runtime start, snapshots
    the tool access envelope, and stores callback callables in
    `AGENT_TOOL_REGISTRIES[run_id]`.
  - failed runtime starts remove the run-scoped tool registry.

## Final Gates

Commands rerun after the product tool path fix:

| Gate | Result |
| --- | --- |
| `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/agent backend/open_webui/test/models/test_agent_runs.py` | `102 passed, 9 warnings` |
| `cd services/agentscope-runtime && uv run --extra test pytest -q` | `24 passed` |
| `npm run test:frontend -- --run src/lib/apis/agentRuns/index.test.ts src/lib/components/chat/AgentEvents/eventFold.test.ts src/lib/components/chat/historySync.test.ts` | `3 files / 24 tests passed` |
| `uv run --frozen python scripts/agent_mode/acceptance_harness.py dry-run` | passed, `failures: none` |
| `uv run --frozen python scripts/agent_mode/acceptance_harness.py fixture` | `case contract: 12/12 satisfied`, `failures: none` |
| `uv run --frozen python scripts/agent_mode/acceptance_harness.py live --evidence handoff/agent-mode/w12d-merged-live-evidence.json` | `case contract: 12/12 satisfied`, `live acceptance: passed`, `failures: none` |
| `uv run ruff check --select F backend/open_webui/agent backend/open_webui/models/agent_runs.py backend/open_webui/routers/agent_runs.py backend/open_webui/routers/agent_service.py backend/open_webui/test/agent backend/open_webui/test/models/test_agent_runs.py scripts/agent_mode services/agentscope-runtime/agentscope_runtime services/agentscope-runtime/tests` | passed |
| `git diff --check 2183a6697c672c60d0137b64d57eca7fdad0b5e6..HEAD` | passed after EOF whitespace cleanup |

## Acceptance Evidence

Merged evidence:

- `handoff/agent-mode/w12d-merged-live-evidence.json`

The live acceptance harness reports all 12 MVP scenarios satisfied:

```text
case contract: 12/12 satisfied
live acceptance: passed
failures: none
```

W13 evidence audit:

- `handoff/agent-mode/w13-evidence-integrity.md`
- Result: GO for evidence integrity. Every merged scenario traces to W12D source
  evidence and no scenario is fixture-only or unit-only.

## Remaining Non-Blocking Notes

- The acceptance harness success message still says `W12B`; the file and
  validation are W12D merged evidence. This is wording only.
- Broad `main.py` ruff remains noisy from pre-existing duplicate/unused imports.
  Final release gates use scoped F-class checks over agent-mode paths.
- Backend tests emit deprecation warnings from existing dependencies and app
  code. They do not fail the gate.
- W12D subagent proof uses public runtime start plus direct
  `AgentScopeSubagentAdapter` driving live OpenWebUI callbacks because the
  runtime HTTP service has no public subagent-plan endpoint. W13-1 classified
  this as weaker-than-full-product-path evidence, not fixture-only evidence.
- Terminal artifact evidence persists logical `/workspace/...` paths while the
  physical host proof writes under `/private/tmp/...` because this host's
  `/workspace` is read-only.
- Root `uv.lock` remains local verification churn and is intentionally unstaged.

## Suggested PR Summary

Agent Mode MVP on top of PR7:

- Adds Chat-Bound Agent Runs and AgentScope runtime service integration.
- Keeps OpenWebUI authoritative for model/tool execution, permissions, events,
  artifacts, approvals, and chat history.
- Streams persisted Agent Run events through OpenWebUI SSE and final answers
  only in final-answer phase.
- Supports OpenWebUI/MCP/OpenAPI/Open Terminal tool authority, destructive
  approval, artifacts, process refs, cancellation, event compaction, subagents,
  and subagent model selection.
- Includes service-local AgentScope runtime docs and final W12/W13 acceptance
  evidence.

Final verification:

- Backend agent/storage: `102 passed`.
- AgentScope runtime: `24 passed`.
- Frontend focused: `24 passed`.
- W12 live acceptance: `12/12`, passed.
- Scoped ruff and PR7-base diff-check passed.
