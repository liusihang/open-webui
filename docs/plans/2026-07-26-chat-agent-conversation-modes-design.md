# Immutable Chat and Agent Conversation Modes

Status: approved on 2026-07-26

## Summary

OpenWebUI will expose two explicit conversation modes in one deployment:

- **Chat** keeps the existing OpenWebUI chat completion, multi-model, retrieval, web search, and ordinary tool path.
- **Agent** uses the AgentScope runtime, Agent event protocol, transcript, approvals, terminal access, pause/resume, and Agent lifecycle.

The mode is selected before the first message creates a conversation. Once created, the conversation mode is immutable. The deployment-level `ENABLE_AGENT_MODE` setting remains an Agent capability gate; it no longer selects Agent execution for every eligible product chat.

## Goals

1. Preserve the pre-Agent ordinary Chat behavior without requiring a separate deployment.
2. Make Agent execution an explicit user choice at conversation creation.
3. Prevent a conversation from changing execution semantics after history has been created.
4. Keep mode selection visible at the top of the conversation surface.
5. Preserve existing Agent transcript, approval, cancellation, recovery, and multi-worker behavior.
6. Maintain compatibility with existing ordinary chats, existing Agent chats, and older clients.
7. Fail explicitly when Agent capability or runtime is unavailable; never silently fall back to Chat.

## Non-goals

- Switching mode for individual messages inside an existing conversation.
- Merging ordinary Chat and Agent event protocols into a single renderer.
- Creating separate Chat and Agent database tables.
- Replacing the existing model selector.
- Adding a user-configurable default mode in the first version; new conversations default to Chat.
- Modifying or deploying to formal live as part of implementation.

## User Experience

### New conversation

A compact segmented selector is centered in the sticky conversation header:

```text
┌─────────────────────────────────────────────┐
│                 [ Chat | Agent ]            │
│                                             │
│                Conversation                 │
│                                             │
│                 Message input               │
└─────────────────────────────────────────────┘
```

- `Chat` is selected by default.
- The selector is independent of the model selector.
- The user may change the mode until the first message is submitted.
- Agent is visibly disabled with an explanatory tooltip when the user or deployment cannot start Agent conversations.

### Existing conversation

- The selected mode remains visible in the same top selector.
- The conversation mode cannot be modified.
- Selecting the other segment opens a confirmation explaining that a new conversation will be created in that mode.
- Confirming navigates to a new empty conversation with the requested mode preselected.
- Shared or otherwise read-only conversations display the mode without offering a switch action.

### Temporary conversation

A temporary conversation has the same immutable semantics within its in-memory lifetime. Its selected mode is reset only when a new temporary conversation is initialized.

## Domain Model

### Canonical value

The existing chat JSON gains one field:

```json
{
  "mode": "chat"
}
```

Allowed values are exactly:

- `chat`
- `agent`

The field is a server-enforced conversation invariant even though it is stored in JSON.

Reasons for using the existing JSON instead of a new database column:

- No database migration is required.
- Clone, export, and import flows already carry the chat JSON.
- Conversation mode is not needed as an indexed query dimension.
- The backend already loads the chat before accepting a follow-up request.

Generic chat updates must reject attempts to change an existing mode. Imports must validate the value before writing it.

### Request contract

The first product chat request includes a top-level field:

```json
{
  "chat_mode": "agent"
}
```

`chat_mode` is not provider input and must be removed before provider dispatch. It is consumed by OpenWebUI product-chat routing.

### Resolution rules

The backend resolves one canonical mode before it chooses an execution path:

```text
if internal Agent model call:
    bypass product conversation mode routing
else if new persisted conversation:
    canonical = requested chat_mode or "chat"
    validate capability and permission
    create chat with chat.mode = canonical
else if existing persisted conversation:
    canonical = stored chat.mode or infer_legacy_mode(chat)
    if requested chat_mode exists and requested != canonical:
        reject with conversation_mode_mismatch
    persist inferred mode on the next normal chat write
else if temporary/local conversation:
    canonical = requested chat_mode or client session mode or "chat"

if canonical == "agent":
    require Agent capability and route to AgentScope
else:
    route to ordinary Chat
```

The canonical value used for persistence must be the same value used for routing. A new conversation must not be created as one mode and executed as the other.

## Backend Routing

### Chat mode

Chat mode continues through the existing ordinary path:

- ordinary completion streaming;
- multi-model response placeholders;
- existing retrieval and knowledge behavior;
- web search and existing filters;
- ordinary tool/function handling;
- existing status history and tool-card rendering.

Enabling Agent capability must not change this behavior.

### Agent mode

Agent mode keeps the current Agent product path:

- one leader model per run;
- AgentScope runtime dispatch;
- `agent_run` persistence;
- `text.delta`, `assistant_note`, and `action_summary` public commentary;
- structured `tool.*`, approval, user-input, cancellation, and recovery events;
- `final.delta` final-answer streaming;
- Agent transcript rendering and refresh backfill.

Single-model constraints and Agent-specific request shaping apply only when the canonical conversation mode is `agent`.

### Capability and permission

`ENABLE_AGENT_MODE` means the deployment provides Agent capability. It does not select a conversation mode.

Starting or continuing an Agent conversation requires:

1. deployment Agent capability enabled;
2. user Agent permission, if configured;
3. a valid Agent leader model;
4. an available Agent runtime when the run is dispatched.

Chat conversations do not depend on Agent runtime health.

## Existing Data Compatibility

