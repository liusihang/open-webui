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

Pending.

## Notes

You are not alone in the codebase. Other W12B workers may be editing their own
worktrees. Do not revert their changes or broaden your scope.
