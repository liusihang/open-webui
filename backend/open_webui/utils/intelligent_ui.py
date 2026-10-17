"""Presentation instructions and bounded control-state validation for Intelligent UI.

These helpers grant no tool, network, filesystem, or execution permissions.
Component specifications are additionally validated by the native client.
"""

import json
import math
import re

INTELLIGENT_UI_PROMPT = """[INTELLIGENT UI PRESENTATION, version 2]
You may mix ordinary Markdown and interactive native components in this answer.
Choose plain text for simple answers. Use components only when they make a
comparison, explanation, data set, or adjustable value easier to understand.
Keep useful explanatory text before or after each component as a readable
fallback. Do not invent data. Label hypothetical examples and uncertainties.

Place each independent UI in its own fenced code block with language
intelligent-ui. Text and multiple UI blocks may alternate in any order.
Inside the fence emit one complete JSON object per line, without commentary.
Each line is an operation:
{"op":"add","path":"/root","value":"chart"}
{"op":"add","path":"/elements/chart","value":{"type":"Chart","props":{"title":"Example values","kind":"bar","data":[{"label":"A","value":3},{"label":"B","value":5}]},"children":[]}}
Close the fence and continue ordinary text when appropriate. Complete each line
before starting the next so the interface can display incrementally.

Only add/replace of /root, a whole /elements/ID, or a whole /datasets/ID is supported. IDs start with
a letter and contain only letters, digits, underscores or hyphens. Each element
has exactly type, props and children. The root is an element ID. Use these types:
- Stack: props {title?: string}; children are element IDs in display order.
- Chart: props {title: string, description?: string, kind: "bar"|"line",
  data: [{label: string, value: finite number}], unit?: string}; children [].
- ComparisonCards: props {title: string, items: [{title: string,
  description: string, attributes?: [{label: string, value: string}]}]}; children [].
- Explanation: props {title: string, content: string}; children [].
- ParameterSlider: props {title: string, min: finite number, max: finite number,
  step: positive finite number, value: finite number, unit?: string,
  followUp?: string}; children []. min < max, and value must be within the range.
  followUp is a short question the user may explicitly submit with the chosen
  value. Moving the slider never calls the model. With stateKey it updates linked
  dataset views locally; without stateKey it only changes its own value.

Additional native components:
- Grid: props {title?: string, columns: integer 1..4}; children are element IDs.
- Tabs: props {title?: string, labels: [string]}; one child per tab label.
- Select: props {title: string, stateKey: safe ID, value: string,
  options: [{label: string, value: string}]}; unique option values without control characters; value must match an option; children [].
- NumberInput: props {title: string, stateKey: safe ID, min: number, max: number,
  step: positive number, value: number, unit?: string}; children []. Values apply on
  change, blur or Enter; slider values update immediately.
- ParameterSlider additionally accepts optional stateKey to share its numeric value.
- DataTable: props {title: string, dataSource: dataset ID,
  columns: [{key: field ID, label: string}], filters?: [filter],
  searchable?: boolean, sortable?: boolean, selectable?: boolean,
  rowKey?: field ID, selectionKey?: safe ID}; children []. Selection requires
  unique non-empty, non-null rowKey values and selectionKey; these properties are
  permitted only when selectable is true. The selected row key becomes
  shared string state. An empty string means no row is selected.
- MetricCard: props {title: string, description?: string, unit?: string,
  value: number} OR {title: string, description?: string, unit?: string,
  dataSource: dataset ID, aggregation: "count"|"sum"|"mean"|"min"|"max",
  field?: field ID, filters?: [filter]}; field required except count; children [].

Chart also supports kind "scatter" with data [{x: number,y: number,label?: string}],
and "heatmap" with data [{x: string,y: string,value: number}]. Instead of inline
Chart data, a Chart can have dataSource, xField, yField, filters, and valueField
(required and permitted only for heatmap). Never supply both data and dataSource.
Heatmap coordinates must be unique; repeated cells are rejected, not aggregated.

For actual linked controls, add a bounded static dataset using a complete line:
{"op":"add","path":"/datasets/plans","value":[{"name":"A","cost":10,"group":"small"},{"name":"B","cost":30,"group":"large"}]}
Dataset fields use safe IDs and primitive string/number/boolean/null cells only.
Selectable row-key strings cannot contain control characters.
At most 4 datasets with 256 rows and 16 fields each; prefer small examples or
selected relevant rows and explain any truncation. Dataset charts draw at most
128 valid points with a count notice; table/metric filters may cover all 256 rows.
Do not fabricate observations.
A filter is {field: field ID, op: "eq"|"gte"|"lte"|"contains", stateKey: safe ID,
ignoreValue?: string}. ignoreValue is allowed only with eq and must match a
Select option when that state is supplied by a Select. Controls declare the
stateKey and its default value.
Multiple controls sharing a key must have consistent defaults and definitions.
Example: a Select defaults to "all" and offers "all", "small", "large";
views use {field:"group",op:"eq",stateKey:"group",ignoreValue:"all"}.
A budget NumberInput can drive {field:"cost",op:"lte",stateKey:"budget"}.
Table, Chart and MetricCard with the same source and filters update together
locally, with zero model requests. Only explicit follow-up buttons call the model.
Shared state and datasets are scoped to ONE fenced block; place linked controls
and views together under Stack/Grid/Tabs. Different fences remain independent.
No arbitrary formulas, network requests or general expression language. JavaScript is
permitted only inside the isolated AppBlock worker described below.

Expanded presentation catalog (all state and actions remain local to this fence):
- WritingBlock: {title, content, format?: "text"|"markdown"}; children []. Editable,
  copyable, expandable draft. Whole-element replace may append streamed content;
  edits are saved separately from the model's original text.
- CodeBlock: {title?, language, code}; children []. Reuses the code editor and
  syntax highlighting. language:"mermaid" renders a diagram. Code execution and
  external preview are not enabled by this component. Ordinary Markdown fences
  continue to use the existing message code tools.
- AppBlock: {title, html, css?, js?, height?:160..900, stateKeys?:[safe ID]}; children [].
  This is a sandboxed visualization, not a website. HTML is restricted to inert
  text/layout/forms without submit plus canvas/progress/meter; no links, images,
  script tags, handlers, embedded frames or external resources. CSS is local.
  JS runs in an isolated worker AFTER generation finishes, not in a DOM window.
  Use ONLY the provided ui API: ui.onState(state=>...), ui.getState(key),
  ui.setState(key,value), ui.on(elementId,"click"|"input"|"change",value=>...),
  ui.setText(id,text), ui.setValue(id,value), ui.setStyle(id,property,value),
  ui.draw(canvasId,commands). Declare every shared key in stateKeys and a native
  control in this fence. No fetch, imports, document, window or external APIs.
  setStyle accepts width/height/minWidth/minHeight/maxWidth/maxHeight (bounded px/%),
  color/backgroundColor/borderColor (#RGB or #RRGGBB). Canvas commands:
  ["clear"], ["rect",x,y,w,h,"#RRGGBB"], ["circle",x,y,r,"#RRGGBB"],
  ["line",x1,y1,x2,y2,"#RRGGBB",width], ["text",text,x,y,"#RRGGBB",fontSize].
  At most 256 commands; coordinates bounded to ±2000; no raw canvas context.
- Chart kind:"pie" uses data:[{label,value}] (nonnegative values), optional donut:true.
  Bar/line can add series:[{name,data:[{label,value}]}] with data:[]; for datasets,
  series:[{name,yField}] uses the shared xField (keep yField as default). At most
  8 series and 128 total drawn points. Series labels must be unique. Charts have
  keyboard/pointer tooltips, legends and an exact-value table.
- Card: {title?,description?}; children are IDs. Combine with responsive Grid.
- Carousel: {title?}; 1..32 children, arrow buttons switch cards.
- Flow: {title?,steps:[{title,description?}]}; children []; at most 16 steps.
- Popover: {label,title?}; children are IDs; click opens, Escape/outside click closes.
- Math: {expression:LaTeX string,display?:boolean}; children []; no trusted commands.
- Image: {src:public HTTPS URL,alt,caption?,source?:public HTTPS URL}; children [].
- ImageGallery: {title?,images:[{src,alt,caption?,source?}]}; children []; max12.
  Only use grounded real source URLs; no local/IP/credentialed URLs or fabricated sources.
- SVG: {svg,label}; children []. Inert svg/g/path/rect/circle/ellipse/line/polyline/
  polygon/text/title/desc only; double-quoted geometry/color attributes; no CSS,
  href, external resources, scripts, foreignObject, animation, entities or handlers.
- Icon: {name:"info"|"check"|"warning"|"star"|"arrow-right"|"heart",label}; children [].
- Input or Textarea: {title,stateKey,value:string,placeholder?,maxLength?:1..256}; children [].
- Checkbox: {title,stateKey,value:boolean}; children [].
- DatePicker: {title,stateKey,value:"YYYY-MM-DD" or "",min?,max?}; children [].
- RadioGroup or SegmentedControl: {title,stateKey,value,options:[{label,value}]};
  children []; value must match one of at most 32 unique choices.
- Text: {text,bindings?:[{token,stateKey}]}; children []. Literal {{token}} placeholders
  display declared control state without evaluating code. Example text:"Count: {{n}}",
  bindings:[{token:"n",stateKey:"count"}]. Use for real-time form summaries.
- Button: {label,actions:[action]}; children []. Pressable has the same props and
  optional non-interactive children; the whole card is keyboard/click activatable.
  Actions: {type:"set",stateKey,value}, {type:"increment",stateKey,amount},
  {type:"toggle",stateKey}, {type:"copy-state",stateKey,from},
  {type:"copy",keys:[stateKey]}. copy writes selected parameter JSON to clipboard
  only on the user's click. All targets must be declared native controls, normalized
  to their allowed types/ranges/options. No network requests or model calls.
- Conditional: {stateKey,equals:string|number|boolean}; first child when equal,
  optional second child otherwise. Use with a SegmentedControl for summary/detail.
Video and map components are not part of this catalog.

Keep interfaces small: prefer at most 24 elements (hard limit 64), 32 chart data points, 6 comparison
cards, and short labels. Prefer one useful component to a large dashboard.
Use plain strings except explicit WritingBlock Markdown, CodeBlock code, SVG markup,
AppBlock HTML/CSS/worker JS and Image source URL fields. Only the listed bounded
actions and bindings are supported; never add arbitrary event handlers, dynamic
expressions or extra properties. No real-world operations are performed by these views. Never promise that a UI action
has executed a real-world operation. All values must be grounded in the answer.
The surrounding system and user instructions still apply.
[/INTELLIGENT UI PRESENTATION]"""


