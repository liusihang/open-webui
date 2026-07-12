# Administrator Global System Prompt

## Objective

Add one administrator-managed global system prompt that applies to every user-facing model conversation, including ordinary chat and Agent Mode. The global prompt must be composed before the model-specific system prompt without keeping behavioral prompt text hardcoded in AgentScope.

## Scope

Included:

- One `Global System Prompt` field in Admin Settings > Interface.
- Database-backed configuration with an empty-string default.
- Ordinary chat prompt composition.
- Agent Mode leader prompt composition.
- Model-specific and chat/user system prompts remain supported after the administrator prompt.
- Request-level tests for both ordinary chat and Agent Mode.

Excluded:

- Separate Agent Mode prompt settings.
- Applying the prompt to internal task-model operations such as title, tag, query, or autocomplete generation.
- Treating prompt text as a security boundary. Authorization and tool access remain enforced by backend code.
- Provider-specific prompt implementations.

## Semantics

The effective instruction order is:

1. Administrator global system prompt.
2. Model-specific system prompt (`params.system`).
3. Existing chat/user system content.

The backend produces one normalized system instruction block with explicit section labels:

```text
[ADMINISTRATOR INSTRUCTIONS]
<global prompt>

[MODEL INSTRUCTIONS]
<model prompt and existing system content>
```

If the global prompt is empty, the request remains byte-for-byte equivalent to the current prompt-composition behavior wherever practical. If downstream prompt content is empty, only the administrator section is emitted.

Message order is not itself a formal authority mechanism across every provider. The section labels and single-message composition communicate application-level precedence consistently, while security-critical rules continue to be enforced outside the model prompt.

## Configuration

- Storage key: `chat.global_system_prompt`
- Environment default: `GLOBAL_SYSTEM_PROMPT`, default `""`
- Admin API field: `GLOBAL_SYSTEM_PROMPT: str`
- API: existing `GET /api/v1/chats/config` and `POST /api/v1/chats/config`
- Reads used during request composition come directly from `Config.get`, avoiding dependence on a worker-local compatibility snapshot.

## Backend design

Create one small prompt-composition module owned by OpenWebUI. Its public function reads the administrator prompt, accepts the provider model prompt and current system content, and returns one normalized system message immediately before provider dispatch.

Ordinary chat:

1. Read `chat.global_system_prompt` at the final model-dispatch boundary.
2. Resolve variables in the administrator prompt using the same supported template context as other system prompts.
3. Preserve current model/chat system resolution.
4. Compose the administrator section before provider model and chat system content.
5. Reuse the same helper in OpenAI, Ollama, function-pipe, and direct-connection dispatch paths.

Agent Mode:

1. AgentScope keeps only structural leader instructions required by the runtime, such as role identity and real output paths.
2. Remove the hardcoded public-progress/commentary contract introduced in commit `159c1e7`.
3. AgentScope model calls continue through the existing OpenWebUI model-authority endpoint.
4. The same final provider-dispatch helper composes the administrator prompt before the selected model prompt and the AgentScope leader system content.

No runtime schema field is needed. This avoids duplicate prompt state and makes the final provider request the single truth surface for precedence.

## Admin interface

Place the field in Admin Settings > Interface under a new `Model Instructions` section above context compaction.

- Label: `Global System Prompt`
- Supporting copy: `Prepended to system instructions for every ordinary chat and Agent Mode run. Model-specific prompts are applied afterward.`
- Multiline textarea using the existing settings component and theme tokens.
- Empty value is valid and disables the global prompt.
- Save through the existing Interface settings form, with existing success/error behavior.
- No new card, modal, color treatment, or animation.

## Compatibility and migration

- Default value is empty. Existing installations do not receive a new hidden behavior.
- The deployed AgentScope public-progress prompt is removed rather than silently migrated into the global field.
- Administrators who want public tool progress can paste that policy into the global field; it will then also affect ordinary tool-enabled chats, as requested.
- Provider adapters receive the same normalized system content through existing Chat Completions or Responses conversion.

## Tests

Backend configuration:

- Chat config GET returns `GLOBAL_SYSTEM_PROMPT`.
- Chat config POST persists and returns the value.
- Empty string is accepted.

Prompt composition:

- Global prompt precedes model/chat prompt.
- Empty global prompt preserves downstream content without wrappers.
- Empty downstream content emits only the administrator section.
- Prompt variables are resolved before provider dispatch.

Ordinary chat:

- Captured provider request contains one system instruction with administrator content before model content.
- Internal task-model requests are unchanged.

Agent Mode:

- Agent model-authority dispatch composes the global prompt before model and leader system content.
- Hardcoded public-progress wording is absent when the setting is empty.

Frontend:

- Type/build checks cover the new config field and binding.
- Browser acceptance verifies load, edit, save, refresh, and empty-value behavior in light and dark themes.

Live acceptance:

- Save a unique marker through the admin UI.
- Verify API and persistent config readback.
- Capture an ordinary chat provider request and confirm exact order.
- Capture an Agent Mode model request and confirm exact order.
- Clear the value and confirm the marker disappears from both paths.

## Rollback

Rollback is limited to reverting the implementation commit and rebuilding the OpenWebUI and AgentScope runtime images. The new config row is inert on older code and does not require destructive database cleanup.
