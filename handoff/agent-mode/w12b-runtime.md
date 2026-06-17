# W12B-1 Runtime And Chat Path Handoff

Date: 2026-06-18

## Goal

Produce real/local acceptance evidence for:

- scenario 01: ordinary Q&A uses Agent Mode and streams final answer;
- scenario 09: final deltas only stream in the final-answer phase;
- scenario 12: runtime unavailable is a visible failure when Agent Mode is enabled.

## Scope

Owns:

- runtime/chat-path acceptance investigation and any narrow fixes required for
  scenarios 01, 09, and 12;
- evidence file `handoff/agent-mode/w12b-runtime-evidence.json`;
- this handoff.

Do not touch:

- terminal/Open Terminal behavior;
- subagent model-selection internals;
- frontend visual polish;
- unrelated root checkout files.

## Evidence Contract

Only mark a scenario `live_passed` when there is direct evidence from the
integrated services. If that is not possible, write `status: "incomplete"` and
explain why in `evidence.notes`.

Required observations:

- scenario 01: `event:run.running`, `event:final.started`,
  `event:final.delta`, `event:run.completed`, `no_tool_events`.
- scenario 09: `event:final.started_before_delta`,
  `final.delta_only_finalizing`, `no_action_after_final.started`.
- scenario 12: `ENABLE_AGENT_MODE:true`, `runtime_unavailable`,
  `event:run.failed`, `no_silent_legacy_fallback`.

## Verification Log

2026-06-18 W12B-1:

Read first, per assignment:

- `handoff/agent-mode/w12b-runtime.md`
- `scripts/agent_mode/acceptance_harness.py`
- `docs/runbooks/agent-mode-runtime-deployment.md`
- focused backend tests:
  - `backend/open_webui/test/agent/test_chat_entry_agent_mode.py`
  - `backend/open_webui/test/agent/test_events.py`
  - `backend/open_webui/test/agent/test_w12_acceptance_harness.py`
  - `backend/open_webui/test/models/test_agent_runs.py::test_final_started_helpers_and_final_text_accumulation`
- focused runtime service test:
  - `services/agentscope-runtime/tests/test_app.py`

Commands and results:

```bash
python3 scripts/agent_mode/acceptance_harness.py dry-run
python3 scripts/agent_mode/acceptance_harness.py fixture
```

Result: dry-run reported no scenario executed; fixture reported `12/12`
contract shape satisfied with live acceptance pending.

```bash
uv run pytest backend/open_webui/test/agent/test_chat_entry_agent_mode.py backend/open_webui/test/agent/test_events.py backend/open_webui/test/agent/test_w12_acceptance_harness.py -q
```

Result: interrupted after it sat in project build with no useful test output.
Root cause: root package build runs `hatch_build.py`, which performs
`npm install --force` and `npm run build`; this is too broad for focused
backend Agent Mode tests. A transient `uv.lock` resolver update caused by this
attempt was restored to HEAD before any handoff/code edits.

```bash
PYTHONPATH=backend WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false DATABASE_ENABLE_SESSION_SHARING=true \
uv run --no-project --python 3.12 --with-requirements backend/requirements.txt --with pytest --with pytest-asyncio \
pytest backend/open_webui/test/agent/test_events.py backend/open_webui/test/agent/test_w12_acceptance_harness.py -q
```

Result: `25 passed in 0.29s`.

```bash
PYTHONPATH=backend WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false DATABASE_ENABLE_SESSION_SHARING=true \
uv run --no-project --python 3.12 --with-requirements backend/requirements.txt --with pytest --with pytest-asyncio \
pytest backend/open_webui/test/agent/test_chat_entry_agent_mode.py -q
```

Result before fix baseline: `6 passed, 19 warnings in 88.08s`.

```bash
PYTHONPATH=backend WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false DATABASE_ENABLE_SESSION_SHARING=true \
uv run --no-project --python 3.12 --with-requirements backend/requirements.txt --with pytest --with pytest-asyncio \
pytest backend/open_webui/test/models/test_agent_runs.py::test_final_started_helpers_and_final_text_accumulation -q
```

