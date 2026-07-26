# Chat and Agent Mode Profiles Implementation Plan

Design: `docs/plans/2026-07-26-chat-agent-mode-profiles-design.md`

Truth surface:

- Worktree: `/Users/liusihang/.codex/worktrees/d790/openwebui`
- Branch: `codex/pr7-chat-agent-dual-mode-20260726`
- Starting commit: `0aea113c4`
- Existing dual-mode implementation commit: `f7583ff4e`
- Current Alembic head: `f8a9b0c1d2e3`
- Formal live remains read-only and out of scope.
- Remote Docker image construction remains paused until a later explicit checkpoint.

## Execution Rules

1. Use test-driven development for every behavior slice: prove the focused failure, implement the smallest complete change, and rerun the focused suite.
2. Keep mode-profile policy in a dedicated backend domain module. Do not spread prompt composition, hashing, tri-state resolution, or binding rules across routers and UI code.
3. Keep permissions separate from defaults. A profile may request a capability but can never grant access.
4. Keep the existing system default-model and model-parameter resolver. Do not introduce Chat/Agent model IDs or duplicate temperature/system/provider parameter trees.
5. Treat the administrator System Prompt and revision binding as server authority. Never trust values submitted through ordinary chat payloads.
6. Preserve unrelated dirty and ignored files. Stage only the verified task slice at each commit.
7. Update `handoff/chat-agent-dual-mode-20260726/` after every red/green checkpoint, migration result, review finding, commit, and runtime checkpoint.
8. Do not push without explicit authorization.
9. Do not build or deploy a Docker image until the paused remote-build checkpoint is explicitly resumed.
10. Never modify, restart, rebuild, or switch formal live.

## Phase A: Pure Profile Contract

### A1. Write failing schema, hash, and merge tests

Create focused pure tests covering:

- allowed modes are exactly `chat` and `agent`;
- schema version validation;
- canonical content hashing is stable across dictionary order;
- an explicitly empty System Prompt is valid;
- a missing or malformed System Prompt is invalid;
- tri-state omitted fields inherit model metadata;
- explicit empty values clear model defaults;
- explicit values override model defaults;
- model IDs and Reasoning Depth are rejected as profile fields;
- known Terminal/Code Interpreter conflicts are rejected;
- Prompt composition order is administrator, model, then user;
- public serialization omits Prompt, hashes, authors, and history metadata.

Target files:

- `backend/open_webui/test/agent/test_conversation_mode_profiles.py`
- `backend/open_webui/agent/conversation_mode_profiles.py`

Red command:

```bash
PYTHONPATH=backend /Users/liusihang/openwebui/.venv/bin/pytest -q \
  backend/open_webui/test/agent/test_conversation_mode_profiles.py
```

### A2. Implement the pure contract

Implement typed profile content, tri-state defaults, validation, canonical hashing, sanitized public projection, and Prompt composition without database, FastAPI, Redis, or frontend dependencies.

Keep stable typed exceptions and machine-readable error codes in the domain module.

### A3. Commit checkpoint

After focused tests and Ruff pass, commit the pure contract separately.

Suggested commit:

```text
test(agent-mode): define mode profile contract
```

## Phase B: Database Schema and Repository

### B1. Write migration and repository tests first

Add failing tests for:

- migration upgrades from Alembic head `f8a9b0c1d2e3`;
- Chat and Agent compatibility baseline revisions are inserted;
- baseline Prompt is explicitly empty and hash-valid;
- all capability fields default to inherit;
- profile heads point to baseline revisions;
- Chat gains a nullable server-owned revision reference;
- temporary bindings enforce a unique user/conversation key and have an expiry index;
- downgrade removes only the new profile schema;
- repository reads heads and immutable revisions;
- immutable revision content has no update/delete mutation path;
- an administrator save inserts a revision and switches the head atomically;
- a stale expected head returns a typed conflict;
- concurrent saves do not share a revision number or lose a head update;
- revision integrity is checked on read.

Target files:

- `backend/open_webui/test/util/test_conversation_mode_profile_migration.py`
- `backend/open_webui/test/agent/test_conversation_mode_profile_store.py`
- `backend/open_webui/migrations/versions/<new_revision>_add_conversation_mode_profiles.py`
- `backend/open_webui/models/conversation_mode_profiles.py`
- `backend/open_webui/models/chats.py`