Existing chats do not have an explicit mode. Their mode is resolved without a bulk database scan:

1. A valid stored `chat.mode` always wins.
2. Otherwise, an existing `agent_run` for the chat or an assistant message containing `agent_run_id` classifies it as Agent.
3. Otherwise, it is Chat.

The inferred mode is persisted on the next normal write. Read-only access may display the inferred mode without mutating the row.

Legacy mixed conversations that contain any Agent run are classified as Agent. This preserves the stronger execution/runtime contract and prevents a later message from unexpectedly returning to the ordinary path.

Older clients remain compatible:

- a new request without `chat_mode` defaults to Chat;
- an existing request without `chat_mode` uses the stored or inferred mode;
- a supplied conflicting mode is rejected.

## Clone, Import, Export, and Share

- Cloning preserves the source mode.
- Export includes the mode in the existing chat JSON.
- Import accepts `chat`, `agent`, or a missing value; any other value is rejected.
- A missing imported mode uses the same legacy inference rules.
- Shared chats expose the mode as read-only.
- Cloning a shared Agent chat produces another Agent chat, subject to the destination user's Agent permission and deployment capability.

## Error Semantics

Errors use stable machine-readable codes in addition to human-readable text.

| HTTP | Code | Meaning |
|------|------|---------|
| 400 | `invalid_conversation_mode` | The request or import contains an unsupported value. |
| 403 | `agent_mode_forbidden` | The user may not create or continue Agent conversations. |
| 409 | `conversation_mode_mismatch` | A request attempts to use a mode different from the persisted conversation mode. |
| 503 | `agent_mode_disabled` | The deployment does not currently expose Agent capability. |
| 503 | `agent_runtime_unavailable` | Agent mode is valid but the runtime cannot accept the run. |

An existing Agent conversation remains readable when Agent capability is disabled. Its composer is disabled with a visible explanation. The backend still enforces the same rule for non-browser clients.

Agent runtime failure must never cause automatic Chat fallback. Existing run failure persistence and visible retry/recovery behavior remain authoritative.

## Frontend State and Rendering

The frontend holds a typed `conversationMode: 'chat' | 'agent'` state.

- New conversation initialization uses `chat` unless navigation explicitly preselects Agent.
- Loading a persisted chat replaces local draft state with the server-resolved mode.
- The mode is included in draft state only before a conversation exists.
- The first completion request sends `chat_mode`.
- Follow-up requests may send the known mode for consistency; the server remains authoritative.
- Model list narrowing and Agent reasoning controls react to `conversationMode === 'agent'`, not the global feature flag.
- Message-level `agent_run_id` continues to select Agent transcript rendering and recovery behavior.

The top selector should be a small dedicated component so locked/new/read-only behavior can be tested independently from the full chat page.

## Security and Integrity

- Do not trust a client-side disabled selector.
- Reject mode mutation through the generic chat update API.
- Validate imported mode values.
- Preserve the existing internal Agent model-call bypass so runtime callbacks do not recursively start product Agent runs.
- Verify chat ownership before mode resolution for existing conversations.
- Keep Agent permissions and runtime service authentication unchanged.

## Verification Strategy

### Backend focused tests

- new request without mode creates Chat and uses the ordinary path;
- new Agent request creates `chat.mode=agent`, creates an Agent run, and dispatches runtime;
- enabling Agent capability does not route a Chat request into Agent;
- existing Chat and Agent follow-ups use their stored mode;
- mismatched requests return `409` without adding messages or runs;
- invalid import/update mode is rejected;
- generic updates cannot mutate mode;
- legacy chats infer Chat or Agent correctly;
- Agent disabled/forbidden/runtime-unavailable errors remain distinct;
- internal Agent model calls bypass product routing;
- clone/import/export preserve mode.

### Frontend focused tests

- top selector renders Chat and Agent states;
- new conversation selection changes the first request payload;
- selection locks after creation;
- selecting the other locked mode opens the new-conversation confirmation;
- refresh uses the server mode, not stale draft state;
- Agent-only single-model and reasoning behavior does not affect Chat;
- disabled/read-only states are accessible by keyboard and screen reader;
- temporary conversations keep one mode for their lifetime.

### Isolated integration acceptance

In one isolated deployment with Agent capability enabled:

1. Create and continue a Chat conversation.
2. Prove it creates no `agent_run` and never contacts the Agent runtime.
3. Exercise ordinary streaming, retrieval, web search, tools, and multi-model where supported.
4. Create and continue an Agent conversation.
5. Prove it creates an `agent_run`, contacts the AgentScope runtime, and preserves commentary/tool/final event order.
6. Refresh both conversations and continue them.
7. Attempt forged mode changes and verify rejection.
8. Stop the Agent runtime and prove Chat remains healthy while Agent fails explicitly.
9. Re-enable the runtime and prove the same Agent conversation can continue without mode mutation.
10. Run focused multi-worker checks to ensure mode persistence and route choice are consistent across workers.

Formal live is read-only and out of scope until a separate, explicit deployment authorization is given.

## Rollout

1. Land backend resolution and invariant tests.
2. Land frontend selector and request behavior behind Agent capability discovery.
3. Run focused backend/frontend regression suites.
4. Build and deploy only to the isolated PR7 stack.
5. Execute the dual-path acceptance matrix.
6. Restore the isolated stack if its configuration was changed.
7. Produce an explicit live go/no-go decision; do not switch live as part of this task without separate authorization.
