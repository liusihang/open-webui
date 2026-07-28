# ChatGPT-style Composer Model and Reasoning Controls

Status: approved on 2026-07-26

## Summary

OpenWebUI will make the chat composer the primary entry for model and reasoning-effort selection while retaining the existing navbar model selector as a synchronized secondary entry. The interaction follows the useful structure observed in the current ChatGPT web UI: a compact composer pill opens one settings menu, model selection opens a searchable submenu, and reasoning effort uses an accessible discrete slider.

The product becomes single-model only. New multi-model comparison cannot be selected or requested. Legacy internal/API boundaries may temporarily receive a length-one model array where a scalar migration would add unrelated risk, and historical multi-response chats remain readable.

Reasoning effort uses Bifrost-native values:

- `low` displayed as `轻度`
- `medium` displayed as `标准`
- `high` displayed as `深度`
- `xhigh` displayed as `极深`

The frontend sends semantic effort values rather than hard-coded reasoning-token budgets. Bifrost valves or provider policy remain responsible for token limits.

## Goals

1. Make model and reasoning controls available directly in the composer.
2. Preserve the navbar model selector as a synchronized secondary control.
3. Replace the native reasoning `<select>` with a four-stop accessible slider.
4. Use one canonical selected model and remove the multi-model comparison product path.
5. Send Bifrost-safe reasoning payloads with explicit non-empty effort values.
6. Omit reasoning entirely when the selected model cannot safely accept configurable effort.
7. Preserve existing model catalog filtering, icons, pinning, permissions, and search behavior.
8. Keep the change isolated to this worktree; formal live deployment is out of scope.

## Non-goals

- Reproducing ChatGPT branding, proprietary source code, decorative maximum-effort effects, or private model taxonomy.
- Adding a separate speed/service-tier control in the first version.
- Replacing the full OpenWebUI model administration or model metadata system.
- Bulk rewriting every historical `selectedModels` array boundary when a length-one adapter is sufficient.
- Deleting renderers needed to display historical multi-model conversations.
- Changing formal live configuration or deploying this branch to live.

## Reference Findings

The current ChatGPT page was inspected through its rendered DOM, computed styles, accessibility tree, and loaded production bundles. The reusable interaction findings are:

- The composer is one rounded surface with trailing controls, not a collection of detached form fields.
- The model trigger is a semantic pill button with menu state and keyboard behavior.
- The compact settings menu is approximately 224px wide with a 16px radius and soft elevation.
- The model submenu uses radio-item semantics for a single selected model.
- Reasoning strength is a discrete slider with one option per model-supported effort value.
- Live selection and committed selection are separate callbacks.
- Reduced-motion and keyboard interactions are explicit parts of the component.
- The request path carries a semantic `thinkingEffort` only when the model exposes configurable effort.

These findings guide behavior and structure only. No ChatGPT production code will be copied.

## Product Experience

### Composer

The composer trailing area contains one primary pill:

```text
┌──────────────────────────────────────────────┐
│ Write a message...                           │
│                                              │
│  +  tools                 Model · Effort  ↑  │
└──────────────────────────────────────────────┘
```

Examples:

- `GPT-5.6 Sol · 轻度`
- `Claude Sonnet · 标准`
- `Legacy model` when configurable reasoning is unavailable

The pill remains compact, truncates long model names, and exposes the complete value through accessible text and a tooltip where necessary.

### Unified settings menu

Selecting the pill opens a compact menu with two rows:

```text
┌────────────────────────┐
│ 模型          GPT-5.6 › │
│ 思考强度        轻度     │
│  ●────●────●────●       │
└────────────────────────┘
```

- The model row opens a searchable submenu backed by the existing OpenWebUI model catalog.
- The reasoning row contains the four-stop slider.
- The selected stop updates the visible label immediately and commits the canonical effort value.
- Escape closes the current submenu first, then the parent menu, and returns focus to the trigger.
- Outside click closes the menu without changing the last committed value.

### Navbar synchronization

The navbar selector remains available for users who prefer the existing location. It reads and writes the same canonical `selectedModelId` as the composer. A change in either entry is reflected immediately in the other without event-loop mirroring or duplicate persisted state.

## Single-model Contract