Migration test command:

```bash
WEBUI_SECRET_KEY=codex-test-placeholder \
PYTHONPATH=backend /Users/liusihang/openwebui/.venv/bin/pytest -q \
  backend/open_webui/test/util/test_conversation_mode_profile_migration.py
```

Repository test command:

```bash
PYTHONPATH=backend /Users/liusihang/openwebui/.venv/bin/pytest -q \
  backend/open_webui/test/agent/test_conversation_mode_profile_store.py
```

### B2. Implement schema and transactional repository

Add:

- `conversation_mode_profile_head` table;
- `conversation_mode_profile_revision` table;
- `conversation_mode_profile_temporary_binding` table;
- nullable indexed `chat.mode_profile_revision_id` foreign key;
- deterministic compatibility baseline rows;
- repository methods for current/public heads, private revisions, history, save-with-expected-head, restore-as-new-revision, persistent binding, temporary binding, binding transfer, integrity verification, and expired-binding cleanup.

Use row locking on PostgreSQL and the branch's established SQLite immediate-transaction pattern for conflicting head or binding claims.

Do not expose ORM objects directly to request handlers.

### B3. Strengthen Chat server-owned fields

Extend Chat persistence/response models so the backend may carry the revision reference without accepting it from ordinary `ChatForm` or import authority.

Add explicit invariant helpers for clone, share-copy, import, and generic update boundaries. Client JSON cannot mutate the server-owned reference.

### B4. Commit checkpoint

Suggested commit:

```text
feat(agent-mode): persist immutable mode profile revisions
```

## Phase C: Administrator and Public APIs

### C1. Add failing API tests

Cover:

- non-admin private reads and writes are rejected;
- admins can read current complete Chat and Agent profiles;
- admins can save a valid profile with an expected head;
- invalid Tool, Skill, Filter, or Terminal IDs fail validation;
- inactive resources fail validation;
- duplicate IDs and known conflicts fail validation;
- globally disabled but valid features return warnings rather than save failure;
- stale administrator saves return `409 mode_profile_revision_conflict`;
- history contains metadata and private content only for admins;
- restoring old content creates a new revision;
- the ordinary app config contains only sanitized defaults and revision IDs;
- the ordinary app config never contains Prompt, hash, creator, or history data;
- audit events contain IDs/counts only and never Prompt content.

Target files:

- `backend/open_webui/test/agent/test_conversation_mode_profile_routes.py`
- `backend/open_webui/routers/configs.py`
- `backend/open_webui/main.py`
- `backend/open_webui/constants.py` or the existing audit event registry when needed.

Focused command:

```bash
PYTHONPATH=backend /Users/liusihang/openwebui/.venv/bin/pytest -q \
  backend/open_webui/test/agent/test_conversation_mode_profile_routes.py
```

### C2. Implement thin typed endpoints

Add typed administrator operations under the existing config-router namespace for:

- current private profiles;
- save/create revision;
- revision history;
- historical detail;
- restore-as-new-revision.

Expose sanitized current profile metadata inside authenticated `/api/config` output.

Validation must call the domain/repository layer and existing Tool, Function/Filter, Skill, Terminal, global-feature, and permission truth surfaces rather than duplicating resource lookup logic.

### C3. Add precise multi-worker cache invalidation

Extend `backend/open_webui/utils/cache_invalidation.py` with a precise namespace for mode-profile heads.

- Cache current heads by mode.
- Cache immutable revisions by revision ID.
- After the transaction commits, publish invalidation only for the changed head.
- Apply the event locally and through Redis to every worker.
- Never invalidate or mutate historical revision content in response to a head change.

Add focused cache invalidation tests proving two simulated app instances converge on the new head while an old revision remains readable.

### C4. Commit checkpoint

Suggested commit:

```text
feat(admin): manage Chat and Agent mode profiles
```

## Phase D: Conversation Binding and Prompt Enforcement

### D1. Add failing chat-entry tests

Extend the focused chat-entry suites to prove:

