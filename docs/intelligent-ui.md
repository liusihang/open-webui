# Intelligent UI: ordered streaming text and native components

## Enable and use

Open **Admin Panel → Settings → General → Features → Intelligent UI**
(中文：管理员面板 → 设置 → 常规 → 功能 → 智能交互界面).
The setting defaults to **on** and applies to all users. Only administrators can
change it; it is not a personal Interface preference. The database-backed key is
`ui.enable_intelligent_ui`, exposed in the admin API as `ENABLE_INTELLIGENT_UI`.
`ENABLE_INTELLIGENT_UI` sets the initial environment default (true when unset);
an existing administrator-saved value takes precedence. An admin API update that
omits the field preserves the saved value.

For logged-in users, `features.enable_intelligent_ui` in the backend configuration
controls rendering. Legacy personal `ui.intelligentUI` values are ignored, including
stored `false`; no user-settings cleanup is needed. The server reads the administrator
configuration directly before appending the existing component instructions to the
system message. Request feature flags cannot enable or disable this injection.
Turning it off also displays existing UI fences as ordinary code without deleting
their specifications or saved interaction state. Existing configuration refresh
behavior applies; other open sessions receive the value when they reload configuration.

Use a model that can follow structured output instructions, then start or continue
a chat. Plain text remains appropriate
for simple questions; the model receives a small component catalog and chooses
whether to use it. No new provider endpoint, API credential, external UI service,
iframe permission, or model-specific tool-calling protocol is required.

Example request:

> Explain three hypothetical storage plans. Put a comparison card after your
> introduction, continue with a short explanation, then add a slider for my
> monthly budget and a button to ask a follow-up using that budget. Clearly label
> the plan data as hypothetical.

## System instruction order

The built-in request assembler emits the following text in order, including only
sections that are enabled and available:

1. The selected base system prompt, using the existing prompt-selection rules.
2. Optional folder instructions, then optional voice instructions.
3. Intelligent UI component instructions, when the administrator setting is on.
4. The optional text conversation summary.
5. Optional memory context.
6. Optional tool/skill instructions and attached-knowledge catalog.
7. Optional RAG system context.

The base prompt is not a forced concatenation of global, personal and model
defaults. Existing provider-specific selection/override rules remain unchanged.
Empty sections add no placeholders. Turning Intelligent UI off omits only its
section. Tool schemas remain in `tools`; terminal AGENTS.md instructions retain
their separate user-message contract. When `RAG_SYSTEM_CONTEXT=false`, retrieval
context remains on the user side instead of moving into this system sequence.

Local tool continuations retain the same order. A text-summary update replaces
only the summary boundary captured after the static prefix; memory, tool text
and rebuilt source context follow it. It does not scan for or deduplicate strings
that resemble summary markers in a user's base prompt. The boundary is
request-local server metadata, not provider data or visible chat history.
Native opaque Responses compaction is a separate path: its encrypted items,
checkpoints, thresholds and prefix/hash validation are unchanged, and no text
summary is fabricated from opaque items.

This ordering keeps the selected base/folder/voice/UI prefix byte-stable when only
summary, memory or source data changes. No per-round IDs, timestamps or new cache
keys are introduced by this assembler. Explicit cache parameters, tool order,
existing user isolation and Responses continuation behavior are preserved; actual
upstream cache-hit rates depend on the provider and are not established by local tests.
At the final native GPT Responses boundary, a separate missing-only
[cache routing key](prompt-caching.md) keeps eligible saved chats stable across turns
and Codex V2 compaction without changing this instruction order.
Third-party filters can still rewrite instructions. If a filter invalidates the
captured summary boundary, unknown text is preserved and the existing append
fallback applies; arbitrary plugin rewrites are outside the built-in order guarantee.

## Component catalog

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
- `python3 tests/migration/test_intelligent_ui_prompt.py`: request bypass prevention,
  personal-setting removal, and append-only prompt seams.
- `python3 tests/migration/test_intelligent_ui_config.py`: actual admin form,
  authenticated read/write routes and logged-in feature-response seams with
  in-memory boundaries (requires FastAPI, Pydantic and HTTPX).
- `python3 tests/migration/test_intelligent_ui_state.py`: state bounds and atomic persistence.

Use Node 22, as required by the upstream engine range. No live provider call is
part of these deterministic tests. See the integration validation report for
observed results and remaining deployment checks. This feature does not require
enabling iframe same-origin access.

The administrator-setting regression uses isolated production-code seams without
starting the application or opening a database. Mounted tests cover unified-config
rendering and existing controls/state behavior. These checks do not establish a
live deployment, real-provider output quality, or cross-session configuration delivery.
Streaming transport is unchanged and is not re-tested as part of the setting move.

## Expanded content blocks and local compositions

The version-2 presentation catalog adds WritingBlock, CodeBlock, isolated AppBlock,
Card, Carousel, Flow, Popover, Math, Image/ImageGallery, inert SVG/Icon, Input,
Textarea, Checkbox, RadioGroup, DatePicker, SegmentedControl, Button, Pressable,
Text bindings and Conditional. Video and maps are intentionally excluded.
Existing tables, linked datasets, filters, metrics, tabs, slider/number controls,
scatter/heatmaps, administrator settings and ordinary message Markdown remain.

