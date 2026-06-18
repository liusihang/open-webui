# W13-1 Evidence Integrity Audit

Date: 2026-06-18
Worker: W13-1 Evidence Integrity auditor
Worktree: `/Users/liusihang/openwebui/.worktrees/agent-mode-w13-evidence-integrity`
Branch: `codex/agent-mode-w13-evidence-integrity`
Base commit: `00481b7ab`

## Goal

Prove whether the merged W12D live evidence is complete, traceable, and live, or
identify exact gaps. This is a read-mostly audit. Product code was not modified.

## Scope

Allowed write scope:

- `handoff/agent-mode/w13-evidence-integrity.md`

Files inspected:

- `handoff/agent-mode/controller.md`
- `handoff/agent-mode/w12d-merged-live-evidence.json`
- `handoff/agent-mode/w12d-runtime-chat-evidence.json`
- `handoff/agent-mode/w12d-tool-terminal-evidence.json`
- `handoff/agent-mode/w12d-subagents-evidence.json`
- `handoff/agent-mode/w12d-sse-ui-evidence.json`
- `scripts/agent_mode/acceptance_harness.py`
- `scripts/agent_mode/merge_w12_evidence.py`
- `/Users/liusihang/openwebui/docs/plans/2026-06-17-openwebui-agent-mode-agentscope-implementation.md`

## Commands And Results

```bash
pwd && git status --short --branch
```

Result:

```text
/Users/liusihang/openwebui/.worktrees/agent-mode-w13-evidence-integrity
## codex/agent-mode-w13-evidence-integrity
```

```bash
git log --oneline --decorate -5
git rev-parse HEAD
git merge-base --is-ancestor 00481b7ab HEAD; echo base_ancestor_status:$?
```

Result:

```text
00481b7ab (HEAD -> codex/agent-mode-w13-evidence-integrity, ...) docs(agent-mode): refresh release readiness plan
098f7ec18 docs(agent-mode): record w12d subagent and merged evidence
89033c15a Add W12D subagent harness scenarios
60a5cc679 Validate W12D subagent acceptance
8fa8db178 docs(agent-mode): record w12d sse ui integration
00481b7abb4194eb2dc27753904621b806d32d9f
base_ancestor_status:0
```

```bash
python3 - <<'PY'
import json
from pathlib import Path
sources=[Path(p) for p in [
  'handoff/agent-mode/w12d-runtime-chat-evidence.json',
  'handoff/agent-mode/w12d-tool-terminal-evidence.json',
  'handoff/agent-mode/w12d-subagents-evidence.json',
  'handoff/agent-mode/w12d-sse-ui-evidence.json',
]]
merged=Path('handoff/agent-mode/w12d-merged-live-evidence.json')
source_map={}
for p in sources:
    d=json.loads(p.read_text())
    for s in d.get('scenarios',[]):
        source_map.setdefault(s.get('id'),[]).append(str(p))
md=json.loads(merged.read_text())
print('merged_mode', md.get('mode'))
print('merged_base_commit', md.get('base_commit'))
print('merged_count', len(md.get('scenarios',[])))
for s in md.get('scenarios',[]):
    ev=s.get('evidence') or {}
    print('\t'.join([s.get('id',''), s.get('status',''), s.get('live_status',''), ','.join(source_map.get(s.get('id'),[])) or 'NO_SOURCE', ','.join(sorted(ev.keys())[:8])]))
print('missing_from_sources', sorted(set(x.get('id') for x in md.get('scenarios',[]))-set(source_map)))
print('source_not_merged', sorted(set(source_map)-set(x.get('id') for x in md.get('scenarios',[]))))
PY
```

Result:

