# W16 Agent Run LLM UI Redesign

Date: 2026-06-18

## Purpose

Upgrade the Agent Mode frontend from a productized event list into a proper
LLM/agent run viewer. The UI should communicate what the agent is doing, what
tools and subagents ran, what artifacts were produced, what approvals were
needed, and what final answer was produced without exposing raw reasoning or
backend-only payloads by default.

This is a design checkpoint only. Do not implement from this file until the
plan is accepted for the next pass.

## Current Baseline

Branch: `codex/agent-mode-ui-productization`

Current commit after W15: `06b5e1ea1 Productize agent run event UI`

Current W15 state:

- `eventFold.ts` folds events into ordered, deduplicated view items.
- `AgentRunEvents.svelte` shows a header, stream status, category chips,
  `bits-ui` collapsible event rows, sanitized details, and a distinct final
  answer area.
- Reconnect/backfill and final delta dedupe are covered by focused Vitest.
- There is no Agent Run cancel/retry/approve frontend API in this branch, so
  UI must not create fake action buttons.

## Reference Libraries Read

Subagents read code from mature open-source LLM UI projects, not only docs:

- `assistant-ui`
  - Relevant patterns: `MessagePart`, grouped parts, tool fallback,
    action-bar visibility, runtime state separated from UI rendering.
  - Use the idea, not the React primitives.
- Vercel `ai-elements` and AI SDK UI messages
  - Relevant patterns: `UIMessagePart`, `Tool` input/output/error sections,
    `Reasoning`, `Sources`, `Task`, explicit stream chunk lifecycle.
  - Use the part taxonomy and section layout, not React/shadcn hooks.
- `CopilotKit`
  - Relevant patterns: renderer registry, wildcard fallback, semantic tool
    states, human-in-the-loop approval separation.
  - Keep approval state separate from approval actions because OpenWebUI
    receives server events and does not have direct callback functions here.
- `prompt-kit`, Svelte AI Elements, Vercel chatbot, shadcn-chatbot-kit
  - Relevant patterns: compact AI message density, compound components,
    Svelte component splitting, low-noise tool/reasoning/source/artifact
    blocks.

## Design Principles

1. Structure before styling.
   The core upgrade is a render model that turns Agent Run events into LLM UI
   parts/groups. Pretty cards without semantic structure would hide the real
   problem.

2. No raw reasoning.
   Do not display `reasoning`, `raw_reasoning`, `chain_of_thought`, `thought`,
   `private`, or raw backend payloads as primary UI. Keep the existing
   sanitization boundary.

3. Transport state and run state are different.
   `loading`, `live`, `reconnecting`, and `error` describe the SSE connection.
   `queued`, `running`, `waiting_approval`, `finalizing`, `completed`,
   `failed`, `cancelled`, and `budget_exceeded` describe the agent run.

4. Tools, approvals, artifacts, subagents, and final answer deserve different
   renderers.
   They are not just differently colored log rows.

5. Reuse mature primitives already in the project.
   Keep `bits-ui` for headless Svelte primitives. Reuse OpenWebUI icons,
   `Tooltip`, `ContentRenderer`, and Tailwind conventions. Do not add a generic
   UI kit.

6. Show actions only when backed by real API.
   Approval, cancel, retry, open, download, or preview affordances must only
   appear when the current frontend/backend contract supports them.

## Proposed Architecture

Add a render-model layer between `eventFold.ts` and Svelte components:

```text
raw AgentRunEvent[]
  -> eventFold.ts
       ordered state, dedupe, run status, final text, sanitized details
  -> renderModel.ts
       grouped LLM UI parts with display kinds and section data
  -> Svelte components
       header, timeline, tool panels, approval callouts, artifacts, final answer
```

The folded state remains the persistence/reconnect correctness layer. The new
render model is the presentation grammar.

## Render Model

Add `src/lib/components/chat/AgentEvents/renderModel.ts`.

Suggested top-level type:

```ts
export type AgentRunRenderModel = {
	runStatus: AgentRunState;
	transportStatus: 'loading' | 'live' | 'reconnecting' | 'error';
	counts: AgentRunEventState['counts'];
	groups: AgentRunRenderGroup[];
	artifacts: AgentRunArtifactPart[];
	finalAnswer: AgentRunFinalPart | null;
	errors: AgentRunErrorPart[];
};
```

Suggested group union:

```ts
export type AgentRunRenderGroup =
	| AgentRunStepGroup
	| AgentRunToolGroup
	| AgentRunApprovalGroup
	| AgentRunArtifactGroup
	| AgentRunSubagentGroup
	| AgentRunModelGroup
	| AgentRunFallbackGroup;
```

Every group should include:

- `id`
- `kind`
- `status`
- `title`
- `subtitle`
- `metadata`
- `seqRange`
- `events`
- `detailSections`

Suggested status vocabulary:

- `queued`
- `running`
- `waiting`
- `done`
- `error`
- `cancelled`

Avoid adding a generic `reasoning` part unless there is explicit public
reasoning summary data. `action.summary` can become `step` or `task`.

## Grouping Rules

Start conservative. Do not over-group events if identity is missing.

1. Tool group
   - Combine related `tool.requested`, `tool.started`, `tool.completed`, and
     `tool.failed`.
   - Group key: `payload.tool_call_id`, then `payload.call_id`, then
     `payload.tool_name + participant_id + nearest sequence window`.
   - Status:
     - requested/started -> `running`
     - completed -> `done`
     - failed -> `error`
   - Sections:
     - Input: arguments, query, path, command, or other safe request fields.
     - Output: result/content/summary/process refs/warnings.
     - Error: `structured_error`, message, code.
     - Debug: sanitized remaining payload.

2. Approval group
   - Combine `approval.requested` and `approval.completed`.
   - Group key: `payload.approval_id`, then participant plus sequence window.
   - Pending approval is a callout, not a normal log row.
   - No decision buttons until an approval action API exists.

3. Artifact group
   - Keep `artifact.registered` in the timeline.
   - Also aggregate artifacts into an artifact strip/card list if there are
     multiple artifacts.
   - Fields: `artifact_id`, name, path, mime type, size if present.
   - Preview/open/download only if existing file/artifact APIs support it.

4. Subagent group
   - Combine `subagent.created`, `subagent.updated`, `subagent.completed`,
     and `subagent.failed` by participant id.
   - Show subagent name, status, model if available, result summary.

5. Model group
   - Combine `model.selection.requested` and `model.selection.completed`.
   - Show selected model, provider, and reason if present.

6. Final answer
   - `final.started` and `final.delta` do not appear as ordinary timeline rows.
   - They drive `finalAnswer`.
   - Use `ContentRenderer` in `AgentRunFinalAnswer.svelte`.

7. Fallback group
   - Unknown event types still render via a compact fallback row.
   - Fallback includes sanitized details, not raw private fields.

## Component Split

Target files under `src/lib/components/chat/AgentEvents/`:

- `renderModel.ts`
  - Pure functions and types for grouped render model.
  - Unit tested.

- `AgentRunEvents.svelte`
  - Data loading and SSE subscription only.
  - Creates folded state and render model.
  - Wires child components.

- `AgentRunHeader.svelte`
  - Run lifecycle status.
  - Transport status.
  - Counts.
  - Error banner if stream parsing or connection fails.

- `AgentRunTimeline.svelte`
  - Renders ordered groups.
  - Handles compact density and optional empty state.

- `AgentRunEventItem.svelte`
  - Shared row shell.
  - Uses `bits-ui` `Collapsible`.
  - Owns icon, title, subtitle, metadata, status badge, and expand affordance.

- `AgentToolPanel.svelte`
  - Vercel AI Elements inspired tool layout.
  - Header, input section, output section, error section, debug section.

- `AgentApprovalPanel.svelte`
  - CopilotKit inspired human-in-the-loop state layout.
  - Pending/completed/cancelled/error states.
  - No action buttons without API.

- `AgentArtifactCard.svelte`
  - Artifact name/path/mime rendering.
  - Future preview affordance gate.

- `AgentSubagentPanel.svelte`
  - Participant status, model, result summary, details.

- `AgentFinalAnswer.svelte`
  - Final answer panel with `ContentRenderer`.
  - Visually separate from timeline.

- `AgentDetailSection.svelte`
  - Reusable section shell for input/output/error/debug details.
  - JSON only in debug or object output sections.

## Visual Direction

Use mature LLM UI density rather than enterprise dashboard density:

- text sizes: `text-xs`, `text-[11px]`, regular body only for final answer.
- icon sizes: `size-3.5` to `size-5`; status dots or small circles.
- spacing: `px-2.5`, `py-2`, `gap-1.5`, no heavy cards for every row.
- timeline: subtle left rail or stacked rows, no decorative section cards.
- status badges: compact, readable, not all uppercase where it harms scanning.
- details: collapsed by default except errors, pending approvals, and active
  tool runs when useful.

