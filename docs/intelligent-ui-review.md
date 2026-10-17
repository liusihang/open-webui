# Intelligent UI independent review

Reviewed against the Open WebUI v0.12.3 integration on 2026-10-17.

## Result and scope

No blocking issue remains in the reviewed code and focused tests. This review
covered the parser, native component renderer, Markdown and Responses rendering
integration, opt-in prompt, interaction permissions, persistence, and follow-up
submission guards. It does not constitute a production deployment validation.

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

## Independently observed test results

- Parser and bounds: **75 passed**
- Markdown streaming seam: **5 passed**
- Server-rendered native components: **6 passed**
- Captured-target persistence queue: **4 passed**
- Ownership/read-only permissions: **3 passed**
- Mounted Svelte/jsdom interactions: **7 passed**
- Python opt-in prompt tests: **5 passed**
- Python state validator and atomic-update method tests: **6 passed**
- `git diff --check`: passed

Total: **100 frontend tests and 11 Python tests passed**. The Python atomic-update
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
