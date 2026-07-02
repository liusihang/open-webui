# W15 Agent Run UI Productization

Date: 2026-06-18

## Scope

Thread target: productize the Agent Mode frontend run experience on branch
`codex/agent-mode-ui-productization`, starting from `7e970942d`.

Do not modify nested `open-terminal`. Do not touch unrelated PR7 review,
security, or backend work. Do not add dependencies unless first-principles
need is proven.

## Current Checkpoint

- [x] Worktree verified and switched to `codex/agent-mode-ui-productization`.
- [x] Existing W13/W14 handoffs reviewed for PR7 verification and lockfile
      hygiene expectations.
- [x] Current Agent Run UI inspected:
  - `AgentRunEvents.svelte` renders a raw `details` list plus final text.
  - `eventFold.ts` already handles seq ordering, duplicate seq suppression,
    final delta dedupe, final-phase gating, and strips raw reasoning fields.
  - `index.ts` API already separates JSON backfill from SSE stream URLs.
  - `historySync.ts` already prevents Agent Mode socket content duplication.
- [x] Component/library audit started:
  - User clarified that "mature components" means mature open-source community
    components, not only local project wrappers.
  - `bits-ui` is already a locked dependency (`2.16.3`) and exports
    `Collapsible`/`Accordion`; the repo already uses `bits-ui` for
    pagination, switches, and link previews.
  - No new dependency is justified for this slice. Use `bits-ui`
    `Collapsible` for accessible expandable rows, and reuse local
    project wrappers for `Tooltip`, `Badge`, and icons where they match
    established OpenWebUI styling.
  - Component-level Svelte testing is not established in current focused tests;
    existing frontend pattern is Vitest unit tests for event folding, API, and
    history sync.
- [x] TDD red test added in
      `src/lib/components/chat/AgentEvents/eventFold.test.ts`.
- [x] Red test command:
      `PATH="$HOME/.nvm/versions/node/v22.22.0/bin:$PATH" npm run test:frontend -- --run src/lib/components/chat/AgentEvents/eventFold.test.ts`
      failed as expected with missing `runStatus`, `counts`, and user-facing event
      semantic fields.
- [x] Folded event model implemented:
  - lifecycle status, status sequence guard, category counts.
  - user-facing category labels and metadata.
  - running fallback from tool/action/subagent/model events when a
    `run.running` event is absent from a reconnect window.
- [x] `AgentRunEvents.svelte` productized:
  - uses already-locked community `bits-ui` `Collapsible`.
  - adds Agent Run header, lifecycle chip, stream state, category counts,
    compact timeline rows, expandable sanitized details, and distinct final
    answer area.
  - no fake cancel/retry affordances added; current frontend/backend Agent Run
    API exposes detail/list/events/stream only, with no cancel/retry endpoint.
- [x] Component-level DOM testing decision:
  - The repo does not currently include a Svelte component DOM test pattern or
    `@testing-library/svelte`.
  - This work keeps tests in the established pattern: fold/API/history Vitest
    tests plus direct Svelte compile verification for the touched component.
  - Browser screenshot was not used because the component requires authenticated
    Agent Run API/SSE state; a mocked DOM harness would require adding a new
    testing stack for this slice.
- [x] Next design checkpoint: evaluate mature open-source LLM UI component
      libraries before a second UI upgrade pass.
  - User clarified the preferred reference set: mature LLM/agent UI component
    libraries, not generic UI kits.
  - Subagents dispatched without parent context to read code from:
    assistant-ui, Vercel AI Elements / AI SDK UI, CopilotKit, and
    prompt-kit / Svelte AI Elements / shadcn-style AI components.
  - Goal: produce a concrete upgrade plan for AgentEvents components before
    any second-pass code changes.
- [x] LLM UI library code-read results received:
  - assistant-ui: borrow `MessagePart` / grouped parts / tool fallback /
    runtime-state separation. Add a render model above the current folded
    event state instead of making the Svelte component understand raw events.
  - Vercel AI Elements / AI SDK UI: borrow `UIMessagePart`-style part
    taxonomy, tool input/output/error sections, reasoning visibility semantics,
    task/step primitive, and explicit transport-vs-run status separation.
  - CopilotKit: borrow renderer registry / wildcard fallback / semantic tool
    state / human-in-the-loop approval separation. Do not copy React hooks.
  - prompt-kit / Svelte AI Elements / Vercel chatbot / shadcn-chatbot-kit:
    borrow compact visual density, compound components, Svelte component
    splitting, artifact separation, and low-noise tool/reasoning/source blocks.
