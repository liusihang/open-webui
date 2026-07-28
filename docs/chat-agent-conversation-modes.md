# Chat and Agent conversation modes

OpenWebUI supports two conversation-level execution modes in the same deployment:

- `chat`: the ordinary provider-backed chat pipeline, including multi-model requests.
- `agent`: the AgentScope runtime pipeline, with one leader model per request.

The mode is selected before the first user message and is immutable for the lifetime of the conversation. The global `ENABLE_AGENT_MODE` setting is only a capability gate; it never selects Agent mode for a conversation.

## Persisted contract

The canonical value is stored in the existing chat JSON:

```json
{
	"mode": "chat"
}
```

No database schema migration is required. Create, import, temporary-chat save, share snapshot, and generic update paths preserve or validate this value.

Legacy conversations without `mode` are classified as `agent` when either their message history contains Agent run evidence or an `agent_run` row exists for the original chat. Other legacy conversations resolve to `chat`. Read APIs return the resolved value; the next normal write persists it.

## Completion API

Product chat completion requests may include the top-level field:

```json
{
	"chat_mode": "agent"
}
```

Rules:

- A new request without `chat_mode` defaults to `chat`.
- An existing request without `chat_mode` uses the persisted or inferred mode.
- A request that conflicts with the canonical mode returns HTTP `409` with `code: "conversation_mode_mismatch"` before message, provider, or Agent run writes.
- An unsupported value returns HTTP `400` with `code: "invalid_conversation_mode"`.
- Agent mode with the deployment capability disabled returns HTTP `503` with `code: "agent_mode_disabled"`; it never falls back to Chat.
- `chat_mode` is removed before ordinary provider dispatch.

Mode-less existing conversations are claimed atomically before execution. PostgreSQL uses a row lock; SQLite starts an immediate write transaction. This prevents concurrent requests from starting Chat and Agent execution for the same legacy conversation.

## Chat update and read APIs

`POST /api/v1/chats/{id}` uses the same atomic claim before merging client state. A stale autosave therefore cannot overwrite a concurrently claimed mode.

Owner, admin, access-grant, folder-share, and share-token reads return the resolved canonical mode. Share-token reads use the original `chat_id` and owner when checking Agent run evidence rather than the public share token.

## Frontend behavior

The chat navbar contains a centered `Chat / Agent` selector.

- Before the first user message, the selector changes the pending conversation mode.
- After the conversation is created, selecting the other mode offers to start a new conversation in that mode.
- Agent mode narrows only the request-local model list to one leader; it does not mutate the user's Chat multi-model selection.
- If Agent capability becomes unavailable, existing Agent history remains readable and the composer is disabled with an explicit notice.
- Ordinary Chat preserves the existing reasoning-depth request and UI behavior.

## Verification surfaces

Focused backend coverage lives in:

- `backend/open_webui/test/agent/test_conversation_mode.py`
- `backend/open_webui/test/agent/test_chat_conversation_mode.py`
- `backend/open_webui/test/agent/test_chat_entry_agent_mode.py`

Focused frontend coverage lives in:

- `src/lib/components/chat/agentModeRequest.test.ts`
- `src/lib/components/chat/ConversationMode.presentation.test.ts`
- `src/lib/components/chat/ConversationMode.compile.test.ts`