Image and ImageGallery accept public HTTPS image URLs and exact, tool-returned
`/api/v1/files/<uuid>/content` references. Internal images use the existing authenticated
file API and its owner/read-grant checks; their bytes are displayed through temporary
browser blob URLs, canceled and revoked when the component changes or unmounts.
Only raster image responses are displayed. Arbitrary local paths, terminal proxy URLs,
query credentials and inline data URLs are not accepted. Source links remain public HTTPS.
Tool image results expose the stored file reference for UI composition; displaying a
file does not itself require another model vision call. Provider requests separately
resolve images in ordinary messages and native tool output to image data, without
rewriting stored conversation history. Unreadable images become explicit unavailable
text, not malformed provider URLs. No database migration or public file access is needed.

WritingBlock provides edit/copy/expand, safe Markdown, streamed append reconciliation,
and conversation-scoped draft persistence (16 KiB). Regenerated revisions do not
silently replace local edits. Input/Textarea shared values are bounded to256 chars;
multiline is supported. All writes use the existing owner-only state endpoint,
128 KiB envelope limit, and read-only/share guards. No database migration is needed.

Charts accept kind `pie` and `donut:true`. Bar/line multi-series use
`series:[{name,data:[{label,value}]}]` with `data:[]`, or dataset series
`[{name,yField}]` alongside `dataSource`, `xField`, and default `yField`.
Each named inline series has unique category labels. At most8 series and128
rendered points are allowed. Tooltips work with pointer and keyboard focus, legends
can hide/show series, and tables expose exact values. Negative pie values are rejected.

### Local form and conditional example

All values below are fictional UI examples. One fence shares one state scope:

```intelligent-ui
{"op":"add","path":"/root","value":"root"}
{"op":"add","path":"/elements/root","value":{"type":"Stack","props":{"title":"Example preferences"},"children":["name","paired","count","summary","copy","mode","view"]}}
{"op":"add","path":"/elements/name","value":{"type":"Input","props":{"title":"Project","stateKey":"name","value":"Demo"},"children":[]}}
{"op":"add","path":"/elements/paired","value":{"type":"Checkbox","props":{"title":"Paired view","stateKey":"paired","value":true},"children":[]}}
{"op":"add","path":"/elements/count","value":{"type":"ParameterSlider","props":{"title":"Items","stateKey":"count","min":1,"max":100,"step":1,"value":12},"children":[]}}
{"op":"add","path":"/elements/summary","value":{"type":"Text","props":{"text":"{{name}}: {{count}} items; paired={{paired}}","bindings":[{"token":"name","stateKey":"name"},{"token":"count","stateKey":"count"},{"token":"paired","stateKey":"paired"}]},"children":[]}}
{"op":"add","path":"/elements/copy","value":{"type":"Button","props":{"label":"Copy parameters","actions":[{"type":"copy","keys":["name","count","paired"]}]},"children":[]}}
{"op":"add","path":"/elements/mode","value":{"type":"SegmentedControl","props":{"title":"View","stateKey":"mode","value":"summary","options":[{"label":"Summary","value":"summary"},{"label":"Details","value":"details"}]},"children":[]}}
{"op":"add","path":"/elements/view","value":{"type":"Conditional","props":{"stateKey":"mode","equals":"summary"},"children":["brief","detail"]}}
{"op":"add","path":"/elements/brief","value":{"type":"Card","props":{"title":"Summary","description":"A compact example."},"children":[]}}
{"op":"add","path":"/elements/detail","value":{"type":"Card","props":{"title":"Details","description":"More example information."},"children":[]}}
```

### Isolated AppBlock example and security boundary

AppBlock displays sanitized HTML/CSS in an opaque-origin iframe (`allow-scripts`
only). Generated JavaScript runs in a separate worker, not the iframe DOM realm.
The iframe bootstrap exposes a bounded text/value/style/canvas bridge. The worker
has no main-page DOM, cookies, chat API, tokens, follow-up callbacks or navigation
capability. CSP denies networking and external resources. Only declared shared
keys are passed, never writing drafts or the complete conversation state. Parent
writes validate source window, opaque origin, unpredictable token, type and bounds.
Generation must finish before any app code runs. There is no arbitrary `document`
or `window` API; this deliberate API is necessary to prevent self-navigation-based
exfiltration, which a plain sandboxed arbitrary-JS iframe does not prevent.

```intelligent-ui
{"op":"add","path":"/root","value":"root"}
{"op":"add","path":"/elements/root","value":{"type":"Stack","props":{},"children":["threshold","app"]}}
{"op":"add","path":"/elements/threshold","value":{"type":"ParameterSlider","props":{"title":"Threshold","stateKey":"threshold","min":0,"max":100,"step":1,"value":70,"unit":"%"},"children":[]}}
{"op":"add","path":"/elements/app","value":{"type":"AppBlock","props":{"title":"Threshold visualization","height":240,"stateKeys":["threshold"],"html":"<p id=\"label\">Threshold</p><div class=\"track\"><div id=\"bar\"></div></div><canvas id=\"plot\" width=\"600\" height=\"100\"></canvas>","css":".track{background:#e5e7eb;border-radius:8px;overflow:hidden}#bar{height:18px;background:#6366f1}","js":"ui.onState(s=>{const n=Number(s.threshold);ui.setText('label',n+'%');ui.setStyle('bar','width',n+'%');ui.draw('plot',[['clear'],['line',10,70,590,70,'#94a3b8',2],['circle',10+n*5.8,50,12,'#6366f1'],['text','Threshold '+n,10,20,'#334155',14]]);});"},"children":[]}}
```

Images accept public HTTPS URLs with no credentials, literal IPs, local hosts or
nonstandard ports. Loading is lazy, referrers are suppressed, failures show alt
text, and source links open with noopener/noreferrer. SVG accepts inert geometry
only; Math uses untrusted bounded KaTeX. None of these components authorizes
external account actions, persistent database work, model requests or file writes.
