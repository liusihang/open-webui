# ChatGPT-style model and reasoning UI handoff

## Goal

Optimize the OpenWebUI chat composer, model selection, and reasoning-effort selection using the currently open ChatGPT web UI as an interaction reference, while preserving compatibility with the existing Bifrost-backed `bifrostapi` function.

## Truth surfaces

- Code: `/Users/liusihang/.codex/worktrees/d790/openwebui`
- Branch: `codex/pr7-chat-agent-dual-mode-20260726`
- Browser reference: the authenticated ChatGPT tab currently open in Microsoft Edge
- Backend contract: the actual `bifrostapi` function implementation and emitted request payload in this worktree/runtime

## User-owned boundaries

- Preserve and do not inspect or overwrite the pre-existing untracked `handoff/chat-agent-dual-mode-20260726/` directory.
- Do not copy proprietary ChatGPT production code; extract reusable interaction structure and behavior.
- No implementation begins before design approval, per the brainstorming workflow.

## Checkpoints

### Checkpoint 1 - reference UI inspection

Status: completed

Evidence:

- Composer surface: white background, 28px radius, subtle 1px/soft shadow treatment.
- Model trigger: semantic button with `aria-haspopup=menu`, open/closed state, 36px height, pill radius, 16px text.
- Main settings menu: semantic menu, 224px wide, 160px high in the observed state, 16px radius, 6px vertical padding, long soft shadow.
- Model submenu: semantic menu with `menuitemradio` items, about 162.5px wide, 16px radius.
- Reasoning strength: discrete horizontal slider with values 0 through 4; observed value 1 maps to the displayed label `轻度`.
- Menu also separates model, reasoning strength, and speed as distinct settings rows.

Next verification:

- Inspect current OpenWebUI composer/model-selector component boundaries.
- Inspect current Bifrost function reasoning parameter normalization and tests.

Stop condition:

- If the current branch does not contain the expected chat composer/Bifrost integration, restate the truth surface before proposing design.

## Current checkpoint

### Checkpoint 2 - repository and Bifrost contract exploration

Status: completed

Repository findings:

- Current HEAD: `f7583ff4ed52bf26cd6cc076b6fc8097757050a5` on `codex/pr7-chat-agent-dual-mode-20260726`.
- The existing primary model selector is rendered from `Navbar.svelte` through `ModelSelector.svelte` and `ModelSelector/Selector.svelte`.
- `MessageInput.svelte` currently renders reasoning depth as a native `<select>` with `medium`, `deep`, and `divergent` choices.
- `agentModeRequest.ts` maps those UI choices to Bifrost-safe payloads:
  - `medium` -> `effort=medium`, `max_tokens=2048`
  - `deep` -> `effort=high`, `max_tokens=8126`
  - `divergent` -> `effort=xhigh`, `max_tokens=12400`
- `Chat.svelte` sends this as the top-level `reasoning` request object in both chat and agent conversation modes.
- `bifrostapi.py::_resolve_reasoning_config()` accepts explicit non-empty `effort`; in non-minimal mode it omits the entire reasoning object if no safe effort exists.
- Focused Bifrost tests already protect explicit `xhigh` preservation and omission of empty/default reasoning.

ChatGPT source findings:

- The production bundle carries a dedicated `ModelReasoningEffortSlider` component rather than styling a native select.
- Slider state is metadata-driven through `thinkingEffort`; the request path forwards `thinkingEffort` only for models that expose configurable effort.
- The slider is discrete (`min=0`, `step=1`, `max=options.length-1`) and separates live selection from commit callbacks.
- The menu uses Radix-style menu/radio/submenu primitives and explicit keyboard handling.
- The source includes reduced-motion handling and distinct drag/commit behavior; these are reusable interaction ideas, not code to copy.

Decision still needed:

- None for the approved design. The next task is implementation planning.

User decision:

- The composer is the primary model/settings entry.
- The existing navbar model selector remains as a synchronized secondary entry.
- Multi-model comparison is removed and is no longer offered in the UI.
- Implementation must remove the multi-model product path rather than merely hiding its add/remove controls.
- Reasoning effort uses four Bifrost-native levels:
  - `low` displayed as `轻度`
  - `medium` displayed as `标准`
  - `high` displayed as `深度`
  - `xhigh` displayed as `极深`
- The old `中度 / 深度 / 发散` product vocabulary is retired from this control.

Approved architecture:

- Remove the multi-model product capability while preserving a length-one compatibility array only at legacy internal/API boundaries where immediate scalar migration would create unnecessary risk.
- Add a primary composer pill displaying `model name · reasoning label`.
- Open a compact unified settings menu with model and reasoning rows.
- Reuse the existing searchable model catalog behavior in a compact submenu.
- Add a four-stop accessible discrete reasoning slider.
- Keep the navbar model selector as a synchronized secondary entry.
- Remove the native reasoning `<select>`, add/remove-model controls, and multi-model comparison entry points.
- Hide or disable reasoning controls for models that cannot safely accept configurable reasoning.