Do not use marketing-style hero/card composition. This is a chat-side
operational UI.

## State And Interaction Rules

- Stream reconnecting should not imply the agent failed.
- A failed run should show a header-level failure plus the failed group details.
- Active tool groups may stay expanded while running.
- Completed groups collapse by default unless they contain warnings, errors, or
  artifacts.
- Pending approval gets visual priority.
- Artifact strip appears only when artifacts exist.
- Final answer appears only when final phase semantics allow final text.
- Long participant ids and paths use tooltip or truncation.
- Raw JSON is a secondary debug affordance.

## Data Safety

Keep and extend existing sanitization:

- Strip `chain_of_thought`.
- Strip `private`.
- Strip `raw`.
- Strip `raw_reasoning`.
- Strip `reasoning`.
- Strip `thought`.

Never render these fields in section summaries, metadata, titles, or debug
details.

## Test Plan

Add `renderModel.test.ts` before implementation:

- Tool lifecycle groups requested/started/completed into one `tool` group.
- Tool failure produces `error` status and error section.
- Approval requested/completed groups into one approval callout.
- Artifact events produce timeline group and artifact part.
- Subagent lifecycle groups by participant id.
- Final events are excluded from ordinary groups and populate `finalAnswer`.
- Unknown events produce fallback groups with sanitized details.
- Duplicate reconnect events do not duplicate groups.
- Out-of-order lower-sequence backfill inserts correctly without reverting
  newer terminal lifecycle state.

Keep existing focused tests:

- `eventFold.test.ts`
- `src/lib/apis/agentRuns/index.test.ts`
- `src/lib/components/chat/historySync.test.ts`

Add compile/static checks:

- Svelte compile for touched components.
- Touched-file ESLint.
- Touched-file Prettier.
- `git diff --check`.

Broad `npm run check` remains useful to try, but W15 evidence shows it is
blocked by existing repo-wide Svelte/type debt.

## Preview Plan

W15 did not include visual preview. W16 should add a temporary preview harness
for this UI work:

1. Create a local fixture-only route or harness component that feeds fixed
   Agent Run events into the render model.
2. Include scenarios:
   - running tool
   - failed tool
   - pending approval
   - completed approval
   - artifacts
   - subagent success/failure
   - finalizing with final answer
   - reconnecting stream status
3. Run dev server with Node 22.
4. Capture desktop and mobile screenshots.
5. Remove temporary harness before final commit unless it is intentionally
   accepted as a test fixture.

## Implementation Phases

Phase 1: Render model

- Add `renderModel.ts`.
- Add `renderModel.test.ts`.
- Keep UI unchanged until render model tests pass.

Phase 2: Component split

- Extract header, timeline, event item, final answer.
- Keep current visual behavior close to W15 while reducing file size.
- Verify compile and focused tests.

Phase 3: Specialized panels

- Add tool, approval, artifact, subagent, detail section components.
- Replace generic row details for known kinds.
- Preserve fallback for unknown events.

Phase 4: Visual preview and polish

- Add temporary fixture harness.
- Capture desktop/mobile screenshots.
- Adjust spacing, truncation, hover, dark/light behavior.
- Remove harness if not committed as fixture.

Phase 5: Commit

- Commit only AgentEvents UI/docs/tests.
- Keep package locks and `uv.lock` unchanged unless a dependency is
  intentionally added.
- Do not push unless explicitly asked.

## Open Questions

- Is there an existing artifact open/download/preview API that the UI may call,
  or should artifact cards remain read-only for W16?
- Is an approval action API planned in this branch, or should approval stay
  status-only for now?
- Should source/citation chips be included in W16 only when payload contains
  explicit source fields, or deferred until Agent Run events carry richer
  citation data?
- Should the fixture preview harness be retained as a permanent dev/test route,
  or removed before commit?

## Recommended Next Step

Start W16 implementation with `renderModel.ts` and `renderModel.test.ts`.

Do not begin by restyling `AgentRunEvents.svelte`; the structural render model
is the cheaper and safer first move.

## W16 Implementation Checkpoint

Status as of 2026-06-18:

- [x] W16 design document accepted as the implementation source for this pass.
- [x] Product/design context loader attempted for the `impeccable` UI skill:
      this worktree has no `PRODUCT.md` or `DESIGN.md`, so the implementation
      will follow OpenWebUI's existing Svelte/Tailwind conventions and W16
      rather than inventing an external design context.
