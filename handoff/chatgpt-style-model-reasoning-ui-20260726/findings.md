# Findings and Decisions

## Requirements

- Use the current ChatGPT web UI as an interaction reference for the composer, model selection, and reasoning effort.
- Make the composer the primary entry and retain a synchronized navbar entry.
- Remove the multi-model comparison product capability.
- Expose four effort levels: `轻度/low`, `标准/medium`, `深度/high`, `极深/xhigh`.
- Preserve compatibility with the existing Bifrost-backed `bifrostapi` function.
- Do not copy proprietary ChatGPT code.
- Do not touch formal live without separate authorization.

## Research Findings

- Current model selection flows through `Navbar.svelte` -> `ModelSelector.svelte` -> `ModelSelector/Selector.svelte`.
- Current reasoning UI is a native `<select>` in `MessageInput.svelte` with `medium/deep/divergent` product values.
- Current request mapping in `agentModeRequest.ts` converts those values to `medium/high/xhigh` plus hard-coded token caps.
- `Chat.svelte` sends a top-level `reasoning` object in ordinary Chat and Agent conversation modes.
- `bifrostapi.py::_resolve_reasoning_config()` already preserves explicit non-empty effort and omits reasoning in non-minimal mode when no effort exists.
- Focused Bifrost tests protect explicit effort preservation and empty-reasoning omission.
- The earlier real provider failure proved that leaving an empty reasoning object is unsafe; the entire object must be omitted.
- The focused frontend command is `npm run test:frontend -- <test paths>` using Vitest 1.6.1.
- Existing request tests currently assert that Chat preserves multiple models and that reasoning includes hard-coded `max_tokens`; both assertions must change under the approved contract.
- Existing presentation tests explicitly require the native `思考深度` control; this should become a RED assertion for the new composer control.
- Existing compile tests already cover `Navbar.svelte`, `Chat.svelte`, `MessageInput.svelte`, and `Placeholder.svelte`; new components should be added.
- No reusable slider component exists in the current chat/common component set, so a focused new Svelte slider is justified.
- `MultiResponseMessages.svelte` is part of historical rendering and should not be deleted merely to remove new comparison creation.
- `selectedModels` arrays are used across capability checks and rendering; the product can enforce length one while preserving these compatibility inputs initially.
- The common `Dropdown.svelte` already provides portal positioning, outside-click close, Escape close, menu role, auto-flip, and existing transition vocabulary.
- The existing model `Selector.svelte` already provides search, tags/provider filters, virtualized results, pinning, model metadata, and keyboard navigation; the composer should reuse it with a compact trigger rather than duplicate catalog logic.
- The current model selector popup is presentation-configurable through `className` and `triggerClassName`, so it can be embedded as the model row while retaining the existing catalog.
- The common dropdown does not itself restore focus after Escape; the composer wrapper must explicitly return focus to its trigger.
- The native reasoning select sits in the same trailing composer row where the new unified pill belongs, so integration can replace one bounded block.
- The lightweight model-boundary exploration confirmed that `selectedModels`, historical message `models`, and backend `message_ids` should remain array-shaped compatibility boundaries.
- New active send flow can remain single-model by normalizing `selectedModelIds` before request dispatch; backend fallback already accepts a one-entry message-id list.
- `MultiResponseMessages` and historical regenerate/continue paths should remain intact for existing chats.
- The minimal removal surface is the add/remove UI in `ModelSelector.svelte` plus active request normalization; a repository-wide scalar migration would add risk without product value.

## ChatGPT Source Findings

- The composer trigger is a 36px semantic pill button with menu state.
- The composer surface is approximately 28px radius with restrained border/elevation.
- The main settings menu is approximately 224px wide with 16px radius and soft shadow.
- The model submenu uses radio-item semantics.
- The production bundle contains a dedicated lazy-loaded `ModelReasoningEffortSlider`.
- The slider is discrete (`min=0`, `step=1`, `max=options.length - 1`).
- It distinguishes live value changes from committed value changes.
- It includes pointer dragging, tick marks, keyboard behavior, reduced motion, and model-provided effort options.
- The request path forwards semantic `thinkingEffort` only for models that expose configurable effort.

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| Build original Svelte components using OpenWebUI tokens | Reuse the interaction pattern without copying private implementation. |
| Canonical selected model is scalar | The user removed multi-model comparison. |
| Keep length-one arrays only at compatibility boundaries | Limits blast radius while enforcing one product model. |
| Four-stop slider is metadata-driven | Supports Bifrost now and narrower provider option lists later. |
| Frontend sends no hard-coded reasoning token budget | Prevents UI/provider coupling and avoids inventing a `low` token value. |
| Capability resolver sits outside visual components | Provider-specific knowledge should not be embedded in presentation code. |
| Unsupported model omits reasoning and explains why | Avoids invalid payloads and silent false behavior. |
| Apply the repository's product register and Calm Workbench design system | The interface should feel familiar, restrained, theme-native, and trustworthy rather than like a ChatGPT clone. |
| Resolve effort capability from explicit metadata first, then `bifrostapi.*` | Supports future provider declarations while preserving the current known Bifrost contract. |

