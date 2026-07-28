# Administrator-Managed Chat and Agent Mode Profiles

Status: approved on 2026-07-26

Related design: `docs/plans/2026-07-26-chat-agent-conversation-modes-design.md`

## Summary

Chat and Agent conversations will use separate administrator-managed default capability profiles while continuing to share the existing system default-model selection.

The mode profile controls the initial System Prompt, Terminal, Tools, Skills, Filters, and supported feature toggles. It does not control the selected model or Reasoning Depth. Users may select any permitted model and may temporarily adjust capability selections inside a conversation, but they cannot create or persist personal Chat/Agent templates.

Each administrator save creates an immutable profile revision. A conversation binds to one revision when it is created and keeps that revision for its entire lifetime. Later administrator changes affect only future conversations.

The mode System Prompt is an administrator-enforced server-side instruction. Ordinary users cannot read, replace, or submit it. Existing model and user System Prompts remain lower-precedence layers.

## Goals

1. Give administrators one authoritative place to configure distinct Chat and Agent defaults.
2. Keep model selection independent from conversation mode and preserve the current system default-model behavior.
3. Let users temporarily adjust capability selections without changing administrator configuration.
4. Keep the mode System Prompt server-authoritative and non-replaceable.
5. Preserve deterministic behavior for refresh, continuation, regeneration, temporary save, clone, and share flows.
6. Make administrator changes affect only future conversations.
7. Support real multi-worker operation with explicit cache invalidation and atomic revision changes.
8. Fail explicitly when a required revision or enforced prompt is corrupt; never silently fall back to another profile.

## Non-goals

- Separate Chat and Agent default models.
- Restricting which permitted model a user may select.
- A mode-specific Reasoning Depth default.
- Per-user saved mode templates.
- Duplicating the existing model parameter tree for temperature, top-p, maximum tokens, or other provider parameters.
- Granting Tool, Skill, Filter, Terminal, or feature access through a default selection.
- Dynamically changing an existing conversation when an administrator edits a profile.
- Building or deploying a Docker image before the separately recorded build checkpoint is resumed.
- Modifying formal live as part of design or implementation.

## Product Semantics

### Ownership

Only administrators may configure Chat and Agent mode profiles. Ordinary users can consume the resulting defaults and make conversation-local changes, but no user setting may become a persistent mode-profile override.

### Model selection

Both modes use the existing system default model and the existing model-selection priority. The mode profile contains no model identifier.

Users may switch to any model they are authorized to use. Changing a model does not change the conversation mode or profile revision.

### Initial defaults versus enforced policy

The fields have two different semantics:

- System Prompt is enforced policy. The administrator profile provides the immutable highest-precedence prompt layer.
- Terminal, Tools, Skills, Filters, and features are creation-time defaults. Users may temporarily alter them.

Temporary changes are stored only in the current conversation or draft state and never write back to the administrator profile.

## Profile Fields

Each Chat or Agent revision contains:

```json
{
  "schema_version": 1,
  "system_prompt": "Administrator-enforced prompt",
  "defaults": {
    "terminal_id": null,
    "tool_ids": [],
    "skill_ids": [],
    "filter_ids": [],
    "feature_ids": []
  }
}
```

Supported feature identifiers initially cover the existing default-capability surface:

- `web_search`
- `code_interpreter`
- `image_generation`

The profile deliberately excludes:

- model IDs;
- Reasoning Depth;
- ordinary model/provider parameters;
- permissions or access grants.

### Tri-state inheritance

Every capability field distinguishes three states:

1. Omitted or `inherit`: inherit the selected model metadata default.
2. Explicit empty or disabled value: override the model metadata with no default selection.
3. Explicit configured value: use the administrator selection before capability and permission filtering.

This distinction is required so an administrator can intentionally disable a model-level default instead of merely omitting configuration.

## Data Model

### Current profile heads

