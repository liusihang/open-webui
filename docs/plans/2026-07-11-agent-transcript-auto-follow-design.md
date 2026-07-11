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
- Reuse the existing coalesced scroll scheduler and its multi-frame
  `content-visibility` correction.
- Cover all rendered height changes, not only Agent-specific events, without
  introducing polling or completion-only fallbacks.

## Chosen design

Bind the message content wrapper in `Chat.svelte` and observe its rendered size
with a `ResizeObserver`. The observer callback checks the existing `autoScroll`
flag and calls the existing `scheduleScrollToBottom()` only when follow mode is
still active. The existing `messages-container` scroll handler remains the sole
owner of follow-mode changes, so user scrolling keeps the current semantics.

The observer lifecycle is owned by `Chat.svelte`: disconnect the previous
observer when the bound content element changes, and disconnect it during
component teardown. A small testable helper owns observer creation and cleanup;
`Chat.svelte` supplies the live `autoScroll` predicate and scroll scheduler.

## Rejected alternatives

- Propagating Agent transcript/final events through four component layers would
  couple generic scrolling to Agent internals and still miss other asynchronous
  layout growth.
- Forcing a scroll only on `run.completed` would hide the final answer eventually
  but would not follow incremental final deltas and would override intentional
  user scrolling.
- Polling scroll height would add permanent work and obscure missing event/lifecycle
  ownership.

## Verification

1. A focused unit test proves resize callbacks schedule scrolling only while the
   follow predicate is true and that teardown disconnects the observer.
2. Frontend type/check and relevant Agent transcript tests remain green.
3. Rebuild the exact committed WebUI image and replace only isolated
   `open-webui-pr7`.
4. Repeat the two-tool browser flow. The final answer must be visible in the first
   viewport without manual scrolling, while commentary/tool ordering and
   incremental final events remain unchanged.
5. During a concurrent model refresh, the UI must remain interactive. The
   protected `open-webui` container must remain unchanged.
