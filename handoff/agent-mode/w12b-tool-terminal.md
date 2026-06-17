# W12B-2 Tool, Approval, And Terminal Artifacts Handoff

Date: 2026-06-18

## Goal

Produce real/local acceptance evidence for:

- scenario 02: a single OpenWebUI tool call succeeds;
- scenario 03: Open Terminal command registers an output artifact;
- scenario 04: tmp artifact is retained and cleanup-eligible;
- scenario 05: destructive action waits for approval;
- scenario 10: cancellation stops the AgentScope loop but does not kill Open
  Terminal processes.

## Scope

Owns:

- tool/approval/terminal acceptance investigation and narrow fixes required for
  scenarios 02, 03, 04, 05, and 10;
- evidence file `handoff/agent-mode/w12b-tool-terminal-evidence.json`;
- this handoff.

Do not touch:

- subagent model-selection internals;
- frontend layout/visual polish;
- broad middleware refactors;
- nested `open-terminal/` source unless you prove the existing API is
  insufficient and record that as a blocker first.

## Evidence Contract

Only mark a scenario `live_passed` when there is direct evidence from the
integrated services. If that is not possible, write `status: "incomplete"` and
explain why in `evidence.notes`.

Required observations:

- scenario 02: `event:tool.requested`, `event:tool.completed`,
  `normalized_tool_result:success`.
- scenario 03: `tool:run_command`, `process_ref_registered`,
  `artifact:/workspace/agent-runs/<run_id>/outputs`.
- scenario 04: `artifact:/workspace/agent-runs/<run_id>/tmp`,
  `cleanup_eligible:true`, `retained_after_completion`.
- scenario 05: `event:approval.requested`, `state:waiting_approval`,
  `normalized_tool_result:approval_required`.
- scenario 10: `event:run.cancelled`, `runtime_cancel_requested`,
  `process_refs_retained`, `no_kill_process`.

## Verification Log

### 2026-06-18 03:20 CST - Read-In

Read first, as requested:

- `handoff/agent-mode/w12b-tool-terminal.md`
- `scripts/agent_mode/acceptance_harness.py`
- `docs/runbooks/agent-mode-runtime-deployment.md`

Relevant modules/tests read:

- `backend/open_webui/agent/tool_authority.py`
- `backend/open_webui/agent/destructive.py`
- `backend/open_webui/agent/artifacts.py`
- `backend/open_webui/agent/approval.py`
- `backend/open_webui/agent/resources.py`
- `backend/open_webui/agent/events.py`
- `backend/open_webui/agent/protocol.py`
- `backend/open_webui/models/agent_runs.py`
- `backend/open_webui/routers/agent_service.py`
- `backend/open_webui/routers/agent_runs.py`
- `services/agentscope-runtime/agentscope_runtime/app.py`
- `services/agentscope-runtime/agentscope_runtime/openwebui_client.py`
- `services/agentscope-runtime/agentscope_runtime/agentscope_bridge.py`
- focused tests under `backend/open_webui/test/agent/`
- service-local runtime tests under `services/agentscope-runtime/tests/`

Findings:

- W12A runbook says dry-run, fixture mode, health checks, and unit tests are
  prerequisites only; they do not prove live W12B acceptance.
- The checked-in `acceptance_harness.py live` validates all 12 MVP scenarios.
  This W12B-2 evidence file is intentionally a five-scenario subset and should
  not be treated as a full W12 harness pass.
- Existing helper-level coverage already proves read-only tool execution,
  destructive approval wait, tmp cleanup metadata, and cancellation retaining
  process refs locally.
- Narrow blocker found for scenario 03 integrated wiring: default
  `get_agent_tool_authority()` ignored configured terminal side-effect helpers,
  so a service-created authority could normalize terminal `process_refs` but
  not register them into the `AgentRunResourceManager`, nor register explicit
  output artifacts through the `AgentRunArtifactRegistrar`.

### 2026-06-18 03:42 CST - Narrow Fix, Red Then Green

Red command:

```bash
uv run pytest -q backend/open_webui/test/agent/test_tool_authority.py::test_service_default_tool_authority_wires_terminal_process_and_artifact_helpers
```

Red result:

- Exit code: 1
- Expected failure:
  `assert [] == [{'command': 'python analysis.py', ...}]`
- Meaning: normalized tool result had a terminal process ref, but the configured
  resource manager did not receive it.

Implemented fix:

- `backend/open_webui/routers/agent_service.py`
  - default `AgentToolAuthority` construction now passes configured
    `AGENT_RUN_RESOURCE_MANAGER` and `AGENT_RUN_ARTIFACT_REGISTRAR`.
