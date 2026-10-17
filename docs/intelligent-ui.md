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

The registered component catalog includes:

- Stack and responsive Grid layouts; Tabs for switching between child views.
- Bar, line, scatter and heatmap charts with grounded numeric data.
- DataTable with optional local search, sorting and row selection.
- MetricCard with a literal value or a filtered dataset aggregation.
- Comparison cards and expandable explanations.
- Select, NumberInput and ParameterSlider controls.
- Explicit follow-up questions on sliders; all other filtering remains local.

Controls with a `stateKey` share local state within their fenced block. Dataset
views with matching declared filters update immediately when those values change;
sliders and number inputs with a matching numeric definition can share one value.
Moving a control does not execute code, make a model request, or recalculate an
unrelated chart that has no declared binding. Only clicking
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

The protocol accepts only `add`/`replace` of `/root`, whole
`/elements/<safe-id>` records, or whole `/datasets/<safe-id>` datasets. It rejects arbitrary JSON-pointer edits, unknown
components/properties, handlers, arbitrary bindings, remote-resource fields, executable
directives, cycles, and
oversized structures. This is a bounded protocol, not the full json-render
expression/action language. Plain strings containing URL or HTML-like text remain
escaped text, not links or executable markup. The renderer uses `@json-render/svelte`'s
`CatalogRenderer`, not its single-text/single-spec chat transport.

## Linked data and controls

A block can declare up to four static datasets using `/datasets/ID` operations.
A dataset contains up to 256 rows, 16 fields per row, and primitive cells only.
It is not a URL or a server query. All data and definitions still fit within the
128 KiB block limit. The model must state when displayed rows are a subset.

Dataset views use `dataSource` plus optional filters of the form
`{field, op, stateKey, ignoreValue?}`. The supported operations are `eq`, `gte`,
`lte` and `contains`. `ignoreValue` is supported only with `eq`; for Select state
it must match an option, such as an explicit All option. Select defaults must
also match an option. Row keys and selection keys are only valid on selectable
tables. These
are predefined operations, not JavaScript expressions or a general formula engine.
Equality compares non-null primitive cells and the control value by their string
representation, so numeric row IDs can match persisted row selections. Selectable
row keys must remain unique after this normalization and cannot be empty.
Heatmap coordinates must be unique; duplicate cells are rejected rather than
silently overlaid or summed.

A Select can filter a table, chart and metric together. A numeric budget control
can filter rows by cost; a table row selection can supply a string key for another
view's filter. DataTable's own search and sorting affect that table; cross-view
filtering must be declared explicitly. Metric aggregations are count, sum, mean,
min and max. Missing/non-numeric cells are omitted from numeric views; empty
aggregations display no data, while count displays zero.

Number inputs retain draft text while typing and apply the value on change, blur,
or Enter; step normalization must not overwrite partially typed numbers. Dataset
charts display at most 128 valid points and disclose when additional points are
not drawn. Tables and metrics still use the full matching dataset (at most 256
rows), so the visible chart subset is not an aggregation limit.

Shared state is **per fenced UI block**. Put linked controls and views under the
same Stack/Grid/Tabs tree. Separate UI fences remain independent even when they
use the same state-key spelling. This preserves stable component isolation across
ordinary prose, multiple Responses output items, chat reloads and forks.

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
