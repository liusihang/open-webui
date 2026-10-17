"""Presentation instructions and bounded control-state validation for Intelligent UI.

These helpers grant no tool, network, filesystem, or execution permissions.
Component specifications are additionally validated by the native client.
"""

import json
import math
import re

INTELLIGENT_UI_PROMPT = """[INTELLIGENT UI PRESENTATION, version 1]
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
No arbitrary formulas, JavaScript, network requests or general expression language.

Keep interfaces small: at most 24 elements, 32 chart data points, 6 comparison
cards, and short labels. Prefer one useful component to a large dashboard.
Use plain strings, not HTML, scripts, URLs, Markdown links or executable code.
No arbitrary event handlers, actions, general bindings, computed expressions, loops, remote
resources or additional properties are supported. Never promise that a UI action
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
            valid_string = isinstance(value, str) and len(value) <= 256 and not re.search(r'[\x00-\x1f\x7f]', value)
            if (
                type(value) is not bool
                and not valid_string
                and not (type(value) in (int, float) and abs(value) <= 1e12 and math.isfinite(value))
            ):
                raise ValueError('Interactive controls must be bounded strings, finite numbers or booleans')
            clean_values[key] = value
        clean[block] = clean_values
    result = {'version': 1, 'blocks': clean}
    if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > 131072:
        raise ValueError('Interactive state is too large')
    return result
