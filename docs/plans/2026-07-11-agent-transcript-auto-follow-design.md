# Agent transcript auto-follow design

## Problem

Agent Mode updates its transcript and final answer inside
`AgentRunStatusBridge`, independently of the ordinary chat completion handler.
The message DOM can therefore grow without calling `Chat.svelte`'s existing
`scheduleScrollToBottom()`. On the isolated PR7 browser surface, the final answer
was present and accessible in the DOM but the internal `messages-container`
remained at `scrollTop = 0`, leaving the final answer below the visible viewport.

## Required behavior

- Continue following transcript and final-answer growth when the user is already
  following the bottom of the conversation.
- Stop following immediately when the user deliberately scrolls away from the
  bottom; never steal their reading position.
- Cover all rendered height changes, not only Agent-specific events, without
  introducing polling, input-device heuristics, or completion-only fallbacks.

## Chosen design

Keep the transcript in normal layout flow. The existing nested `h-full` flex
children were shrinkable to the viewport, so the growing message list overflowed
their boxes instead of increasing their layout height. That prevented a bottom
anchor from participating in the scroller's native anchoring algorithm. Replace
those three wrappers with non-shrinking `min-h-full`/`flex-none` flow containers.

Add a one-pixel anchor immediately after the message `<ul>`. While the existing
`autoScroll` state says the user is at the bottom, a class on the scroller excludes
the list from anchor selection and the sentinel owns `overflow-anchor: auto`.
Browser scroll anchoring then keeps it at the same viewport position across
transcript, Markdown, media, and final-delta growth. Once the user scrolls upward,
the class is removed: normal message anchors are restored for top pagination and
the off-screen sentinel no longer pulls the viewport. The existing scroll handler
therefore controls anchor ownership and the jump-button UI, not imperative follow
scrolling.

## Rejected alternatives

- Propagating Agent transcript/final events through four component layers would
  couple generic scrolling to Agent internals and still miss other asynchronous
  layout growth.
- Forcing a scroll only on `run.completed` would hide the final answer eventually
  but would not follow incremental final deltas and would override intentional
  user scrolling.
- ResizeObserver plus animation-frame scrolling was rejected after live testing:
  content growth can race its own `scroll` event, while attempts to infer user
  intent from wheel, touch, key, scrollbar, layout-grace, or scroll direction
  remain incomplete and can still steal or lose follow state.
- Polling scroll height would add permanent work and obscure missing event/lifecycle
  ownership.

## Verification

1. A focused structural test freezes the normal-flow wrappers, sentinel, and
   `overflow-anchor` ownership and rejects the removed observer/scheduler path.
2. Frontend type/check and relevant Agent transcript tests remain green.
3. Rebuild the exact committed WebUI image and replace only isolated
   `open-webui-pr7`.
4. Repeat the two-tool browser flow. The final answer must be visible in the first
   viewport without manual scrolling, while commentary/tool ordering and
   incremental final events remain unchanged.
5. While a response grows, deliberately scroll upward and prove subsequent
   growth leaves `scrollTop` unchanged; returning to the bottom must resume follow.
6. During a concurrent model refresh, the UI must remain interactive. The
   protected `open-webui` container must remain unchanged.