Approved data flow and compatibility contract:

- Canonical product state is scalar `selectedModelId` plus `reasoningEffort`.
- New conversations default to `medium` / `标准`; drafts persist model and effort per conversation.
- Frontend sends `reasoning: { enabled: true, effort }` without hard-coded reasoning token caps.
- Unsupported models omit the entire reasoning object and show a disabled explanatory row.
- Existing Bifrost empty-reasoning omission and safe retry behavior remain required.
- Focused frontend, request, Bifrost, browser, and real isolated-request verification are required.

Design document:

- `docs/plans/2026-07-26-chatgpt-style-composer-model-reasoning-design.md`

## Current checkpoint

Checkpoint 5 - design is fully approved and documented; commit the design, then create the implementation plan.

Commit note:

- Normal staging is blocked by the repository ignore rule for new `docs/plans/*` files.
- Force-add only `docs/plans/2026-07-26-chatgpt-style-composer-model-reasoning-design.md`; inspect staged scope before commit.

### Checkpoint 6 - request-contract TDD

Status: completed

RED evidence:

- Focused command: `npm run test:frontend -- src/lib/components/chat/agentModeRequest.test.ts --run`
- Result: 6 expected failures, 2 passes.
- Proven gaps: Chat preserves multiple models, four native efforts are not accepted, and request payloads still include frontend `max_tokens`.

Next verification:

- Add RED tests for model capability resolution and the new composer UI contract.

GREEN evidence:

- The same focused command now passes `8/8` tests.
- Product request shaping now selects one non-empty model in both modes.
- Reasoning payloads now accept `low/medium/high/xhigh`, normalize legacy/unknown input, and omit frontend `max_tokens`.

### Checkpoint 7 - composer UI TDD

Status: completed

RED evidence:

- Focused command: `npm run test:frontend -- src/lib/components/chat/ComposerModelSettings.presentation.test.ts --run`
- Result: 6 expected failures.
- Proven gaps: new composer/slider components do not exist, the native reasoning select remains, and add/remove multi-model controls remain.

GREEN evidence:

- Focused command covered request, composer presentation, conversation presentation, and Svelte compilation suites.
- Result: `27 passed` across 4 files.
- New `ComposerModelSettings.svelte` and `ReasoningEffortSlider.svelte` compile.
- `MessageInput` now uses the unified composer control and no longer contains the native reasoning select.
- `ModelSelector.svelte` no longer exposes add/remove multi-model controls.

### Checkpoint 8 - integration, browser QA, and Bifrost acceptance

Status: completed

Implementation:

- Added `ComposerModelSettings.svelte` and `ReasoningEffortSlider.svelte`.
- Replaced the native reasoning select with one composer pill showing `model · effort`.
- Kept the navbar selector synchronized and single-select; removed add/remove multi-model creation controls.
- Added metadata-first reasoning capability resolution with `bifrostapi.*` fallback support for all four effort levels.
- Active requests normalize to one model and omit reasoning for unsupported models.
- Removed frontend reasoning token caps; Bifrost/provider policy remains authoritative.
- Added approved Chinese labels and a compact mobile navbar label.

Browser findings and fixes:

- Four keyboard stops rendered and reported exactly as `轻度/标准/深度/极深`.
- Source-only tests missed a local-state reset. Real interaction proved it, then the implementation was fixed to use `bind:value` and preserve valid effort across model changes.
- Desktop and 390x844 mobile screenshots show the composer menu without clipping; the mobile top selector no longer collides with the centered conversation-mode control.
- Composer model changes update the navbar immediately.

Bifrost acceptance:

- Local isolated OpenWebUI backend and Bifrost function only; formal live untouched.
- Mock upstream captured `{"reasoning":{"effort":"xhigh","enabled":true}}` on the real chat request.
- Corrected SSE fixture then returned `isolated ok`, which rendered in the UI.

Verification:

- Frontend focused suites: `33 passed`.
- Bifrost focused suite: `37 passed`.
- Production build: passed in 50.27s with `NODE_OPTIONS=--max-old-space-size=8192`.
- Fresh browser tab: correct URL/title, meaningful DOM, no framework overlay, zero console logs.
- `git diff --check` on this task's paths: clean.

### Checkpoint 9 - commit boundary

Status: completed

- Preserve unrelated concurrent conversation-profile persistence files and `handoff/chat-agent-dual-mode-20260726/`.
- Scoped implementation commit: `0c736a9e4 feat(chat): add composer model reasoning controls`.
- The staged scope contained only this task's implementation, tests, translations, store typing correction, and task-local handoff files.
- Final focused verification before commit remained green: frontend `33/33`, Bifrost `37/37`, and staged diff check clean.
- Do not deploy, restart, or modify formal live.