Result: `1 passed, 1 warning in 1.49s`.

```bash
cd services/agentscope-runtime
uv run --frozen --extra test pytest tests/test_app.py -q
```

Result: `7 passed in 0.92s` before the fix, then `7 passed in 0.26s`
after the fix.

RED for scenario 12 required observation:

```bash
PYTHONPATH=backend WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false DATABASE_ENABLE_SESSION_SHARING=true \
uv run --no-project --python 3.12 --with-requirements backend/requirements.txt --with pytest --with pytest-asyncio \
pytest backend/open_webui/test/agent/test_chat_entry_agent_mode.py::test_agent_mode_runtime_unavailable_marks_run_failed_and_visible -q
```

Result before implementation: failed because `AgentRuns.list_events(run.id)`
returned `[]`; runtime-unavailable path marked the run failed and surfaced the
assistant message error, but did not append `run.failed`.

Narrow fix:

- `backend/open_webui/main.py`: in `_start_agent_mode_chat`, after runtime
  start failure transitions the run from `queued` to `failed`, append an Agent
  Run event with `event_type=run.failed`, `phase=failed`, and the same error
  payload returned to the UI.
- `backend/open_webui/test/agent/test_chat_entry_agent_mode.py`: extended the
  runtime-unavailable test to assert the `run.failed` event payload.

GREEN after fix:

```bash
PYTHONPATH=backend WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false DATABASE_ENABLE_SESSION_SHARING=true \
uv run --no-project --python 3.12 --with-requirements backend/requirements.txt --with pytest --with pytest-asyncio \
pytest backend/open_webui/test/agent/test_chat_entry_agent_mode.py::test_agent_mode_runtime_unavailable_marks_run_failed_and_visible -q
```

Result: `1 passed, 8 warnings in 7.52s`.

Final focused verification:

```bash
PYTHONPATH=backend WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false DATABASE_ENABLE_SESSION_SHARING=true \
uv run --no-project --python 3.12 --with-requirements backend/requirements.txt --with pytest --with pytest-asyncio \
pytest backend/open_webui/test/agent/test_chat_entry_agent_mode.py backend/open_webui/test/agent/test_events.py backend/open_webui/test/agent/test_w12_acceptance_harness.py backend/open_webui/test/models/test_agent_runs.py::test_final_started_helpers_and_final_text_accumulation -q
```

Result: `32 passed, 8 warnings in 7.80s`.

```bash
cd services/agentscope-runtime
uv run --frozen --extra test pytest tests/test_app.py -q
```

Result: `7 passed in 0.26s`.

Evidence file:

- Created `handoff/agent-mode/w12b-runtime-evidence.json`.
- It contains only `scenario_01_ordinary_qa`,
  `scenario_09_final_phase_deltas`, and
  `scenario_12_runtime_unavailable_failure`.
- All three are marked `status: incomplete` and
  `live_status: not_proven`.

## Notes

You are not alone in the codebase. Other W12B workers may be editing their own
worktrees. Do not revert their changes or broaden your scope.

Findings:

- Scenario 01 remains incomplete for live acceptance. Supporting evidence shows
  Agent Mode chat entry creates a run and the runtime service skeleton appends
  `run.running`, but no direct integrated-service ordinary Q&A captured
  `final.started`, `final.delta`, and `run.completed`. The runtime service
  implementation read in this worktree only appends `run.running` on start.
- Scenario 09 remains incomplete for live acceptance. Supporting backend tests
  prove `final.delta` is accepted only after `final.started` while the run is
  finalizing, and tool/subagent/artifact/model events are rejected after
  `final.started`; however, no direct integrated-service final-answer stream was
  captured.
- Scenario 12 remains incomplete for live acceptance, but the narrow missing
  backend observation was fixed. Supporting tests now cover
  `ENABLE_AGENT_MODE:true`, `runtime_unavailable`, `event:run.failed`, and
  `no_silent_legacy_fallback`. It is not marked `live_passed` because there is
  no direct request against a running integrated OpenWebUI service and
  unavailable runtime in this handoff.
