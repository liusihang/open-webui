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

Pending.

## Notes

You are not alone in the codebase. Other W12B workers may be editing their own
worktrees. Do not revert their changes or broaden your scope.
