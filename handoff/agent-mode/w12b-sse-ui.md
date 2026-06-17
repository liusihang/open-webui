# W12B-4 SSE, UI, Reconnect, And Compaction Handoff

Date: 2026-06-18

## Goal

Produce real/local acceptance evidence for:

- scenario 08: SSE reconnect backfills by event sequence;
- scenario 11: terminal states trigger compaction and the summary retains
  expandable UI details.

Also verify that Agent Mode messages do not double-render socket and SSE final
content.

## Scope

Owns:

- SSE/UI/reconnect/compaction acceptance investigation and narrow fixes required
  for scenarios 08 and 11;
- evidence file `handoff/agent-mode/w12b-sse-ui-evidence.json`;
- this handoff.

Do not touch:

- runtime subagent internals;
- terminal artifact registration logic except through read-only verification;
- broad `Chat.svelte` rewrites beyond a narrowly proven duplicate-render fix.

## Evidence Contract

Only mark a scenario `live_passed` when there is direct evidence from the
integrated services. If that is not possible, write `status: "incomplete"` and
explain why in `evidence.notes`.

Required observations:

- scenario 08: `last_event_id_reconnect`, `backfill_by_seq`, `dedupe_seq`.
- scenario 11: `compaction:completed`, `compaction:failed`,
  `compaction:cancelled`, `compaction:budget_exceeded`,
  `summary_retains_expandable_ui`.

## Verification Log

- 2026-06-18 03:57:11 CST - Started W12B-4 in
  `/Users/liusihang/openwebui/.worktrees/agent-mode-w12b-sse-ui` on branch
  `codex/agent-mode-w12b-sse-ui`.
  - `git status --short --branch` showed clean branch
    `codex/agent-mode-w12b-sse-ui`.
  - Read this handoff, `scripts/agent_mode/acceptance_harness.py`, and
    `docs/runbooks/agent-mode-runtime-deployment.md`.
  - Harness finding: live mode requires top-level `mode: "live"` and each
    scenario to have `status` in `{"live_passed", "passed"}` plus
    `live_status: "passed"`; fixture/dry-run/unit tests cannot prove W12B live
    acceptance.
  - Scenario 08 code path found:
    `backend/open_webui/agent/events.py` resolves `after_seq` and
    `Last-Event-ID`; `backend/open_webui/routers/agent_runs.py` exposes
    `/agent/runs/{run_id}/events/list?after_seq=` and SSE
    `/agent/runs/{run_id}/events`; `src/lib/apis/agentRuns/index.ts` builds
    both URLs; `src/lib/components/chat/AgentEvents/eventFold.ts` tracks
    `seenSeqs` and final-delta keys; `eventFold.test.ts` already covers replay
    dedupe and unseen lower-seq backfill.
  - Duplicate-prevention code path found:
    `src/lib/components/chat/historySync.ts` blocks socket incremental content
    events (`chat:completion`, `chat:message:delta`, `message`) for messages
    with `agent_run_id`; `Chat.svelte` consults that helper before appending
    socket content; `historySync.test.ts` covers the Agent Mode and legacy
    branches.
  - Scenario 11 code path found:
    `backend/open_webui/agent/resources.py` calls a provided `compact` callback
    exactly once during terminal-state cleanup and preserves terminal process
    refs; `backend/open_webui/agent/compaction.py` builds summaries retaining
    participants, actions, tools, approvals, subagents, artifacts, process refs,
    budget, errors, and warnings; `test_resources.py` and
    `test_compaction.py` cover those pieces.
  - Next checkpoint: run focused backend/frontend tests, then attempt local
    API/SSE-level evidence for scenarios 08 and 11. Only mark live passed if
    integrated service evidence is actually available.