```text
merged_mode live
merged_base_commit 89033c15a
merged_count 12
scenario_01_ordinary_qa live_passed passed handoff/agent-mode/w12d-runtime-chat-evidence.json
scenario_02_single_tool_call live_passed passed handoff/agent-mode/w12d-tool-terminal-evidence.json
scenario_03_terminal_output_artifact live_passed passed handoff/agent-mode/w12d-tool-terminal-evidence.json
scenario_04_tmp_artifact_retention live_passed passed handoff/agent-mode/w12d-tool-terminal-evidence.json
scenario_05_destructive_approval live_passed passed handoff/agent-mode/w12d-tool-terminal-evidence.json
scenario_06_subagent_cap_concurrency live_passed passed handoff/agent-mode/w12d-subagents-evidence.json
scenario_07_subagent_model_selection live_passed passed handoff/agent-mode/w12d-subagents-evidence.json
scenario_08_sse_reconnect_backfill live_passed passed handoff/agent-mode/w12d-sse-ui-evidence.json
scenario_09_final_phase_deltas live_passed passed handoff/agent-mode/w12d-runtime-chat-evidence.json
scenario_10_cancel_keeps_terminal_process live_passed passed handoff/agent-mode/w12d-tool-terminal-evidence.json
scenario_11_terminal_state_compaction live_passed passed handoff/agent-mode/w12d-sse-ui-evidence.json
scenario_12_runtime_unavailable_failure live_passed passed handoff/agent-mode/w12d-runtime-chat-evidence.json
missing_from_sources []
source_not_merged []
```

```bash
uv run --frozen python scripts/agent_mode/acceptance_harness.py live --evidence handoff/agent-mode/w12d-merged-live-evidence.json
```

Result:

```text
Using CPython 3.12.13
Creating virtual environment at: .venv
Built open-webui @ file:///Users/liusihang/openwebui/.worktrees/agent-mode-w13-evidence-integrity
Installed 306 packages in 2.03s
Agent Mode W12 acceptance harness
mode: live
case contract: 12/12 satisfied
live acceptance: passed
message: Live W12B acceptance evidence satisfies all 12 MVP scenarios.
failures: none
```

```bash
jq -r '.limitations[]?' handoff/agent-mode/w12d-subagents-evidence.json
```

Result:

```text
The runtime HTTP service exposes run start/cancel/status but no public subagent-plan endpoint; W12D-3 drives AgentScopeSubagentAdapter directly against live OpenWebUI callbacks after public runtime start.
```

```bash
jq '.notes? // empty, .service_checks? // empty' handoff/agent-mode/w12d-tool-terminal-evidence.json
```

Result summary:

- Backend/runtime/Open Terminal health checks were present and passed.
- The tool-terminal worker states that the backend used an acceptance-only
  in-memory `AGENT_TOOL_REGISTRY`; no product code was changed.
- Because `/workspace` is read-only on this host, logical `/workspace/...`
  artifact paths were persisted while corroborating files were written under
  `/private/tmp/openwebui-agent-mode-w12d-tool-runtime/workspace`.

## Traceability Matrix