- [x] Second-pass design recommendation:
  - Do not add a generic UI kit such as Flowbite or Skeleton.
  - Keep `bits-ui` as the Svelte headless primitive layer.
  - Add a local `renderModel.ts` and split Agent Run UI into small Svelte
    components inspired by mature LLM UI libraries.
  - Default to structure-first changes: tool panels, approval callouts,
    artifact cards, final answer panel, and grouped run steps.
- [x] Formal W16 redesign document written:
  - `handoff/agent-mode/w16-llm-ui-redesign.md`

## Design Decision

Use a compact Agent Run panel inside the existing message surface:

1. Header summarizes lifecycle state from events: queued, running, waiting for
   approval, finalizing, completed, failed, cancelled, or budget exceeded.
2. Phase chips show the useful counts and affordance categories: tools,
   approvals, artifacts, subagents, model selection, and final answer.
3. Timeline rows use `bits-ui` `Collapsible` with user-facing labels and
   compact metadata, not raw event names. Details stay expandable and
   sanitized.
4. Final answer stays visually separate and appears only after final phase
   semantics already allowed by `eventFold`.
5. Reconnect/backfill remains driven by seq and final delta keys, with tests
   preserving no-duplicate behavior.

## Implementation Plan

1. Extend the folded view model in `types.ts` and `eventFold.ts` with:
   lifecycle `runStatus`, category, label, metadata, detail title, and counts.
2. Add focused Vitest cases before implementation:
   - lifecycle state moves through running/finalizing/completed/failed/cancelled.
   - event categories and labels for tools, approvals, artifacts, subagents,
     model selection, and final answer are user-facing.
   - reconnect/backfill and final delta replay still do not duplicate output.
   - API and history sync tests remain green.
3. Rework `AgentRunEvents.svelte` only:
   - compact header and status chip.
   - grouped user-facing timeline rows using existing styling conventions.
   - sanitized expandable details with concise metadata.
   - responsive layout for narrow message width.
4. Verify focused frontend tests and scoped static checks. If component testing
   remains absent, document that and use the folded-view tests plus a browser
   or markup-level sanity check if feasible.

## Verification Log

- `npm run test:frontend -- --run src/lib/components/chat/AgentEvents/eventFold.test.ts`
  initially could not run because this worktree had no `node_modules`.
- `npm ci` with Node 24 failed engine checks; project requires Node
  `>=18.13.0 <=22.x.x`.
- `PATH="$HOME/.nvm/versions/node/v22.22.0/bin:$PATH" npm ci` installed
  dependencies from the existing lockfile.
- Red test command above failed on the intended missing semantic fields.
- Focused frontend:
  `PATH="$HOME/.nvm/versions/node/v22.22.0/bin:$PATH" npm run test:frontend -- --run src/lib/components/chat/AgentEvents/eventFold.test.ts src/lib/apis/agentRuns/index.test.ts src/lib/components/chat/historySync.test.ts`
  -> `3 passed`, `27 tests passed`.
- Touched-file ESLint:
  `PATH="$HOME/.nvm/versions/node/v22.22.0/bin:$PATH" npx eslint src/lib/components/chat/AgentEvents/AgentRunEvents.svelte src/lib/components/chat/AgentEvents/eventFold.ts src/lib/components/chat/AgentEvents/types.ts src/lib/components/chat/AgentEvents/eventFold.test.ts`
  -> passed.
- Touched Svelte compile:
  `compile(src/lib/components/chat/AgentEvents/AgentRunEvents.svelte)` via
  `svelte/compiler` -> `warnings=0`.
- Formatting:
  `PATH="$HOME/.nvm/versions/node/v22.22.0/bin:$PATH" npx prettier --check ...`
  -> all matched files use Prettier style. Prettier emits an existing
  `pluginSearchDirs` option warning.
- Whitespace:
  `git diff --check` -> passed.
- Broad `npm run check` was attempted and remains blocked by existing repo-wide
  Svelte/type debt: `9409 errors and 275 warnings in 387 files`, beginning in
  `RichTextInput/AutoCompletion.js`, `listDragHandlePlugin.js`, auth routes,
  office-preview, and share route files. No touched AgentEvents error appeared
  before the broad noise made the command unsuitable as this slice's gate.

## Notes for Continuation

- Root `uv.lock` and package locks should remain untouched unless an intentional
  dependency is added.
- Broad repo checks may be noisy; prefer focused frontend tests first.
