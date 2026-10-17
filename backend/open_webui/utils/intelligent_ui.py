"""Presentation instructions and bounded control-state validation for Intelligent UI.

These helpers grant no tool, network, filesystem, or execution permissions.
Component specifications are additionally validated by the native client.
"""

import json
import math

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

Only add/replace of /root or a whole /elements/ID is supported. IDs start with
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
  value. Moving the slider alone never calls the model or modifies a chart.

Keep interfaces small: at most 24 elements, 32 chart data points, 6 comparison
cards, and short labels. Prefer one useful component to a large dashboard.
Use plain strings, not HTML, scripts, URLs, Markdown links or executable code.
No arbitrary event handlers, actions, bindings, computed values, loops, remote
resources or additional properties are supported. Never promise that a UI action
has executed a real-world operation. All values must be grounded in the answer.
The surrounding system and user instructions still apply.
[/INTELLIGENT UI PRESENTATION]"""


def get_intelligent_ui_prompt(features: object) -> str | None:
    """Require an actual boolean opt-in, not a truthy client-provided value."""
    if isinstance(features, dict) and features.get('intelligent_ui') is True:
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
            if type(value) is not bool and not (
                type(value) in (int, float) and abs(value) <= 1e12 and math.isfinite(value)
            ):
                raise ValueError('Interactive controls must be finite numbers or booleans')
            clean_values[key] = value
        clean[block] = clean_values
    result = {'version': 1, 'blocks': clean}
    if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > 131072:
        raise ValueError('Interactive state is too large')
    return result