- `backend/open_webui/test/agent/test_tool_authority.py`
  - added regression coverage for default service-authority wiring of terminal
    process refs and explicit output artifacts.

Green command:

```bash
uv run pytest -q backend/open_webui/test/agent/test_tool_authority.py::test_service_default_tool_authority_wires_terminal_process_and_artifact_helpers
```

Green result:

- Exit code: 0
- `1 passed, 2 warnings`

### 2026-06-18 03:50 CST - Focused Verification

Command:

```bash
uv run pytest -q backend/open_webui/test/agent/test_tool_authority.py backend/open_webui/test/agent/test_terminal_artifacts.py backend/open_webui/test/agent/test_approval.py backend/open_webui/test/agent/test_resources.py
```

Result:

- Exit code: 0
- `17 passed, 2 warnings`

Command:

```bash
python3 scripts/agent_mode/acceptance_harness.py dry-run && python3 scripts/agent_mode/acceptance_harness.py fixture
```

Result:

- Exit code: 0
- dry-run: `case contract: 0/12 satisfied`, live acceptance pending
- fixture: `case contract: 12/12 satisfied`, live acceptance pending

Command:

```bash
uv run pytest -q backend/open_webui/test/agent/test_w12_acceptance_harness.py
```

Result:

- Exit code: 0
- `4 passed`

Command:

```bash
uv run --frozen ruff check backend/open_webui/routers/agent_service.py backend/open_webui/test/agent/test_tool_authority.py
```

Result:

- Exit code: 0
- `All checks passed!`

Command:

```bash
python3 - <<'PY'
import json
from pathlib import Path
path = Path('handoff/agent-mode/w12b-tool-terminal-evidence.json')
data = json.loads(path.read_text())
expected = {
    'scenario_02_single_tool_call',
    'scenario_03_terminal_output_artifact',
    'scenario_04_tmp_artifact_retention',
    'scenario_05_destructive_approval',
    'scenario_10_cancel_keeps_terminal_process',
}
ids = {scenario['id'] for scenario in data['scenarios']}
assert ids == expected, ids
for scenario in data['scenarios']:
    assert scenario['status'] == 'incomplete', scenario
    assert scenario['live_status'] == 'not_proven', scenario
    assert scenario['evidence']['notes'], scenario
print('evidence json ok')
PY
```

Result:

- Exit code: 0
- `evidence json ok`

Command:

```bash
cd services/agentscope-runtime && uv run --extra test pytest -q tests/test_app.py tests/test_subagents.py tests/test_agentscope_bridge.py
```

Result:

- Exit code: 0
- `13 passed`

Generated-root `uv.lock` churn occurred during root `uv run`; it was reverted
because the diff was a broad dependency-resolution rewrite unrelated to this
task.

Final fresh reruns after lint:

- `uv run pytest -q backend/open_webui/test/agent/test_tool_authority.py backend/open_webui/test/agent/test_terminal_artifacts.py backend/open_webui/test/agent/test_approval.py backend/open_webui/test/agent/test_resources.py`
  - Exit code: 0
  - `17 passed, 2 warnings`
- `cd services/agentscope-runtime && uv run --extra test pytest -q tests/test_app.py tests/test_subagents.py tests/test_agentscope_bridge.py`
  - Exit code: 0
  - `13 passed`
- `python3 scripts/agent_mode/acceptance_harness.py dry-run && python3 scripts/agent_mode/acceptance_harness.py fixture`
  - Exit code: 0
  - dry-run: live acceptance pending
  - fixture: live acceptance pending
- `git diff --check`
  - Exit code: 0

### Scenario Status

| Scenario | Status | Live status | Notes |
| --- | --- | --- | --- |
| scenario_02_single_tool_call | incomplete | not_proven | Local normalized tool success is covered; no integrated `tool.requested` / `tool.completed` event capture. |
| scenario_03_terminal_output_artifact | incomplete | not_proven | Narrow service-helper wiring fixed and locally tested; no real Open Terminal command through integrated OpenWebUI service. |
| scenario_04_tmp_artifact_retention | incomplete | not_proven | Local tmp cleanup metadata is covered; no integrated completion/readback proving retained-after-completion. |
| scenario_05_destructive_approval | incomplete | not_proven | Local approval wait/state/result covered; no integrated approval UI/service capture. |
| scenario_10_cancel_keeps_terminal_process | incomplete | not_proven | Local runtime/resource tests cover cancel and no-kill behavior; no full AgentScope loop with real Open Terminal process was cancelled and read back. |

Evidence file:

- `handoff/agent-mode/w12b-tool-terminal-evidence.json`

## Notes

You are not alone in the codebase. Other W12B workers may be editing their own
worktrees. Do not revert their changes or broaden your scope.

No nested `open-terminal/` source was edited.