| Scenario | Source evidence | Harness status | Evidence strength |
| --- | --- | --- | --- |
| `scenario_01_ordinary_qa` | `w12d-runtime-chat-evidence.json` | `live_passed` / `passed` | Live integrated chat/runtime/provider callback evidence. |
| `scenario_02_single_tool_call` | `w12d-tool-terminal-evidence.json` | `live_passed` / `passed` | Live backend/runtime evidence, but tool registry was acceptance-only in-memory. Product-path strength is weaker than persisted/normal product tool discovery. |
| `scenario_03_terminal_output_artifact` | `w12d-tool-terminal-evidence.json` | `live_passed` / `passed` | Live backend/runtime/Open Terminal evidence with persisted logical artifact plus physical file corroboration under `/private/tmp`; host `/workspace` writeability caveat is explicit. |
| `scenario_04_tmp_artifact_retention` | `w12d-tool-terminal-evidence.json` | `live_passed` / `passed` | Same live terminal path as scenario 03; retention proof is live, with the same host path caveat. |
| `scenario_05_destructive_approval` | `w12d-tool-terminal-evidence.json` | `live_passed` / `passed` | Live backend/runtime approval events and normalized results; same acceptance-only tool registry caveat. |
| `scenario_06_subagent_cap_concurrency` | `w12d-subagents-evidence.json` | `live_passed` / `passed` | Live backend/runtime health and public runtime start were used, but subagent planning was adapter-direct because no public subagent-plan endpoint exists. This is not fixture/unit evidence, but it is not a full public runtime HTTP product-path proof. |
| `scenario_07_subagent_model_selection` | `w12d-subagents-evidence.json` | `live_passed` / `passed` | Same W12D-3 limitation as scenario 06; model-selection callback/catalog behavior is live, but subagent orchestration is adapter-direct. |
| `scenario_08_sse_reconnect_backfill` | `w12d-sse-ui-evidence.json` | `live_passed` / `passed` | Live backend SSE plus browser UI reload proof; includes `text/event-stream`, Last-Event-ID/backfill/dedupe, and screenshot path. |
| `scenario_09_final_phase_deltas` | `w12d-runtime-chat-evidence.json` | `live_passed` / `passed` | Live integrated runtime/chat event-order proof. |
| `scenario_10_cancel_keeps_terminal_process` | `w12d-tool-terminal-evidence.json` | `live_passed` / `passed` | Live backend/runtime/Open Terminal process-ref proof; long process was manually killed after evidence capture. |
| `scenario_11_terminal_state_compaction` | `w12d-sse-ui-evidence.json` | `live_passed` / `passed` | Live backend compaction/state evidence for completed, failed, cancelled, and budget_exceeded terminal states. The trigger is direct `AgentRuns.transition_state` terminal-target compaction, so it is narrower than a full natural Open Terminal end-to-end run for every terminal state. |
| `scenario_12_runtime_unavailable_failure` | `w12d-runtime-chat-evidence.json` | `live_passed` / `passed` | Live chat path visible failure proof; no silent legacy fallback. |

## Findings

1. Completeness: GO for the harness contract. The merged evidence contains all
   12 expected scenario IDs, has `mode: live`, and the live harness reports
   `12/12 satisfied` with `failures: none`.

2. Traceability: GO. Every merged scenario traces to exactly one W12D source
   evidence file. There are no merged scenarios missing from source evidence and
   no source scenarios missing from the merged evidence.

3. Fixture/unit-only evidence: none found. No scenario is proven only by the
   W12 fixture or by unit tests.

4. Weaker-than-full-product-path evidence:
   - `scenario_02_single_tool_call`, `scenario_05_destructive_approval`: live
     backend/runtime evidence, but W12D-2 used an acceptance-only in-memory
     `AGENT_TOOL_REGISTRY`.
   - `scenario_03_terminal_output_artifact`, `scenario_04_tmp_artifact_retention`:
     live terminal/artifact evidence, but physical writes are corroborated under
     `/private/tmp/...` because host `/workspace` is read-only; logical
     `/workspace/...` paths are persisted by OpenWebUI.
   - `scenario_06_subagent_cap_concurrency`, `scenario_07_subagent_model_selection`:
     live callbacks and health checks, but the subagent adapter is driven
     directly after public runtime start because the runtime HTTP service lacks
     a public subagent-plan endpoint.
   - `scenario_11_terminal_state_compaction`: live backend compaction evidence,
     but the proof triggers `AgentRuns.transition_state` directly for terminal
     states instead of proving every state through a natural full Open Terminal
     product flow.

## Go / No-Go

GO for W12D evidence integrity as a merged live acceptance contract: complete,
traceable, and accepted by the live harness.

Conditional GO for release readiness: the evidence is not fixture-only or
unit-only, but the product-path caveats above should be called out explicitly
before PR handoff, especially the acceptance-only tool registry and
adapter-direct subagent plan proof.

## Final Workspace State

At the time this handoff was written, the only intended file change is this
handoff file. `uv run --frozen` created `.venv` for the worktree but did not
surface tracked `uv.lock` changes in `git status`.