The product state is scalar:

```ts
type SelectedModelId = string;
```

The UI no longer offers:

- add-model controls;
- remove-model controls;
- multiple checked model items;
- multi-model comparison placeholders;
- new requests containing more than one model.

Where an existing internal function still requires `string[]`, a narrow adapter supplies either `[selectedModelId]` or `['']`. The adapter is a compatibility boundary, not a second domain representation. Input containing multiple model IDs is normalized to the first valid model before product request dispatch.

Historical multi-response messages remain renderable so removing the creation path does not corrupt or hide existing chats.

## Component Design

### `ComposerModelSettings.svelte`

Responsibilities:

- render the primary pill;
- own open/close and focus-return behavior;
- render model and reasoning rows;
- open the model submenu;
- display disabled reasoning state when appropriate;
- emit scalar model and reasoning changes.

The component receives model catalog data and canonical state through props. It does not fetch models or construct provider payloads.

### Compact model submenu

The existing model selector contains valuable catalog behavior but its large presentation shell is not suitable inside the composer. Shared selection logic should be extracted or reused so the compact submenu preserves:

- model search;
- hidden-model filtering;
- pinned ordering;
- model icons and descriptions;
- connection/provider labels where useful;
- permission filtering;
- keyboard navigation.

It must use single-selection radio semantics. Model administration actions remain outside the compact composer menu.

### `ReasoningEffortSlider.svelte`

The slider receives an ordered list of allowed effort values and labels. For the current Bifrost path the list is:

```ts
[
  { value: 'low', label: '轻度' },
  { value: 'medium', label: '标准' },
  { value: 'high', label: '深度' },
  { value: 'xhigh', label: '极深' }
]
```

Requirements:

- one discrete stop per allowed option;
- left/right arrow support;
- pointer click and drag support;
- separate preview/change and commit behavior;
- visible selected track and thumb;
- correct slider accessibility values and announcements;
- reduced-motion behavior;
- dark-mode styling;
- no decorative particle or maximum-effort effects in the first version.

## Canonical State and Persistence

Chat state becomes conceptually:

```ts
type ReasoningEffort = 'low' | 'medium' | 'high' | 'xhigh';

type ComposerModelState = {
  selectedModelId: string;
  reasoningEffort: ReasoningEffort;
};
```

- New conversations default to `medium` / `标准`.
- Draft state stores the scalar model ID and reasoning effort.
- Loading a draft normalizes unknown values to `medium`.
- Loading persisted chat state replaces stale local draft state where the server already has an authoritative model choice.
- Model and effort changes from either UI entry update the same state object.
- The composer does not maintain its own copy of model or effort state.

## Reasoning Capability Resolution

A dedicated resolver determines whether configurable reasoning is available and which efforts are allowed.

Resolution order:

1. Explicit model capability metadata, when present.
2. The known Bifrost function family, initially `bifrostapi.*`, which supports `low`, `medium`, `high`, and `xhigh` through the existing function contract.
3. Unsupported when neither source provides a safe capability declaration.

The resolver centralizes provider-specific knowledge so the visual components do not contain model-prefix checks.

When reasoning is unsupported:

- the menu shows a disabled `思考强度` row with explanatory text;
- the pill displays only the model name;
- request construction omits the entire `reasoning` key.

When switching to a model with a narrower allowed list, the current value is normalized to the model default or the first allowed value and both UI entries update immediately.

## Request Contract

For a supported model, the product request contains an explicit effort:

```json
{
  "reasoning": {
    "enabled": true,
    "effort": "high"
  }
}
```

The frontend does not attach hard-coded `max_tokens`. Bifrost may add a configured default through `DEFAULT_REASONING_MAX_TOKENS`, and provider-specific policy remains on the server side.

For an unsupported model, the request contains no `reasoning`, `reasoning_effort`, `reasoning_summary`, or reasoning-token field.

`bifrostapi.py::_resolve_reasoning_config()` remains the final compatibility boundary:

- preserve explicit non-empty `low`, `medium`, `high`, or `xhigh` effort;
- omit reasoning in non-minimal mode when no safe effort exists;
- preserve the existing reasoning-parameter fallback for providers that reject the field;
- never emit an empty reasoning object or empty effort value.

