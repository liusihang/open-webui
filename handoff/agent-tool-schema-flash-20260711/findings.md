# Findings

## Initial report

- User observed the full English description of a `write_file` tool.
- The block sometimes appears temporarily and disappears later.
- Exact chat supplied: `f251c1ab-4ee1-4142-99c4-279220d4379b`.

## Existing hypothesis to verify

The content may be either a persisted model `text.delta`/provider auxiliary
event, or a frontend-only in-flight preview that is replaced during event
reconciliation. This remains unconfirmed until the exact run is inspected.

## Browser evidence

- A fresh independent browser had no authentication and redirected to `/auth`.
- The authenticated in-app browser opened the exact chat successfully.
- The persisted/reloaded transcript does **not** contain the English
  `Write complete text content to a file.` description.
- The transcript does contain structured events for `write_file`:
  - an initial failed call to
    `/workspace/agent-runs/456d784f-a621-4658-8dee-d4e3923fae48/outputs/tool_test.txt`;
  - approval and a later completed `write_file` call;
  - a second approved path `/home/user/tool_test.txt`.
- The final answer is persisted separately and contains no leaked tool schema.
- Browser setup emitted unrelated Statsig telemetry network errors, but the
  target OpenWebUI page loaded and rendered normally.

## Interim conclusion

The reported description is absent after reload. The remaining question is
whether it ever existed as a persisted Agent event and was filtered during
backfill, or existed only in the live frontend/model stream.

## Confirmed backend event sequence

- Chat has two completed Agent runs. The affected run is
  `456d784f-a621-4658-8dee-d4e3923fae48` and has 31 persisted events.
- It has no `text.delta` events. Therefore this symptom is not commentary,
  provider auxiliary text, or final-answer streaming.
- Persisted event 12 and event 16 are `tool.requested` for `write_file`.
- Both events store the complete tool schema description in the event `summary`.
- Completion/failure events use short lifecycle summaries such as
  `Write file completed.` and `Write file failed.`

## Confirmed source path

- `OpenWebUIToolProxy.__call__` emits `tool.requested` with
  `summary=self.description or f"{self.name} requested."`.
- The description is the complete tool schema description passed into the
  proxy when the toolkit is built.
- The frontend groups tool lifecycle events by `tool_call_id`. While only the
  request exists, `lastItem.summary` is the full description. After a terminal
  event arrives, `lastItem.summary` becomes the short completed/failed text.
- `ToolPart.svelte` uses that summary as the running action label, which makes
  the full schema visible temporarily.

## Root cause

The tool contract description is being overloaded as a user-facing lifecycle
summary. The disappearance is deterministic lifecycle replacement, not lost
streaming data and not event-replay deletion.