def get_intelligent_ui_prompt(enabled: object) -> str | None:
    """Use only the administrator's boolean configuration, never client features."""
    if enabled is True:
        return INTELLIGENT_UI_PROMPT
    return None


def validate_intelligent_ui_state(state: object) -> dict:
    """Only bounded native control values may be persisted, never UI code/specs."""
    if not isinstance(state, dict) or set(state) != {'version', 'blocks'}:
        raise ValueError('Invalid interactive state envelope')
    if type(state['version']) is not int or state['version'] != 1:
        raise ValueError('Unsupported interactive state version')
    blocks = state['blocks']
    if not isinstance(blocks, dict) or len(blocks) > 64:
        raise ValueError('Too many interactive blocks')
    forbidden = {'__proto__', 'prototype', 'constructor'}
    clean = {}
    for block, values in blocks.items():
        if not isinstance(block, str) or not 1 <= len(block) <= 512 or block in forbidden:
            raise ValueError('Invalid interactive block identifier')
        if not isinstance(values, dict) or len(values) > 64:
            raise ValueError('Too many interactive control values')
        clean_values = {}
        for key, value in values.items():
            if not isinstance(key, str) or not 1 <= len(key) <= 512 or key in forbidden:
                raise ValueError('Invalid interactive control identifier')
            multiline = key.startswith(('writing:', 'shared:'))
            max_length = 16384 if key.startswith('writing:') else 256
            forbidden_chars = r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]' if multiline else r'[\x00-\x1f\x7f]'
            valid_string = isinstance(value, str) and len(value) <= max_length and not re.search(forbidden_chars, value)
            if (
                type(value) is not bool
                and not valid_string
                and not (type(value) in (int, float) and abs(value) <= 1e12 and math.isfinite(value))
            ):
                raise ValueError('Interactive controls must be bounded strings, finite numbers or booleans')
            clean_values[key] = value
        clean[block] = clean_values
    result = {'version': 1, 'blocks': clean}
    if len(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) > 131072:
        raise ValueError('Interactive state is too large')
    return result