- 2026-06-18 04:04:04 CST - Focused verification and evidence file completed.
  - Backend command:
    `uv run --group dev pytest backend/open_webui/test/agent/test_events.py backend/open_webui/test/agent/test_compaction.py backend/open_webui/test/agent/test_resources.py`
    - Result: `25 passed in 0.53s`.
    - Note: `uv run` created `.venv` with CPython 3.12.13. It also refreshed
      `uv.lock` as a side effect; since the branch was clean before this worker
      started and that lockfile change was unrelated to W12B evidence, restored
      only `uv.lock` to HEAD.
  - Frontend environment command:
    `npm ci`
    - Result: failed with `EBADENGINE` because default `node` was `v25.8.0`
      and project requires `>=18.13.0 <=22.x.x`.
  - Frontend retry commands:
    - `PATH="$HOME/.nvm/versions/node/v22.22.0/bin:$PATH" node --version`
      returned `v22.22.0`.
    - `PATH="$HOME/.nvm/versions/node/v22.22.0/bin:$PATH" npm --version`
      returned `10.9.4`.
    - `PATH="$HOME/.nvm/versions/node/v22.22.0/bin:$PATH" npm ci`
      succeeded, adding 1124 packages. npm reported existing audit findings:
      `27 vulnerabilities (8 low, 13 moderate, 5 high, 1 critical)`.
  - Frontend test command:
    `PATH="$HOME/.nvm/versions/node/v22.22.0/bin:$PATH" npx vitest run src/lib/components/chat/AgentEvents/eventFold.test.ts src/lib/components/chat/historySync.test.ts`
    - Result: `2 passed` test files, `19 passed` tests.
    - Coverage relevant to this worker: seq replay/dedupe in AgentEvents fold
      and socket incremental-content suppression for Agent Mode messages.
  - Harness commands:
    - `python3 scripts/agent_mode/acceptance_harness.py dry-run`
      returned `case contract: 0/12 satisfied`, `live acceptance: pending`,
      `failures: none`.
    - `python3 scripts/agent_mode/acceptance_harness.py fixture`
      returned `case contract: 12/12 satisfied`, `live acceptance: pending`,
      `failures: none`.
  - Scenario 08 local route command:
    `WEBUI_SECRET_KEY=w12b-local-route-secret uv run python3 - <<'PY' ... FastAPI TestClient against open_webui.routers.agent_runs ... PY`
    - First attempt without `WEBUI_SECRET_KEY` failed at auth import with:
      `WEBUI_SECRET_KEY is not set. It is a hard requirement when authentication is enabled.`
    - Retry with test-only `WEBUI_SECRET_KEY` passed.
    - Evidence: `/agent/runs/run-1/events/list?after_seq=2` returned seq
      `[3, 4]`; SSE `/agent/runs/run-1/events` with `Last-Event-ID: 2`
      returned ids `3` and `4` only; invalid `Last-Event-ID: not-an-int`
      returned HTTP `400`.
    - Limitation: this is local FastAPI route evidence with a fake event store,
      not a running full OpenWebUI + AgentScope + browser stack.
  - Scenario 11 local lifecycle command:
    `.venv/bin/python - <<'PY' ... AgentRunResourceManager cleanup_terminal_state plus build_compacted_run_summary assertions ... PY`
    - Result: passed.
    - Evidence: terminal states `completed`, `failed`, `cancelled`, and
      `budget_exceeded` each invoked the compaction callback exactly once.
      Compacted summary retained expandable UI keys:
      `actions`, `approvals`, `artifacts`, `budget`, `errors`,
      `participants`, `process_refs`, `subagents`, `tools`, `warnings`.
    - Limitation: this is local lifecycle/summary code evidence, not a real
      terminal runtime callback with persisted Agent Run summary.
  - Integrated-service availability checks:
    - `lsof -nP -iTCP -sTCP:LISTEN | rg '(:8080|:5173|:8097|open-webui|uvicorn|vite)'`
      returned no matches.
    - `curl -fsS --max-time 2 http://127.0.0.1:8080/health` failed to connect.
    - `curl -fsS --max-time 2 http://127.0.0.1:8097/health` failed to connect.
    - Conclusion: direct integrated-service acceptance was not available in
      this worktree/session; evidence must remain `live_status: "not_proven"`.
  - Final evidence validation commands:
    - `python3 -m json.tool handoff/agent-mode/w12b-sse-ui-evidence.json >/tmp/w12b-sse-ui-evidence.pretty.json`
      succeeded.
    - `git diff --check` succeeded.
    - `python3 scripts/agent_mode/acceptance_harness.py live --evidence handoff/agent-mode/w12b-sse-ui-evidence.json`
      failed as expected because this worker's evidence file is intentionally
      `mode: local_evidence_not_live`, contains only scenarios 08 and 11, and
      marks both as `status: "incomplete"` / `live_status: "not_proven"`.
      The harness reported `case contract: 0/12 satisfied` and listed missing
      evidence for the other ten scenarios.
    - `git status --short` showed only:
      `M handoff/agent-mode/w12b-sse-ui.md` and
      `?? handoff/agent-mode/w12b-sse-ui-evidence.json`.
    - Stored result/path summary in mem0 for future analysis; mem0 returned
      queued event id `1bfb8aa2-5604-4db1-8dad-425a0885786c`.