Add a `conversation_mode_profile_heads` table with one row for each supported mode.

Suggested fields:

- `mode`, primary key: `chat` or `agent`;
- `current_revision_id`, foreign key to the revision table;
- `baseline_revision_id`, immutable compatibility revision created at migration cutover;
- `cutover_at`;
- `updated_at`;
- `updated_by`.

The head is the only mutable profile record. Updating it and inserting the new revision occur in one database transaction.

### Immutable revisions

Add a `conversation_mode_profile_revisions` table.

Suggested fields:

- `id`, opaque UUID primary key;
- `mode`;
- monotonic `revision_number` within the mode;
- `schema_version`;
- `system_prompt`;
- `defaults`, JSON;
- `content_hash` over canonical profile content;
- `created_at`;
- `created_by`;
- optional `restored_from_revision_id`.

Revision content cannot be updated or deleted while referenced. Restoring an older profile creates a new revision with copied content rather than mutating history.

### Persistent conversation binding

Add a server-owned nullable `mode_profile_revision_id` foreign key to the Chat persistence model.

New conversations must receive a non-null binding before provider or Agent runtime dispatch. Generic chat update/import payloads cannot select or mutate this field.

The revision identifier may be exposed as audit metadata, but it is never treated as client authority.

### Temporary conversation binding

Temporary conversations need stable profile behavior without storing message content. Add an expiring binding keyed by user and temporary conversation ID:

```text
user_id + temporary_conversation_id -> mode + revision_id + expires_at
```

The first temporary request atomically binds the current revision. Later requests and a save-to-history operation reuse it. Expired bindings are removed by the existing startup-singleton cleanup path so four application workers do not duplicate cleanup work.

## Trust and Security Boundaries

- Ordinary user APIs never return the enforced System Prompt.
- Only administrator endpoints may read complete revision content.
- The backend loads the bound revision and composes the request Prompt.
- Client-supplied System Prompt content cannot replace the administrator layer.
- Client-supplied revision identifiers cannot change an existing binding.
- A new persisted or temporary conversation may bind only the current revision selected by the server.
- Content hashes are verified when revisions are read for execution.
- Missing revisions, mode/revision mismatches, invalid schema versions, and hash failures stop execution.
- Capability defaults never grant access. Model support, global feature gates, user permissions, and resource availability remain authoritative.

## Runtime Initialization

### New conversation

1. The user selects Chat or Agent before sending the first message.
2. The existing model-selection logic chooses the system default model or the user's explicit model.
3. The frontend receives sanitized public defaults for the current mode revision.
4. Explicit profile fields override corresponding model metadata defaults.
5. Omitted profile fields inherit model metadata.
6. The result is filtered by model support, deployment feature gates, resource existence, and user permission.
7. The user may temporarily adjust the resulting capability state.
8. The first request atomically binds the server-selected revision before dispatch.

The frontend revision identifier is a consistency hint only. The backend verifies that a new conversation binds the current head and returns a conflict or refresh instruction if the public configuration is stale.

### Existing conversation

An existing conversation always uses its persisted revision. The current profile head is not consulted for execution.

Refresh, regeneration, continuation, cancellation recovery, and Agent resume all retain the same revision.

### Model changes

Changing the selected model does not reapply the mode defaults.

- Keep user-selected Terminal, Tools, Skills, Filters, and features that remain valid.
- Remove items unsupported by the new model or unavailable to the user.
- Emit one structured warning describing removed items without exposing sensitive configuration.
- Keep the mode, revision, and enforced System Prompt unchanged.

### Persistent user preferences

Persistent user defaults must not override administrator mode profiles for controlled fields. Existing user preferences such as an always-enabled web search setting do not replace the mode's creation-time default.

Users may still toggle a supported feature in the current conversation. Existing user System Prompt behavior may remain, but it is composed only as a lower-precedence layer.

## Prompt Composition

The backend constructs one deterministic Prompt stack:

1. administrator mode System Prompt;
2. selected model System Prompt from the existing resolved model configuration;
3. user System Prompt, if the product continues to allow one.

The first layer is immutable and cannot be removed by request parameters or user settings. The final provider-compatible representation may be one canonical system message or multiple provider-supported system blocks, but the composition order and server authority must be identical across providers.

An explicitly empty administrator Prompt in a compatibility baseline is valid and hashable. A missing field, invalid schema, or corrupt stored value is not equivalent to an intentional empty Prompt and must fail closed.

## Capability Validation and Failure Behavior

### Administrator save

Reject the save when:

- referenced Tool, Skill, Filter, or Terminal IDs do not exist or are inactive;
- IDs are duplicated or malformed;
- the profile contains a known impossible conflict, such as a default Terminal and default Code Interpreter combination that the product treats as mutually exclusive;
- the System Prompt or schema is invalid.

A feature that is valid but globally disabled may be saved with a visible warning so it can become usable after the global gate is enabled.

Model compatibility produces warnings rather than a global save failure because users may select different models.

### Runtime drift

When a valid saved resource later becomes unavailable or a user lacks access:

- skip only the affected capability;
- preserve the rest of the profile;
- return a structured warning that the frontend displays once;
- do not block the conversation.

Reject malformed client requests and mutually exclusive capability combinations with a stable parameter error rather than silently rewriting them.

Revision corruption, missing enforced Prompt data, or revision/mode mismatch blocks the request and never falls back to another profile or mode.

## Administrator UI

Add an administrator entry under:

```text
Admin Settings -> Models -> Chat / Agent Defaults
```

The page uses Chat and Agent tabs. Each tab contains:

- enforced System Prompt editor;
- tri-state Terminal control: inherit, disabled, or selected Terminal;
- tri-state Tools, Skills, and Filters controls;
- tri-state Web Search, Code Interpreter, and Image Generation controls;
- current revision number, author, and creation time;
- validation warnings and errors;
- read-only revision history;
- restore action that creates a new revision from an older one.

The page does not contain model selection or Reasoning Depth controls.

Two administrators cannot overwrite each other silently. Save requests include the expected current revision and return `409` when the head changed after the editor loaded.

## API Contract

### Administrator endpoints

Provide administrator-only operations to:

- read the complete current Chat and Agent profiles;
- validate and create a new revision for one mode;
- list revision history metadata;
- read a historical revision;
- restore historical content by creating a new revision.

The exact route names may follow the existing config-router conventions, but they must use typed request/response schemas and stable validation error codes.

### Public configuration

The ordinary frontend configuration response may expose only:

- mode;
- current revision ID;
- schema version;
- sanitized capability defaults.

It must not expose:

- System Prompt content;
- revision history;
- administrator identity;
- internal hashes.

### Multi-worker invalidation

After the revision insert and head update commit, publish a precise cache invalidation event for the changed mode head. All workers must observe the new head for future conversations. Revisions are immutable and can be safely cached by revision ID.

## Migration and Compatibility

### Compatibility baselines

The database migration creates one baseline revision for Chat and one for Agent:

- valid explicitly empty administrator System Prompt;
- every capability field set to inherit;
- recorded baseline revision ID and cutover time;
- initial current head pointing at the baseline.

This preserves the pre-feature behavior until an administrator intentionally creates a new profile.

### Existing conversations

Pre-cutover conversations without a revision use the existing canonical conversation-mode inference:

1. explicit stored mode;
2. Agent run or Agent message evidence;
3. Chat otherwise.

On the next continuation, update, or save, the backend atomically binds the corresponding compatibility baseline rather than the current administrator head. This prevents an old conversation from unexpectedly adopting a later profile.

### Lifecycle operations