- [x] Dependency decision reaffirmed:
      do not add React LLM UI packages or generic Svelte UI kits. Use the
      already locked `bits-ui` primitives plus local OpenWebUI components.
- [x] Phase 1 TDD:
      add `renderModel.test.ts`, verify the tests fail because
      `renderModel.ts` does not exist yet, then implement the pure render
      model.
      Red command:
      `PATH="$HOME/.nvm/versions/node/v22.22.0/bin:$PATH" npm run test:frontend -- --run src/lib/components/chat/AgentEvents/renderModel.test.ts`
      failed as expected because `./renderModel` does not exist.
      Green command:
      `PATH="$HOME/.nvm/versions/node/v22.22.0/bin:$PATH" npm run test:frontend -- --run src/lib/components/chat/AgentEvents/renderModel.test.ts`
      passed with 8 tests.
- [x] Phase 2/3 UI:
      split `AgentRunEvents.svelte` into semantic AgentEvents components and
      render tool, approval, artifact, subagent, model, fallback, and final
      answer groups from the render model.
- [x] Phase 4 preview:
      use a temporary fixture harness or equivalent local preview path to
      capture desktop and mobile screenshots, then remove temporary preview
      code unless intentionally retained.
- [x] Phase 5 verification:
      run focused frontend tests, touched-file compile/lint/format checks,
      `git diff --check`, update this handoff with evidence, and commit.

Implemented files:

- `src/lib/components/chat/AgentEvents/renderModel.ts`
- `src/lib/components/chat/AgentEvents/renderModel.test.ts`
- `src/lib/components/chat/AgentEvents/AgentRunEvents.svelte`
- `src/lib/components/chat/AgentEvents/AgentRunHeader.svelte`
- `src/lib/components/chat/AgentEvents/AgentRunTimeline.svelte`
- `src/lib/components/chat/AgentEvents/AgentRunEventItem.svelte`
- `src/lib/components/chat/AgentEvents/AgentToolPanel.svelte`
- `src/lib/components/chat/AgentEvents/AgentApprovalPanel.svelte`
- `src/lib/components/chat/AgentEvents/AgentArtifactCard.svelte`
- `src/lib/components/chat/AgentEvents/AgentSubagentPanel.svelte`
- `src/lib/components/chat/AgentEvents/AgentFinalAnswer.svelte`
- `src/lib/components/chat/AgentEvents/AgentDetailSection.svelte`

Preview evidence:

- Temporary route used: `src/routes/agent-run-preview/+page.svelte`.
- Temporary route removed before commit.
- Desktop screenshot:
  `/tmp/openwebui-agent-mode-w16/agent-run-preview-desktop.png`
- Mobile screenshot:
  `/tmp/openwebui-agent-mode-w16/agent-run-preview-mobile.png`
- Preview required Playwright route mocks for global layout backend calls
  (`/api/config`, `/api/v1/auths/`) because no backend was running on
  `127.0.0.1:8080`. The AgentEvents preview rendered correctly; remaining
  console errors were from global layout websocket/user-settings/timezone calls
  in the no-backend dev preview.

Verification evidence:

- Focused frontend:
  `PATH="$HOME/.nvm/versions/node/v22.22.0/bin:$PATH" npm run test:frontend -- --run src/lib/components/chat/AgentEvents/eventFold.test.ts src/lib/components/chat/AgentEvents/renderModel.test.ts src/lib/apis/agentRuns/index.test.ts src/lib/components/chat/historySync.test.ts`
  -> `4 passed`, `35 tests passed`.
- Touched Svelte compile:
  `AgentRunEvents.svelte`, `AgentRunHeader.svelte`, `AgentRunTimeline.svelte`,
  `AgentRunEventItem.svelte`, `AgentToolPanel.svelte`,
  `AgentApprovalPanel.svelte`, `AgentArtifactCard.svelte`,
  `AgentSubagentPanel.svelte`, `AgentFinalAnswer.svelte`,
  `AgentDetailSection.svelte` -> `warnings=0`.
- Touched-file ESLint:
  `npx eslint` over all touched AgentEvents Svelte/TS/test files -> passed.
- Formatting:
  `npx prettier --check` over touched files -> passed. Prettier still emits the
  existing `pluginSearchDirs` warning.
- Whitespace:
  `git diff --check` -> passed.
- Broad `npm run check` was not used as the W16 gate because W15 already
  documented existing repo-wide Svelte/type debt; this pass used scoped compile,
  lint, formatting, and focused behavioral tests.