## Error and Edge-case Behavior

- Missing model: show the standard select-model state and disable send as the existing product does.
- Removed/unavailable saved model: normalize to the first allowed model through existing availability rules.
- Invalid saved effort: normalize to `medium`, or to the model's first allowed effort when `medium` is unavailable.
- Unsupported model: omit reasoning and show a disabled explanatory row; do not silently pretend an effort was applied.
- Bifrost reasoning rejection: use the existing retry path that removes the entire reasoning object.
- Menu closed during drag: commit the last valid stop before closing or restore the last committed value consistently; tests define the chosen behavior.
- Navbar/composer update collision: canonical state update is synchronous and idempotent; components do not dispatch changes when the incoming value already matches.

## Accessibility and Visual Rules

- The composer trigger is a button with `aria-haspopup="menu"` and expanded state.
- The model submenu uses a radio group and single checked item.
- The reasoning control exposes slider position and meaningful effort labels to assistive technology.
- All actions are usable by keyboard.
- Focus returns to the originating trigger after closure.
- Hit targets remain at least 36px high in the composer.
- The composer surface keeps the existing OpenWebUI theme tokens while moving toward a 28px rounded container and soft border/elevation treatment.
- Menus use approximately 16px corner radii and restrained shadows.
- Long model names truncate without shifting the send button.

## Verification Strategy

### Frontend unit and presentation tests

- four labels map exactly to `low`, `medium`, `high`, and `xhigh`;
- invalid effort normalizes safely;
- navbar and composer update one canonical selected model;
- add/remove-model and multi-select controls are absent;
- product requests cannot contain more than one model;
- unsupported models omit reasoning and display the disabled row;
- slider click, drag, arrow keys, Escape, and focus return work;
- reduced-motion styling is respected;
- legacy multi-response messages remain renderable.

### Request and Bifrost tests

- each of the four explicit efforts survives request construction;
- no frontend reasoning-token cap is emitted;
- no effort produces no reasoning object;
- unsupported models emit no reasoning fields;
- `bifrostapi` preserves explicit `xhigh` and the newly exposed `low` value;
- empty/default reasoning remains omitted;
- reasoning-parameter rejection strips the full object before retry.

### Browser acceptance

In the isolated development surface:

1. Select a model from the composer and confirm the navbar updates.
2. Select a model from the navbar and confirm the composer updates.
3. Exercise all four effort stops by mouse and keyboard.
4. Refresh a draft and confirm model/effort persistence.
5. Send one real request for each effort through the current Bifrost function.
6. Inspect the emitted request or marker-correlated Bifrost evidence to confirm the exact effort.
7. Select an unsupported model and confirm reasoning is omitted.
8. Confirm no multi-model creation path remains.
9. Confirm an existing historical multi-response chat still renders.

Formal live remains untouched unless the user separately authorizes deployment.

## Expected Implementation Surfaces

Likely frontend surfaces:

- `src/lib/components/chat/Chat.svelte`
- `src/lib/components/chat/Navbar.svelte`
- `src/lib/components/chat/MessageInput.svelte`
- `src/lib/components/chat/ModelSelector.svelte`
- `src/lib/components/chat/ModelSelector/Selector.svelte`
- new composer settings and reasoning-slider components
- `src/lib/components/chat/agentModeRequest.ts` or a renamed shared reasoning request module
- focused frontend tests and translations

Likely backend/function surfaces:

- `tools/openwebui/functions/bifrostapi.py`
- `backend/open_webui/test/util/test_bifrostapi_pipe_function.py`
- only narrow request normalization changes if current behavior does not already satisfy the approved contract

## Acceptance Criteria

The design is implemented when:

1. The composer is the primary model/reasoning entry.
2. The navbar remains synchronized with the same single selected model.
3. Multi-model comparison cannot be selected or requested.
4. The native reasoning select is gone.
5. The four Bifrost effort values are accessible and persist correctly.
6. Supported requests send exact non-empty effort values without frontend token caps.
7. Unsupported requests omit reasoning entirely.
8. Focused frontend and Bifrost tests pass.
9. A real isolated Bifrost request proves the selected effort reaches the function/provider path.
10. Formal live has not been modified.
