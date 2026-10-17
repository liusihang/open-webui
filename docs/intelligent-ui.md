# Intelligent UI: ordered streaming text and native components

## Enable and use

Open **Settings → Interface → Intelligent UI** (中文：设置 → 界面 → 智能交互界面).
The setting defaults to off. Turn it on for a model that can follow structured
output instructions, then start or continue a chat. Plain text remains appropriate
for simple questions; the model receives a small component catalog and chooses
whether to use it. No new provider endpoint, API credential, external UI service,
iframe permission, or model-specific tool-calling protocol is required.

Example request:

> Explain three hypothetical storage plans. Put a comparison card after your
> introduction, continue with a short explanation, then add a slider for my
> monthly budget and a button to ask a follow-up using that budget. Clearly label
> the plan data as hypothetical.

The initial component set is deliberately small:

- Bar/line charts with grounded labels and numeric values.
- Comparison cards with descriptions and attributes.
- Expandable explanations.
- Parameter sliders with optional explicit follow-up questions.
- A Stack for grouping these components.

Moving a slider changes local presentation state. It does not execute code, make
a model request, or automatically recalculate an unrelated chart. Only clicking
the visible follow-up button invokes the normal chat submission flow. A native
component registry defines all rendering and interactions; generated HTML or
JavaScript is not executed.

## Ordered streaming protocol

Ordinary Markdown and separately fenced `intelligent-ui` blocks alternate inside
the same assistant message. The existing message transport, citations, history,
tool rendering, and Responses output renderer are retained. Each UI block is
parsed independently; completed JSONL operations can render before the response
or fence finishes. A partial last line waits for more tokens.

Example (invented data):

````text
These are illustrative values, not measurements.

```intelligent-ui
{"op":"add","path":"/root","value":"chart"}
{"op":"add","path":"/elements/chart","value":{"type":"Chart","props":{"title":"Illustrative values","kind":"bar","data":[{"label":"A","value":3},{"label":"B","value":5}]},"children":[]}}
```

Category B is larger in this example. Here is a separate explanation:

```intelligent-ui
{"op":"add","path":"/root","value":"note"}
{"op":"add","path":"/elements/note","value":{"type":"Explanation","props":{"title":"How to read the chart","content":"The bars show the example values above."},"children":[]}}
```
````

The protocol accepts only `add`/`replace` of `/root` or whole
`/elements/<safe-id>` records. It rejects arbitrary JSON-pointer edits, unknown
components/properties, handlers, bindings, remote-resource fields, executable
directives, cycles, and
oversized structures. This is a bounded protocol, not the full json-render
expression/action language. Plain strings containing URL or HTML-like text remain
escaped text, not links or executable markup. The renderer uses `@json-render/svelte`'s
`CatalogRenderer`, not its single-text/single-spec chat transport.

## Persistence and fallback

The original UI specification stays in the message content, so ordinary chat
history and export/import retain it. Interaction state is stored under the
assistant message's versioned `meta.intelligent_ui` field and saved through a
bounded message-only update under the existing authenticated chat route. This
preserves normalized message rows and fork/export behavior without overwriting
concurrent content edits or the active branch. Read-only/shared views cannot persist
state or submit follow-up messages. Saving is disabled during response generation.

When the feature is off, UI fences display as ordinary code. Invalid UI data has
a readable escaped-data fallback. Models are instructed to include useful prose
so a visual failure does not remove the whole explanation. Text and diagram
quality still depend on the selected model; the renderer cannot verify factual
claims or turn unsupported output into a correct answer.

## Development and verification

- `npm run test:frontend -- --run`: standard frontend and protocol/SSR tests.
- `npm run test:intelligent-ui`: mounted Svelte interaction tests in jsdom.
- `python3 tests/migration/test_intelligent_ui_prompt.py`: opt-in and prompt seams.
- `python3 tests/migration/test_intelligent_ui_state.py`: state bounds and atomic persistence.

Use Node 22, as required by the upstream engine range. No live provider call is
part of these deterministic tests. See the integration validation report for
observed results and remaining deployment checks. This feature does not require
enabling iframe same-origin access.
