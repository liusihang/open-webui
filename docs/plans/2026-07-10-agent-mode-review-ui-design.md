# Agent Mode Review and UI Productization Design

## Status

Approved direction: Claude-like calm transcript hierarchy combined with ChatGPT-like clarity for tools, approvals, user input, reconnecting, and errors. The user confirmed the recommended hybrid direction on 2026-07-10.

## Feature Summary

Agent Mode should read as part of the assistant conversation, not as an embedded monitoring console. It must make long-running work trustworthy and controllable while keeping the final answer visually primary.

## Primary User Action

Understand the current state at a glance and respond immediately when the agent needs approval or input, without reading raw event data.

## Design Direction

- Register: product.
- Color strategy: Restrained.
- Scene: a user follows a long agent task in a normal desktop or mobile chat session, wants to stay focused on the outcome, and needs interruptions to be obvious without making every second feel urgent.
- Anchors: Claude for calm process narration and reading rhythm; ChatGPT for explicit tool and interaction states; OpenWebUI for theme, typography, component vocabulary, and message layout.
- Visual probe: skipped because this is a directed refinement of an existing coded surface. Real browser comparison is the authoritative visual evidence.

## Scope

- Fidelity: production-ready.
- Breadth: the Agent Mode process surface inside assistant messages.
- Interactivity: shipped Svelte components, not a static prototype.
- Time intent: review, harden, polish, test, and commit.

## Layout Strategy

The final answer remains in the normal `ContentRenderer` flow. Immediately above it, a compact process disclosure shows the current state and elapsed time. Expanded content is a single chronological flow, with routine commentary and completed work visually quiet. Pending approval, user input, reconnecting, and failures use bounded attention regions with explicit actions.

No dashboard metrics, nested cards, or separate Agent Mode page are introduced. Successful tool and artifact details use progressive disclosure; error detail is visible by default.

## Key States

- Starting and running: expanded, calm live state with elapsed time.
- Completed: collapsed by default unless the user left it open; final answer remains primary.
- Waiting for approval: open, focused prompt, explicit approve/reject controls.
- Waiting for user input: open, accessible choices or custom answer, clear submit/skip feedback.
- Reconnecting: open with a persistent textual connection state and non-destructive retry behavior.
- Failed: open with concise error summary and sanitized detail.
- Cancelled or budget exceeded: terminal summary with no misleading active spinner.
- Empty or delayed events: human-readable starting state without layout jump.

## Interaction Model

- The summary is a semantic disclosure button with a standard chevron icon and visible keyboard focus.
- User collapse is respected during routine streaming. A transition into a new attention state reopens the disclosure once.
- Tool rows expose successful details through a quiet disclosure and keep failure details open.
- Approval and user-input submissions show pending state, prevent duplicate requests, and surface API errors inline.
- Motion is short and state-driven; reduced-motion users receive static indicators and text.

## Content Requirements

- Prefer verbs and ordinary nouns: "Searched files", "Ran command", "Waiting for approval", "Connection lost".
- Avoid raw event names such as `tool.completed`, participant IDs, and internal phase labels in the primary view.
- Preserve exact technical content inside sanitized details when it helps recovery.
- All new labels go through the existing i18n path.

## Architecture and Data Flow

1. Backend and AgentScope runtime emit structured public events while excluding private reasoning.
2. `eventFold.ts` owns deterministic event-to-state reduction.
3. `transcriptModel.ts` owns grouping, summaries, attention flags, timing, and disclosure defaults.
4. `AgentRunStatusBridge.svelte` owns initial fetch, SSE, reconnect/backfill, and model dispatch.
5. `AgentTranscript.svelte` and part components render semantic, accessible UI without reinterpreting protocol payloads.

Presentation components must not parse raw events or invent state. Protocol fixes stay in backend/runtime layers; visual decisions stay in the render-model and components.

## Error Handling

- Malformed SSE events remain isolated and do not break the stream.
- Reconnect backfills from the last sequence and never duplicates visible events.
- Submit controls disable while a request is in flight and provide recoverable inline error text.
- Sanitization remains recursive for transcript detail and replayed tool data.
- Trimming replay context must preserve valid function-call/output pairing and never leave an orphaned provider item.

## Testing Strategy

- Backend: replay selection, sanitization, trimming, Responses conversion, and Chat Completions compatibility.
- Runtime: replay insertion order, function-call pairing, live commentary placement, and retry behavior.
- Frontend model: attention transitions, grouping, timing, reconnect, and error defaults.
- Frontend presentation: semantic disclosure, icon/focus behavior, successful detail access, pending controls, reduced motion, and source guardrails.
- Browser: real Agent Mode run in light and dark themes, desktop and narrow viewport, approval/input flows, reconnect or refresh recovery, completion, and keyboard navigation.

## Open Questions Resolved During Build

- Exact existing icon component for disclosure and status markers.
- Whether a successful tool summary can be derived safely from current sanitized payloads without adding protocol fields.
- Which runtime/provider combinations accept structured replay items and which require canonical Chat Completions conversion.