## Current Status

- Created `handoff/agent-mode/w12b-sse-ui-evidence.json` with only
  `scenario_08_sse_reconnect_backfill` and
  `scenario_11_terminal_state_compaction`.
- Both scenarios are marked `status: "incomplete"` and
  `live_status: "not_proven"` because no direct integrated-service evidence was
  available.
- Duplicate-prevention behavior is verified locally by
  `src/lib/components/chat/historySync.test.ts`: Agent Mode messages ignore
  socket incremental content events while allowing replace-style final socket
  updates.
- No narrow product bug was found in this worker scope, so no code fix and no
  commit were made.

## Exact Inline Commands

Scenario 08 route-level command that passed:

```bash
WEBUI_SECRET_KEY=w12b-local-route-secret uv run python3 - <<'PY'
from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace

from open_webui.agent.protocol import AgentEventType, AgentRunEvent, AgentRunState
from open_webui.routers import agent_runs
from open_webui.utils.auth import get_verified_user

class FakeStore:
    def __init__(self):
        self.events = [
            AgentRunEvent(run_id='run-1', seq=1, event_type=AgentEventType.RUN_RUNNING, summary='started', payload={}, created_at=1),
            AgentRunEvent(run_id='run-1', seq=2, event_type=AgentEventType.ACTION_SUMMARY, summary='step 1', payload={}, created_at=2),
            AgentRunEvent(run_id='run-1', seq=3, event_type=AgentEventType.TOOL_COMPLETED, summary='tool done', payload={'tool_name': 'search'}, created_at=3),
            AgentRunEvent(run_id='run-1', seq=4, event_type=AgentEventType.RUN_COMPLETED, summary='completed', payload={}, created_at=4),
        ]

    def get_run_state(self, run_id: str):
        return AgentRunState.COMPLETED

    def has_final_started(self, run_id: str):
        return False

    def append_event(self, event):
        raise NotImplementedError

    def list_events_after(self, run_id: str, after_seq: int = 0):
        return [event for event in self.events if event.run_id == run_id and event.seq > after_seq]

    def append_final_text_delta(self, run_id: str, final_stream_id: str, delta_index: int, delta: str):
        raise NotImplementedError

app = FastAPI()
app.state.AGENT_EVENT_STORE = FakeStore()
app.include_router(agent_runs.router, prefix='/agent/runs')
app.dependency_overrides[get_verified_user] = lambda: SimpleNamespace(id='user-1')
client = TestClient(app)

list_response = client.get('/agent/runs/run-1/events/list?after_seq=2')
assert list_response.status_code == 200, list_response.text
list_payload = list_response.json()
list_seqs = [event['seq'] for event in list_payload['events']]
assert list_seqs == [3, 4], list_payload
assert list_payload['last_seq'] == 4, list_payload

sse_response = client.get('/agent/runs/run-1/events', headers={'Last-Event-ID': '2'})
assert sse_response.status_code == 200, sse_response.text
sse_body = sse_response.text
assert 'id: 3\n' in sse_body and 'id: 4\n' in sse_body, sse_body
assert 'id: 1\n' not in sse_body and 'id: 2\n' not in sse_body, sse_body

invalid_response = client.get('/agent/runs/run-1/events', headers={'Last-Event-ID': 'not-an-int'})
assert invalid_response.status_code == 400, invalid_response.text

print('scenario_08_local_route_evidence')
print({'events_list_after_seq_2': list_seqs, 'sse_last_event_id_2_contains_ids': [3, 4], 'invalid_last_event_id_status': invalid_response.status_code})
PY
```

Scenario 11 lifecycle/summary command that passed:

```bash
.venv/bin/python - <<'PY'
import asyncio
from types import SimpleNamespace

from open_webui.agent.compaction import build_compacted_run_summary
from open_webui.agent.protocol import AgentEventType, AgentRunState
from open_webui.agent.resources import AgentRunResourceManager


def event(seq, event_type, *, summary=None, participant_id='leader', payload=None):
    return SimpleNamespace(
        seq=seq,
        event_type=event_type,
        participant_id=participant_id,
        phase=None,
        summary=summary,
        payload=payload or {},
        created_at=seq,
    )


def artifact(path):
    return SimpleNamespace(
        id=path.rsplit('/', 1)[-1],
        kind='file',
        terminal_server_id='terminal-main',
        path=path,
        url=f'/api/artifacts/{path.rsplit("/", 1)[-1]}',
        mime_type='text/plain',
        size=12,
        metadata={},
        created_at=100,
    )


async def main():
    terminal_states = [
        AgentRunState.COMPLETED,
        AgentRunState.FAILED,
        AgentRunState.CANCELLED,
        AgentRunState.BUDGET_EXCEEDED,
    ]
    compacted = {}

    for state in terminal_states:
        run_id = f'run-{state.value}'
        manager = AgentRunResourceManager()
        manager.register_terminal_process(
            run_id,
            {
                'terminal_server_id': 'terminal-main',
                'process_id': f'proc-{state.value}',
                'status': 'running',
            },
        )
        calls = {'compact': 0}

        def compact():
            calls['compact'] += 1
            return {'state': state.value, 'ui': {'tools': [{'name': 'run_command'}]}}

        first = await manager.cleanup_terminal_state(run_id, state, compact=compact)
        second = await manager.cleanup_terminal_state(run_id, state, compact=compact)
        assert first.cleaned is True
        assert second.cleaned is False
        assert calls['compact'] == 1
        assert first.summary == {'state': state.value, 'ui': {'tools': [{'name': 'run_command'}]}}
        assert first.retained_process_refs == [
            {
                'terminal_server_id': 'terminal-main',
                'process_id': f'proc-{state.value}',
                'status': 'running',
            }
        ]
        compacted[state.value] = calls['compact']

    run = SimpleNamespace(
        id='run-completed',
        state='completed',
        participants=[{'id': 'leader'}, {'id': 'subagent-1'}],
        process_refs=[{'process_id': 'proc-completed', 'status': 'running'}],
        budget={'max_tool_calls': 5, 'tool_calls_used': 2},
        error={'code': 'none'},
        final_text='Final answer.',
    )
    summary = build_compacted_run_summary(
        run=run,
        events=[
            event(1, AgentEventType.ACTION_SUMMARY, summary='Investigated'),
            event(
                2,
                AgentEventType.TOOL_COMPLETED,
                summary='Command completed',
                payload={
                    'tool_name': 'run_command',
                    'arguments_summary': 'python job.py',
                    'result_status': 'success',
                    'artifacts': [{'path': '/workspace/agent-runs/run-completed/outputs/report.txt'}],
                    'process_refs': [{'process_id': 'proc-completed', 'status': 'running'}],
                    'warnings': [{'code': 'still_running'}],
                },
            ),
            event(3, AgentEventType.APPROVAL_REQUESTED, summary='Approval requested', payload={'approval_id': 'approval-1'}),
            event(4, AgentEventType.SUBAGENT_COMPLETED, participant_id='subagent-1', summary='Subagent done', payload={'status': 'completed'}),
            event(5, AgentEventType.RUN_COMPLETED, summary='Completed'),
        ],
        artifacts=[
            artifact('/workspace/agent-runs/run-completed/outputs/report.txt'),
            artifact('/workspace/agent-runs/run-completed/tmp/scratch.json'),
        ],
        now_ns=1_718_000_000_000_000_000,
    )

    assert summary['ui']['participants'] == run.participants
    assert summary['ui']['actions'][0]['summary'] == 'Investigated'
    assert summary['ui']['tools'][0]['name'] == 'run_command'
    assert summary['ui']['approvals'][0]['approval_id'] == 'approval-1'
    assert summary['ui']['subagents'][0]['participant_id'] == 'subagent-1'
    assert summary['ui']['artifacts'][1]['metadata']['cleanup_eligible'] is True
    assert summary['ui']['process_refs'] == run.process_refs
    assert summary['ui']['budget'] == run.budget
    assert summary['ui']['warnings'] == [{'code': 'still_running'}]

    print('scenario_11_local_lifecycle_evidence')
    print({'compaction_calls_by_terminal_state': compacted, 'summary_ui_keys': sorted(summary['ui'].keys())})

asyncio.run(main())
PY
```

## Notes

You are not alone in the codebase. Other W12B workers may be editing their own
worktrees. Do not revert their changes or broaden your scope.