- a new Chat conversation binds the current Chat revision before ordinary provider dispatch;
- a new Agent conversation binds the current Agent revision before Agent run creation/runtime dispatch;
- a stale public revision hint cannot select an old revision for a new conversation;
- an existing conversation continues using its bound revision after the head changes;
- request-supplied Prompt content cannot replace the administrator Prompt;
- request-supplied revision IDs cannot mutate a binding;
- revision mode mismatch and integrity failure stop before messages, runs, provider calls, or runtime calls are created;
- Prompt composition is administrator, model, then user for Chat and Agent;
- a compatibility baseline's explicit empty Prompt preserves prior behavior;
- current global model/default parameter precedence remains unchanged.

Target files:

- `backend/open_webui/test/agent/test_chat_entry_mode_profiles.py`
- `backend/open_webui/test/agent/test_chat_entry_agent_mode.py`
- `backend/open_webui/main.py`
- `backend/open_webui/models/chats.py`

Focused command:

```bash
PYTHONPATH=backend /Users/liusihang/openwebui/.venv/bin/pytest -q \
  backend/open_webui/test/agent/test_chat_entry_mode_profiles.py \
  backend/open_webui/test/agent/test_chat_entry_agent_mode.py
```

### D2. Implement one canonical resolution path

Before any product-chat write or dispatch:

1. resolve canonical conversation mode;
2. claim or load the canonical profile revision;
3. verify revision integrity and matching mode;
4. compose the enforced/model/user Prompt layers;
5. revalidate requested capabilities against permissions and live resources;
6. attach structured drift warnings;
7. continue through the existing ordinary Chat or Agent path.

Avoid independent lookups that can observe different mode/head/binding states. Reuse or deepen the existing atomic conversation-mode claim instead of introducing a competing lock order.

### D3. Add capability drift and malformed request tests

Prove:

- later resource deletion or permission loss skips only the affected item;
- structured warnings identify omitted capability categories without leaking private configuration;
- malformed IDs and Terminal/Code Interpreter conflicts submitted by clients return stable parameter errors;
- mode defaults do not grant access.

### D4. Commit checkpoint

Suggested commit:

```text
feat(chat): enforce bound mode profiles
```

## Phase E: Legacy, Temporary, Clone, Share, and Import Flows

### E1. Add failing lifecycle tests

Cover:

- pre-cutover explicit Chat binds the Chat baseline;
- pre-cutover explicit or inferred Agent binds the Agent baseline;
- a later administrator head change cannot affect an unbound legacy chat;
- two workers claiming one legacy chat converge on one baseline binding;
- temporary first request binds the current revision atomically;
- temporary follow-ups and save-to-history preserve the binding;
- concurrent temporary requests do not create conflicting bindings;
- expired temporary bindings are deleted once;
- local branch/clone and trusted share copy preserve source revision;
- share reads never expose Prompt content;
- export contains only mode and revision audit ID;
- import ignores revision authority and binds the current revision for the imported mode;
- generic updates, autosave, and drafts cannot mutate the server binding;
- regeneration and Agent resume preserve the revision.

Target files:

- `backend/open_webui/test/agent/test_conversation_mode_profile_lifecycle.py`
- existing chat router/model tests under `backend/open_webui/test/agent/`
- `backend/open_webui/models/chats.py`
- relevant chat routers and share/import handlers.

### E2. Implement lazy legacy binding

Use the migration cutover marker and baseline IDs. Any pre-cutover chat without a binding claims the appropriate baseline under the same transaction/lock used for canonical mode resolution.

Do not bulk rewrite all chat JSON or scan all Agent runs during deployment.

### E3. Implement temporary cleanup through the startup singleton

Add expired-binding cleanup to `_run_singleton_startup_tasks()` or its dedicated startup service. Test the repository cleanup separately and the singleton registration with a focused startup contract test.

No worker-local scheduler may run duplicate cleanup loops.

### E4. Commit checkpoint

Suggested commit:

```text
feat(chat): preserve mode profiles across conversation lifecycle
```

## Phase F: Frontend Types, APIs, and Administrator UI

### F1. Add failing frontend API/type tests

Create typed frontend contracts for:

- public sanitized profile defaults;
- private administrator profile content;
- tri-state values;
- revision history;
- structured validation warnings and conflict responses.

Target files:

- `src/lib/apis/configs/index.ts`
- `src/lib/types/index.ts` or the closest existing config type module;
- `src/lib/components/admin/Settings/Models/ConversationModeProfiles.test.ts`.

### F2. Add failing administrator presentation/compile tests

Prove the UI contains:

- Chat and Agent tabs;
- enforced System Prompt editor;
- tri-state Terminal, Tools, Skills, Filters, and feature controls;
- no model selector;
- no Reasoning Depth control;
- revision metadata and history;
- restore-as-new-revision behavior;
- warnings and blocking validation errors;
- stale-save conflict refresh behavior;
- no Prompt content in ordinary user stores or components.

Target files:

- `src/lib/components/admin/Settings/Models/ConversationModeProfiles.svelte`
- `src/lib/components/admin/Settings/Models/ConversationModeProfileEditor.svelte`
- `src/lib/components/admin/Settings/Models/ConversationModeProfiles.presentation.test.ts`
- `src/lib/components/admin/Settings/Models/ConversationModeProfiles.compile.test.ts`
- `src/lib/components/admin/Settings/Models.svelte`

### F3. Implement the administrator UI

Reuse existing selectors for Tools, Skills, Filters, Terminals, and features where their contracts support tri-state behavior. Add a small explicit inherit/override wrapper instead of encoding inheritance as an empty array.

Do not reuse `DEFAULT_MODEL_METADATA` as storage; it has model-creation semantics and cannot represent separate Chat/Agent revisions.

### F4. Commit checkpoint

Suggested commit:

```text
feat(admin): add Chat and Agent profile settings
```

## Phase G: Frontend Conversation Defaults and Model Changes

### G1. Extract and test a pure default resolver

Create a pure helper that accepts:

- conversation mode;
- sanitized current profile defaults;
- selected model metadata;
- current available resources and permissions;
- optional current user selections.

Prove:

- explicit profile values override model defaults;
- explicit empty values clear model defaults;
- omitted values inherit model defaults;
- unavailable or forbidden capabilities are omitted with warnings;
- mode profiles never select a model;
- Reasoning Depth remains unchanged;
- persistent user defaults do not override controlled mode defaults;
- switching models retains valid user selections and removes only invalid ones.

Target files:

- `src/lib/components/chat/conversationModeProfiles.ts`
- `src/lib/components/chat/conversationModeProfiles.test.ts`

### G2. Integrate new-chat initialization

Update `Chat.svelte` so mode-profile defaults apply exactly once when a new conversation/draft is initialized.

- Keep existing system default-model priority.
- Use the effective Agent leader model only for capability checks without destructively changing selected model state.
- Preserve the public revision hint in new/temporary draft state until the backend returns the canonical binding.
- Do not expose or represent the enforced System Prompt in frontend state.

### G3. Fix model-change and Terminal state leakage with failing tests

Strengthen tests before changing behavior:

- `resetInput()` cannot leave a Terminal selected from a prior conversation;
- switching models retains a valid Terminal and removes an invalid Terminal;
- switching modes by creating a new conversation initializes the new profile rather than reusing the previous global `selectedTerminalId`;
- model change does not reapply defaults or erase valid user-selected Tools/Skills/Filters/features;
- Terminal and Code Interpreter remain mutually exclusive.

Likely target files:

- `src/lib/components/chat/Chat.svelte`
- `src/lib/components/chat/MessageInput.svelte`
- `src/lib/components/chat/ConversationMode.presentation.test.ts`
- `src/lib/components/chat/ConversationMode.compile.test.ts`
- focused new source/pure tests where mounted component tests are not practical.

### G4. Draft, warning, and refresh behavior

- Store only public revision metadata and user-adjustable selections in drafts.
- On refresh, load the server-bound revision metadata for existing conversations.
- Display capability-drift warnings once.
- Keep Agent-unavailable behavior explicit and never fall back to Chat.

### G5. Commit checkpoint

Suggested commit:

```text
feat(chat): apply mode-specific capability defaults
```

## Phase H: Documentation, Regression, and Review

### H1. Documentation

Update or add:

- administrator configuration documentation;
- database/revision/binding contract;
- API request/response and error-code documentation;
- developer notes for Prompt composition and tri-state inheritance;
- operational notes for multi-worker invalidation and temporary-binding cleanup;
- TODOs only for explicitly deferred retention or UI enhancements.