- Continue, refresh, and regenerate: retain the bound revision.
- Local branch or clone: copy the source revision server-side.
- Share read: retain the server-side source binding and never expose the Prompt.
- Create a local copy from a trusted share: preserve the source revision.
- Save a temporary conversation: transfer its temporary binding.
- Export: include only mode and revision audit ID; do not export Prompt content.
- Import: ignore the imported revision as authority and bind the current revision for the imported mode.
- Delete a conversation: remove its binding/reference, not the immutable revision.

Referenced revisions cannot be deleted. Unreferenced revision retention may be addressed by a later explicit policy; it is not part of this change.

## Error Semantics

Use stable machine-readable errors in addition to localized text. Suggested cases include:

| HTTP | Code | Meaning |
|------|------|---------|
| 400 | `invalid_mode_profile` | Profile schema, IDs, or capability combination is invalid. |
| 403 | `mode_profile_admin_required` | A non-admin attempted to read or mutate private profile data. |
| 409 | `mode_profile_revision_conflict` | The current head changed after the administrator loaded the editor. |
| 409 | `mode_profile_binding_mismatch` | A client attempted to alter a bound conversation revision. |
| 503 | `mode_profile_unavailable` | A new conversation has no valid current profile head. |
| 500/503 | `mode_profile_integrity_error` | A bound revision is missing, corrupt, or mode-incompatible. |

Capability drift is returned as a structured warning rather than one of these fatal errors.

## Verification Strategy

### Backend tests

- administrator-only read/write/history access;
- immutable revision records and atomic head changes;
- optimistic concurrency conflict;
- strict ID/schema/conflict validation;
- sanitized public config never exposes Prompt content;
- new persistent and temporary conversations bind current revisions atomically;
- existing conversations retain old revisions after head changes;
- legacy conversations bind compatibility baselines;
- deterministic Prompt composition order;
- client Prompt/revision tampering rejection;
- revision hash and schema integrity failure;
- permission/resource drift warnings;
- model changes keep valid temporary choices and remove invalid choices;
- clone/share/temporary-save preservation and import rebinding;
- temporary-binding expiry and singleton cleanup;
- Redis head invalidation across workers.

### Frontend tests

- administrator Chat/Agent tabs and tri-state controls;
- no model or Reasoning Depth field;
- validation, stale-edit conflict, history, and restore behavior;
- ordinary users never receive or render the enforced Prompt;
- mode-specific public defaults initialize new conversations;
- explicit empty values override model metadata;
- omitted values inherit model metadata;
- user changes remain conversation-local;
- model switching retains valid choices and displays removal warnings;
- draft, refresh, and temporary-save bindings remain stable.

### Isolated four-worker acceptance

After code, focused tests, documentation, commits, and explicit resumption of the paused image-build checkpoint:

1. Start the isolated PR7 stack with four workers and prove requests hit four distinct PIDs.
2. Change the Chat head and prove all workers read the new revision for new conversations.
3. Prove conversations bound before the change continue using the prior revision on every worker.
4. Repeat independently for Agent.
5. Prove cache invalidation does not flush or mutate immutable historical revisions.
6. Prove temporary-binding cleanup and other startup singleton work execute once.
7. Run real Chat and Agent SSE, Tool, Terminal, Skill, cancellation, refresh, recovery, and concurrency probes.
8. Restore the isolated stack to its original worker count and recheck container/image/health/restart anchors.
9. Keep formal live read-only and prove its before/after state is unchanged.

## Implementation Boundaries

- Use test-driven development for every behavior slice.
- Keep permission checks separate from default selection.
- Reuse the existing model-parameter resolver rather than creating mode-specific provider parameter trees.
- Reuse precise Redis invalidation patterns already verified for multi-worker PR7 operation.
- Update API, database, developer, and operational documentation with the final contract.
- Commit verified design, implementation plan, backend, frontend, and documentation slices without pushing.
- Keep remote Docker image construction paused until the post-implementation checkpoint is explicitly resumed.
- Never modify, restart, rebuild, or switch formal live during this work.
