# W12A Deployment And E2E Harness Handoff

Date: 2026-06-18

## Goal

Prepare deployment docs, health/readiness checks, and an E2E acceptance harness
for the AgentScope-based Agent Mode MVP.

This slice may start now, but it must not claim that all 12 MVP scenarios pass
until W9B2 and W10A are merged and W12B final acceptance is actually run.

## Base

- Worktree:
  `/Users/liusihang/openwebui/.worktrees/agent-mode-w12-e2e-harness`
- Branch: `codex/agent-mode-w12-e2e-harness`
- Base commit: `28830b966`

## Read-Only Context

- `/Users/liusihang/openwebui/docs/plans/2026-06-17-openwebui-agent-mode-agentscope-implementation.md`
- `/Users/liusihang/openwebui/docs/plans/2026-06-18-openwebui-agent-mode-runtime-contracts.md`
- `/Users/liusihang/openwebui/docs/plans/2026-06-17-openwebui-agent-mode-agentscope-design.md`
- `/Users/liusihang/openwebui/docs/adr/0002-agent-mode-runtime-boundaries.md`

## Owned Files

- docs/runbooks/deployment docs for Agent Mode runtime
- health/readiness probe scripts or tests
- E2E harness scripts/fixtures for the 12 MVP acceptance scenarios
- This handoff

## Must Not Touch

- core backend runtime logic
- frontend `Chat.svelte`
- `services/agentscope-runtime` implementation internals beyond documented
  health endpoint usage
- nested `open-terminal/`

## Required First Step

Inspect existing docs/scripts/test patterns and write the harness skeleton with
a dry-run or fixture mode first. Record the command/result here.

Required harness coverage targets:

1. ordinary Q&A streams final answer through Agent Mode;
2. single OpenWebUI tool call succeeds;
3. Open Terminal command registers output artifact;
4. tmp artifact retained and cleanup-eligible;
5. destructive action waits for approval;
6. leader creates concurrent subagents up to cap;
7. subagent model selection uses `meta.agent_selection`;
8. SSE reconnect backfills by sequence;
9. final deltas only stream in final-answer phase;
10. cancel stops runtime loop but not Open Terminal process;
11. terminal states trigger compaction;
12. runtime unavailable is visible failure when enabled.

## Verification To Record

- Harness dry-run / fixture-mode command and result.
- Any health probe tests.
- `git diff --check`
- Explicit statement that W12B live acceptance is still pending unless it has
  actually been run after W9B2 and W10A merge.

## Progress Log

- 2026-06-18 checkpoint 1:
  - Verified worktree/branch:
    `/Users/liusihang/openwebui/.worktrees/agent-mode-w12-e2e-harness`
    on `codex/agent-mode-w12-e2e-harness`.
  - Confirmed current `HEAD` includes base `28830b966` by ancestry.
  - Read the required handoff and the four root docs listed above.
  - Inspected existing patterns:
    - runtime service skeleton exposes `GET /health` and protected
      `/v1/openwebui/runs/{run_id}/status`;
    - backend Agent Mode tests live under `backend/open_webui/test/agent`;
    - there is no existing Agent Mode runbook or acceptance harness.
  - Chosen W12A shape: add a standalone `scripts/agent_mode` fixture/dry-run
    harness plus focused pytest coverage, add a health/readiness probe script,
    and document live deployment/acceptance gates without claiming the 12 MVP
    scenarios pass live.
- 2026-06-18 checkpoint 2:
  - Added deployment runbook:
    `docs/runbooks/agent-mode-runtime-deployment.md`.
  - Added acceptance harness:
    `scripts/agent_mode/acceptance_harness.py`.
  - Added fixture transcript:
    `scripts/agent_mode/fixtures/w12_mvp_fixture.json`.
  - Added health/readiness probe:
    `scripts/agent_mode/healthcheck.py`.
  - Added focused tests:
    `backend/open_webui/test/agent/test_w12_acceptance_harness.py` and
    `backend/open_webui/test/agent/test_w12_healthcheck.py`.
  - Environment note: bare `python` is not available on this host, and bare
    `python3 -m pytest` has no pytest installed. Final verification used
    `python3` for scripts and `uv run` for pytest.
- 2026-06-18 verification:
  - `python3 scripts/agent_mode/acceptance_harness.py dry-run`:
    exit 0; listed 12 required W12B evidence cases; live acceptance pending.
  - `python3 scripts/agent_mode/acceptance_harness.py fixture`:
    exit 0; fixture contract `12/12 satisfied`; live acceptance pending.
  - `ENABLE_AGENT_MODE=true AGENT_RUNTIME_BASE_URL=http://agent-runtime.test AGENT_RUNTIME_SERVICE_TOKEN=test-service-token AGENT_TEAM_MAX_SUBAGENTS=5 python3 scripts/agent_mode/healthcheck.py --check-env --skip-runtime`:
    exit 0; env probe ok.
  - `uv run --frozen --group dev python3 -m pytest backend/open_webui/test/agent/test_w12_acceptance_harness.py backend/open_webui/test/agent/test_w12_healthcheck.py`:
    exit 0; 9 passed. Pytest emitted the existing
    `asyncio_default_fixture_loop_scope` deprecation warning.
  - `uv run --with ruff==0.15.5 ruff check scripts/agent_mode/acceptance_harness.py scripts/agent_mode/healthcheck.py backend/open_webui/test/agent/test_w12_acceptance_harness.py backend/open_webui/test/agent/test_w12_healthcheck.py`:
    exit 0; all checks passed.
  - W12B live acceptance is still pending. The 12 MVP scenarios have not been
    claimed live-passing because W9B2 and W10A are not merged in this worktree
    and the final live acceptance run has not been executed.