## Repository Design Context

- `PRODUCT.md` classifies the surface as a product UI for self-hosted AI users and technical operators.
- Brand personality is calm, capable, and transparent.
- The primary experience remains the user's task and answer; controls should not resemble an embedded operations dashboard.
- Existing OpenWebUI typography, themes, icons, and component vocabulary must win over copied ChatGPT styling.
- Accent color is reserved for selection, focus, and attention; inactive states remain neutral.
- Required states include default, hover, focus, active, disabled, loading, and error.
- Motion should communicate state in roughly 150 to 250ms and respect reduced motion.
- Nested cards, decorative glass, gradients, and gratuitous motion are prohibited.

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| Shell could not directly reach ChatGPT assets | Used the configured Clash proxy. |
| ChatGPT assets are minified and bundled | Combined DOM/accessibility inspection with narrow bundle/CSS searches. |
| New design files under `docs/plans` are ignored | Force-added only the approved design document. |
| Current branch already has another untracked handoff | Created and used a separate task-local handoff directory. |
| No literal mini-series subagent role is exposed | Delegated the narrow read-only model-boundary exploration to `SuperFastAgent` with no context fork. |

## Delegated Exploration

- Agent: Fermat (`019f9df7-9e77-7373-8338-fbb4ffe21e7e`)
- Scope: read-only single-model lifecycle boundaries only; no reasoning-effort exploration.
- Result: keep array compatibility for historical messages and backend lists, remove new multi-select creation, and normalize active requests to one model.
- Risks identified: global array clamping could break historical rendering; broad `message_ids` contract changes could break legacy continuations.

## Resources

- Design: `docs/plans/2026-07-26-chatgpt-style-composer-model-reasoning-design.md`
- Handoff: `handoff/chatgpt-style-model-reasoning-ui-20260726/handoff.md`
- Current reasoning builder: `src/lib/components/chat/agentModeRequest.ts`
- Current composer: `src/lib/components/chat/MessageInput.svelte`
- Current model selector: `src/lib/components/chat/ModelSelector.svelte`
- Bifrost function: `tools/openwebui/functions/bifrostapi.py`
- Bifrost tests: `backend/open_webui/test/util/test_bifrostapi_pipe_function.py`

## Visual/Browser Findings

- ChatGPT combines model and effort inside the composer rather than leaving effort as a detached select.
- The compact menu presents model, reasoning, and speed as separate rows; this task implements model and reasoning only.
- The observed effort slider had five visual stops for that model, but the approved OpenWebUI control exposes the four values supported by current Bifrost.
- The visible trigger displays the selected model and effort together and truncates safely.
- Real rendering exposed two state defects that source-only checks missed: model changes reset the effort to `medium`, and callback-only slider updates could remain local to the menu. The final implementation preserves valid effort across model changes and uses native `bind:value` through the component chain.
- On a 390x844 viewport, the full Bifrost model name collided with the centered conversation-mode switch. The navbar now renders a compact mobile trigger label while keeping the full accessible/model-list label.
- The isolated browser flow verified all four keyboard stops in order: `轻度`, `标准`, `深度`, `极深`.
- Composer-to-navbar synchronization was verified by selecting `bifrost/gpt-5` in the composer and observing the navbar update to the same model while retaining `极深`.
- A fresh browser tab loaded meaningful content with no framework overlay and zero console warnings/errors.

## Final Request Evidence

- Focused frontend suites: 33/33 passed across request, presentation, and Svelte compile tests.
- Focused Bifrost suite: 37/37 passed, including parameterized `low/medium/high/xhigh` preservation.
- Production frontend build: passed with `NODE_OPTIONS=--max-old-space-size=8192 npm run build`.
- Real isolated request: the local mock upstream captured `{"effort": "xhigh", "enabled": true}` and the UI rendered `isolated ok`.
- Formal live was not modified, restarted, or configured.
- Scoped implementation commit: `0c736a9e4 feat(chat): add composer model reasoning controls`.
