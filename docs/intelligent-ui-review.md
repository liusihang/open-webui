# Intelligent UI independent review

Reviewed against the Open WebUI v0.12.3 integration on 2026-10-17.

## Result and scope

No blocking issue remains in the reviewed code and focused tests. This review
covered the parser, native component renderer, Markdown and Responses rendering
integration, opt-in prompt, interaction permissions, persistence, and follow-up
submission guards. The expanded review also covered Grid, Tabs, DataTable,
MetricCard, scatter/heatmap charts, Select, NumberInput, and linked local controls.
It does not constitute a production deployment validation.

The implementation uses the actual `@json-render/svelte` `CatalogRenderer` with a
fixed native registry. It does not execute generated JavaScript or HTML. Exact
field validation rejects unknown components/properties, arbitrary actions,
dynamic bindings, and prototype-related keys. Payload, operation, node, depth,
and expanded-graph limits bound parsing and rendering work.

Completed JSONL operations render while the final record is still streaming.
Markdown preserves alternating prose and independent UI blocks. Block keys
retain the message ID, Responses output-item ID where applicable, and token
path, while excluding the chat ID. The inspected export/reload paths preserve
the specification and versioned interaction metadata.

Sliders stay local until the user clicks the follow-up button. The proposed
question is visible before submission. Read-only views can explore controls
without persisting state or submitting a model request. Invalid specifications
fall back to escaped source data.

Linked controls operate on bounded datasets within one UI fence. Shared values
update tables, charts, and fixed count/sum/mean/min/max aggregates without a model
request. Separate fences retain independent state. Empty counts return zero;
other aggregates with no matching numeric data show a no-data result. Invalid
restored options fall back to declared defaults, and numeric controls respect
their range and step. Table search and sorting are local, with stable ordering
and absent values last. Tabs support keyboard navigation and restored selection.

## Verified corrections

- Owner chats remain writable after reload even when the API labels them
  `share_mode: "continue"`.
- Debounced saves capture their original chat/message target and serialize
  requests, preventing navigation from dropping the pending save or redirecting
  it to another chat.
- A narrow, ownership-checked update changes only `message.meta.intelligent_ui`
  under the chat transaction. It preserves message content, other metadata,
  conversation branches, and timestamps, and cannot recreate a deleted message.
- Metadata storage uses the existing normalized message representation, avoiding
  the earlier top-level-field loss through the inspected fork path.
- Follow-ups have a message-level pending guard and recheck the chat/message
  target after awaiting persistence.
- Oversized integers fail state validation cleanly rather than overflowing the
  finite-number check.
- Selectable row IDs reject empty values and collisions after string
  normalization, including numeric/string collisions. Duplicate heatmap
  coordinates are rejected rather than silently overlapped or aggregated.
- Persistable option and selection strings reject control characters consistently
  with the backend. Nonselectable tables cannot shadow a shared selection provider.
- NumberInput retains partially typed values until change, blur, or Enter, then
  normalizes the committed value and updates linked controls. Sequential typing
  is covered by a mounted regression.
- Dataset charts disclose when they show the first 128 matching points while a
  table or aggregate includes more rows.

## Independently observed test results

- Parser and bounds: **102 passed**
- Dataset filtering, chart conversion, aggregation, sorting, and state: **39 passed**
- Markdown streaming seam: **6 passed**
- Server-rendered native components: **6 passed**
- Captured-target persistence queue: **4 passed**
- Ownership/read-only permissions: **3 passed**
- Mounted Svelte/jsdom interactions: **18 passed**
- Python opt-in prompt tests: **5 passed**
- Python state validator and atomic-update method tests: **7 passed**
- `git diff --check`: passed

Total: **178 frontend tests and 12 Python tests passed**. The Python atomic-update
tests execute the actual method extracted from the source with mocked storage;
they are not live database integration tests. The final visible follow-up label
was also inspected as escaped Svelte text.

## Remaining deployment checks

Exercise a running application with a real database and authenticated account:
change controls, navigate immediately, reload, export/import, and fork the chat.
Confirm shared/read-only behavior and slow-save follow-up navigation in an actual
browser. Test streamed and cancelled responses from the intended provider,
including separate Responses output items. No live provider credentials, live
database round trip, browser route journey, or production performance benchmark
was used in this focused review. Full production build results belong in the
separate integration validation report.
