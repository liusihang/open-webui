# W12B-3 Subagent And Model Selection Handoff

Date: 2026-06-18

## Goal

Produce real/local acceptance evidence for:

- scenario 06: leader creates concurrent subagents up to the cap;
- scenario 07: subagent model selection uses `meta.agent_selection`.

## Scope

Owns:

- subagent/model-selection acceptance investigation and narrow fixes required
  for scenarios 06 and 07;
- evidence file `handoff/agent-mode/w12b-subagents-evidence.json`;
- this handoff.

Do not touch:

- terminal/Open Terminal behavior;
- frontend layout/visual polish;
- broad OpenWebUI auth/model permission rewrites.

## Evidence Contract

Only mark a scenario `live_passed` when there is direct evidence from the
integrated services. If that is not possible, write `status: "incomplete"` and
explain why in `evidence.notes`.

Required observations:

- scenario 06: `event:subagent.created`, `subagent_concurrency:observed`,
  `subagent_cap:5`.
- scenario 07: `event:model.selection.requested`,
  `event:model.selection.completed`, `meta.agent_selection`.

## Verification Log

Pending.

## Notes

You are not alone in the codebase. Other W12B workers may be editing their own
worktrees. Do not revert their changes or broaden your scope.
