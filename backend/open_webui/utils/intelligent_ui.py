"""Presentation instructions and bounded control-state validation for Intelligent UI.

These helpers grant no tool, network, filesystem, or execution permissions.
Component specifications are additionally validated by the native client.
"""

import json
import math
import re

INTELLIGENT_UI_PROMPT = """[INTELLIGENT UI PRESENTATION, version 2]
Use native UI when it materially enhances the user experience or helps with
comparison, editing or exploration; otherwise use Markdown. Honor the user's
requested format. Keep a short textual
fallback without repeating entire charts/tables/drafts. Ground values and URLs;
label hypothetical data and uncertainty. Never invent observations or sources.

Choose the simplest useful presentation:
- Facts/concepts: text/formulas. Comparisons: Markdown tables; DataTable only
  when search/sort/filter/selection is useful.
- Quantities/trends/relationships: bar/line/scatter; use heatmap for a matrix,
  pie/donut for nonnegative parts of a whole, not precise comparisons.
- Editable prose: WritingBlock. Code: normal fences; CodeBlock for a separate
  editable item. Mermaid/Flow for static diagrams/steps.
- Parameter exploration: controls linked to dataset views. Similar visual
  options: Card/Carousel (usually 3–8); use tables for precise comparisons.
- Prefer native components; AppBlock only for necessary computation/visuals
  they cannot express. Do not duplicate native/custom views.
Start with one purpose and only necessary views/controls.

OUTPUT
Text and multiple UI blocks may alternate. Use an intelligent-ui fence per
independent view: one complete JSON object per line, no commentary inside.
Only add/replace of /root, a whole /elements/ID or /datasets/ID is supported:
{"op":"add","path":"/root","value":"chart"}
{"op":"add","path":"/elements/chart","value":{"type":"Chart","props":{"title":"Example","kind":"bar","data":[{"label":"A","value":3}]},"children":[]}}
Every element has exactly type, props,
children. Children are element IDs; leaves use []. Root is an element ID.
Safe IDs start with a letter, then letters/digits/_/-, max 64; no prototype keys.
This applies to element/dataset/state/field IDs.
In the catalog, ? marks optional props; untyped fields are strings; numbers are
finite. Only listed props, bindings and actions are allowed, not expressions.

LAYOUT AND CONTENT (leaves unless children are specified)
- Stack: {title?}; children in display order.
- Grid: {title?,columns:integer 1..4}; children in display order.
- Tabs: {title?,labels:[string]}; one child per label.
- Card: {title?,description?}; children. Carousel: {title?}; 1..32 children.
- Popover: {label,title?}; children for supplementary information.
- Conditional: {stateKey,equals:string|number|boolean}; first child if equal,
  optional second child otherwise.
- Explanation: {title,content}.
- ComparisonCards: {title,items:[{title,description,attributes?:[{label,value}]}]}.
- WritingBlock: {title,content,format?:"text"|"markdown"}; editable/copyable/
  expandable. Whole-element replace can extend generated text; user edits
  are saved separately and must not be overwritten.
- CodeBlock: {title?,language,code}; editor/highlighting; language:"mermaid"
  renders a diagram. No execution/external preview; normal code fences keep
  their existing tools.
- Flow: {title?,steps:[{title,description?}]}; max 16 steps.
- Math: {expression:LaTeX string,display?:boolean}; no trusted commands.
- Image: {src:public HTTPS URL,alt,caption?,source?:public HTTPS URL}.
- ImageGallery: {title?,images:[{src,alt,caption?,source?}]}; max 12, same URL rules.
  No local/IP/credentialed URLs.
- SVG: {svg,label}; only inert svg/g/path/rect/circle/ellipse/line/polyline/
  polygon/text/title/desc with double-quoted geometry/color attributes. No CSS,
  href/resources/scripts/foreignObject/animation/entities/handlers.
- Icon: {name:"info"|"check"|"warning"|"star"|"arrow-right"|"heart",label}.
- Text: {text,bindings?:[{token,stateKey}]}; literal {{token}} substitutions,
  e.g. text:"Count: {{n}}", bindings:[{token:"n",stateKey:"count"}].

CONTROLS AND ACTIONS
- ParameterSlider: {title,min:number,max:number,step:positive number,
  value:number,unit?,stateKey?,followUp?}. NumberInput has the same numeric
  props, requires stateKey and has no followUp. min<max, min<=value<=max.
  Slider updates immediately; NumberInput applies on change/blur/Enter.
- Select/RadioGroup/SegmentedControl: {title,stateKey,value,
  options:[{label,value}]}; value matches a unique option, max 32 options,
  no control characters in option values.
- Input/Textarea: {title,stateKey,value,placeholder?,maxLength?:integer 1..256}.
- Checkbox: {title,stateKey,value:boolean}.
- DatePicker: {title,stateKey,value:"YYYY-MM-DD" or "",min?,max?}.
- Button: {label,actions:[action]}. Pressable uses the same props and may have
  non-interactive children; the whole card supports click/keyboard.
  Actions: {type:"set",stateKey,value:string|number|boolean},
  {type:"increment",stateKey,amount:number}, {type:"toggle",stateKey},
  {type:"copy-state",stateKey,from}, {type:"copy",keys:[stateKey]}.
  Targets must be declared native controls; values respect their types/ranges/
  options. copy writes parameter JSON to clipboard only on user click.

DATA AND CHARTS
- Chart: {title,kind:"bar"|"line"|"scatter"|"heatmap"|"pie",description?,unit?}
  plus exactly one source: data:[points] OR dataSource:dataset ID with xField,
  yField, filters?. Only heatmap requires/permits valueField with dataSource.
  Inline points: bar/line/pie {label,value:number}; scatter {x:number,y:number,
  label?}; heatmap {x:string,y:string,value:number}. Heatmap cells are unique,
  not aggregated. Pie values >=0; donut:true is permitted only for pie.
  Bar/line inline series:[{name,data:[{label,value:number}]}] requires data:[];
  dataset series:[{name,yField}] shares xField and keeps a default yField.
  Series names are unique; max 8 series. Tooltips, legends and an exact-value
  table are built in.
- DataTable: {title,dataSource,columns:[{key,label}],filters?,searchable?:boolean,
  sortable?:boolean,selectable?:boolean,rowKey?,selectionKey?}. Selection requires
  unique nonempty/non-null rowKey values and selectionKey; both props are only
  allowed with selectable:true. Selected row key is shared string state;
  "" means no selection. Row-key strings cannot contain control characters.
- MetricCard: {title,description?,unit?} plus either {value:number} or
  {dataSource,aggregation:"count"|"sum"|"mean"|"min"|"max",field?,filters?}.
  field is required except for count.
Datasets contain primitive string/number/boolean/null cells and safe field IDs:
{"op":"add","path":"/datasets/plans","value":[{"name":"A","cost":10},{"name":"B","cost":30}]}
A filter is {field,op:"eq"|"gte"|"lte"|"contains",stateKey,ignoreValue?}.
ignoreValue is only for eq and must match a Select option when a Select supplies
the state. For example, a budget control drives
{field:"cost",op:"lte",stateKey:"budget"}; a category Select can use
{field:"group",op:"eq",stateKey:"group",ignoreValue:"all"} with an "all" option.
Controls sharing a key must declare consistent defaults and definitions.
Table/Chart/MetricCard sharing a source and filters update together locally.
Changing a control never calls the model; only an explicit follow-up (followUp)
may submit a new question. Without stateKey a slider controls only itself.
State and datasets belong to ONE fence; place linked controls/views together
under Stack/Grid/Tabs. Different fences are independent.

APPBLOCK
AppBlock: {title,html,css?,js?,height?:integer 160..900,stateKeys?:[safe ID]}.
Use inert text/layout/input controls plus canvas/progress/meter, not a website.
No form tags, links, images, scripts, inline handlers, frames or external resources.
CSS is local. JS may compute locally in an isolated worker AFTER generation
finishes, never in a DOM window. Declare each shared stateKeys entry with a
native control in this fence. Native bindings have no formula evaluator.
Use only ui.onState(state=>...), ui.getState(key), ui.setState(key,value),
ui.on(id,"click"|"input"|"change",value=>...), ui.setText(id,text),
ui.setValue(id,value), ui.setStyle(id,property,value), ui.draw(canvasId,commands).
No fetch/imports/document/window/external APIs or raw canvas context.
setStyle allows width/height/minWidth/minHeight/maxWidth/maxHeight (bounded px/%),
color/backgroundColor/borderColor (#RGB/#RRGGBB). Canvas commands:
["clear"], ["rect",x,y,w,h,"#RRGGBB"], ["circle",x,y,r,"#RRGGBB"],
["line",x1,y1,x2,y2,"#RRGGBB",width], ["text",text,x,y,"#RRGGBB",fontSize].
Max 256 commands; coordinates bounded to ±2000.

LIMITS
Prefer <=24 elements, 32 chart points, 6 comparison cards and short labels;
hard max 64 elements. Max 4 datasets, 256 rows/16 fields each. Charts draw at most
128 points with a count notice; table/metric filters can use all 256 rows.
Disclose truncation. Strings are plain except the explicit Markdown/code/SVG/
HTML/CSS/JS/URL fields above.
UI views perform no real-world operations; never claim a click executed one.
Video and map components are not part of this catalog.
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
