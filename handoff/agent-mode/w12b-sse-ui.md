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

Pending.

## Notes

You are not alone in the codebase. Other W12B workers may be editing their own
worktrees. Do not revert their changes or broaden your scope.