Do not duplicate the approved design; document the implemented contract and operational procedures.

### H2. Focused backend regression

Run all new profile tests plus existing conversation-mode, Chat entry, Agent run, startup singleton, Config, Tool/Function cache, import/share, and migration suites.

Minimum focused command set:

```bash
PYTHONPATH=backend /Users/liusihang/openwebui/.venv/bin/pytest -q \
  backend/open_webui/test/agent/test_conversation_mode_profiles.py \
  backend/open_webui/test/agent/test_conversation_mode_profile_store.py \
  backend/open_webui/test/agent/test_conversation_mode_profile_routes.py \
  backend/open_webui/test/agent/test_chat_entry_mode_profiles.py \
  backend/open_webui/test/agent/test_conversation_mode_profile_lifecycle.py \
  backend/open_webui/test/agent/test_conversation_mode.py \
  backend/open_webui/test/agent/test_chat_entry_agent_mode.py \
  backend/open_webui/test/agent/test_chat_conversation_mode.py
```

Run migration tests with the required ephemeral test secret and verify `alembic upgrade head` and downgrade/upgrade on an isolated temporary database.

### H3. Focused frontend regression

Run the new administrator/profile/default tests plus existing conversation-mode, Agent transcript, history synchronization, default-feature, and terminal helper suites.

Then run the production frontend build with the repository-supported Node/npm environment. Record existing baseline warnings separately from feature failures.

### H4. Static checks

- Ruff and focused Python formatting/lint on new and touched backend files.
- Prettier on touched frontend/docs files.
- Svelte compiler tests for every changed component.
- `git diff --check` before every commit.
- Do not claim repository-wide `svelte-check` is clean; retain the known baseline unless it materially changes.

### H5. Independent review

Request a read-only Critical/Important review over:

- server authority and Prompt leakage;
- transaction/lock ordering;
- legacy and temporary binding races;
- import/clone/share trust boundaries;
- multi-worker invalidation;
- frontend one-time initialization and model-change retention;
- migration portability across SQLite and PostgreSQL.

Resolve every validated Critical/Important issue and rerun focused regressions.

### H6. Final implementation commit and checkpoint

Commit verified code and documentation without pushing. Report:

- commit IDs;
- exact test counts;
- migration verification;
- frontend build result;
- unresolved baseline warnings;
- remote image build still paused.

## Phase I: Paused Image Build and Isolated Four-Worker Acceptance

Do not start this phase until the user explicitly resumes the paused image-build checkpoint.

When resumed:

1. Re-read formal-live and isolated-stack container/image/health/restart anchors.
2. Build a clean source archive from the verified commit using the existing isolated build procedure.
3. Build/import only the candidate isolated WebUI image.
4. Recreate only `open-webui-pr7` with the exact recorded Compose chain and a reversible image/worker override.
5. Enable four workers only in the isolated stack.
6. Prove requests hit four distinct PIDs.
7. Change Chat and Agent heads independently and prove all workers converge for new conversations.
8. Prove pre-change conversations remain bound to historical revisions across every worker.
9. Prove startup singleton and temporary-binding cleanup run once without respawn loops.
10. Run real Chat/Agent SSE, Tool, Terminal, Skill, cancellation, approval/user-input, refresh recovery, knowledge, file, and non-destructive concurrency acceptance.
11. Report latency, error rate, CPU, memory, connections, and precise exception logs rather than HTTP status alone.
12. Restore the isolated stack to its original worker count/configuration and verify health/restart/image anchors.
13. Prove formal live remained unchanged before and after.
14. Issue a separate PR7/4-worker live go/no-go decision based only on the resulting evidence.

## Completion Criteria

Implementation is complete only when:

- all approved profile semantics are implemented and documented;
- new migrations are portable and verified;
- focused backend and frontend regressions pass;
- production frontend build passes;
- no enforced Prompt leaks through ordinary APIs, exports, shares, logs, or UI state;
- multi-worker invalidation has focused test evidence;
- independent Critical/Important review is clear;
- verified changes are committed without push;
- remote Docker image construction remains paused unless explicitly resumed;
- formal live has not been modified.
