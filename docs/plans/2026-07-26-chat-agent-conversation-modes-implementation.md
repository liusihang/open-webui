# Chat and Agent Conversation Modes Implementation Plan

Design: `docs/plans/2026-07-26-chat-agent-conversation-modes-design.md`

Truth surface:

- Worktree: `/Users/liusihang/.codex/worktrees/d790/openwebui`
- Branch: `codex/pr7-chat-agent-dual-mode-20260726`
- Design commit: `39851fe0d`
- Base: `codex/pr7-live-compatible-20260722` at `eb072df904d488b6ca9d6b9fe4eb2a4d0b462c5e`
- Formal live remains read-only and out of scope.

## Execution Rules

1. Use test-driven development for every behavior change: add a failing focused test, confirm the expected failure, implement the smallest complete fix, and rerun the focused test.
2. Keep the ordinary Chat path and Agent path separate. Do not add fallback routing that hides a mode or runtime failure.
3. Treat persisted mode as server-authoritative; frontend state is presentation and request intent only.
4. Preserve unrelated worktree and remote state.
5. Update the handoff files after every checkpoint.
6. Commit verified backend and frontend slices promptly; do not push without explicit authorization.

## Phase A: Backend Domain Contract

### A1. Add failing pure mode-contract tests

Create focused tests for a small conversation-mode module:

- valid values normalize to `chat` and `agent`;
- missing new-chat mode resolves to `chat`;
- invalid values raise a typed invalid-mode error;
- explicit persisted mode wins;
- legacy Agent evidence resolves to `agent`;
- legacy chat without Agent evidence resolves to `chat`;
- requested/persisted mismatch raises a typed conflict.

Target files:

- `backend/open_webui/test/agent/test_conversation_mode.py`
- `backend/open_webui/agent/conversation_mode.py`

Red command:

```bash
PYTHONPATH=backend /Users/liusihang/openwebui/.venv/bin/pytest -q \
  backend/open_webui/test/agent/test_conversation_mode.py
```

This Codex worktree does not contain its own virtualenv, so task execution reuses the root checkout's `.venv` while pointing `PYTHONPATH` at the feature worktree.

### A2. Implement the pure contract

Implement a narrow module containing:

- `ConversationMode` string enum or literal constants;
- normalization and validation;
- legacy message evidence detection;
- canonical resolution from requested, persisted, and legacy evidence;
- stable error types/codes.

Keep database and FastAPI dependencies outside the pure functions.

### A3. Add efficient legacy Agent-run evidence

Add an `AgentRuns.has_runs_by_chat(chat_id, user_id)` existence query rather than loading full run rows. Cover it with the existing Agent run DB-store tests.

## Phase B: Backend Product-Chat Routing

### B1. Strengthen chat-entry tests first

Extend `backend/open_webui/test/agent/test_chat_entry_agent_mode.py` with failing tests proving:

- global Agent capability plus `chat_mode=chat` uses the ordinary path;
- a new request without `chat_mode` defaults to Chat;
- `chat_mode=agent` creates an Agent chat and Agent run;
- Agent mode is rejected explicitly when deployment capability is disabled;
- an existing stored Chat cannot be requested as Agent;
- an existing stored Agent cannot be requested as Chat;
- omitted mode on an existing chat uses its stored mode;
- a legacy chat is inferred and then persisted;
- rejected mismatches add neither user messages nor Agent runs;
- internal Agent model calls still bypass product routing.

### B2. Resolve mode before persistence and routing

Refactor `backend/open_webui/main.py` so it:

1. removes `chat_mode` from provider-bound form data;
2. verifies ownership and loads an existing chat before accepting a follow-up;
3. resolves canonical mode once;
4. validates Agent capability before creating a new Agent chat;
5. stores `mode` in new chat JSON;
6. persists an inferred legacy mode during the existing-chat write;
7. places canonical mode in internal metadata;
8. routes to Agent only when canonical mode is `agent`;
9. applies single-leader model selection only to Agent requests.

Do not infer Agent mode from `ENABLE_AGENT_MODE` alone.

### B3. Preserve explicit Agent failures

Map mode failures to stable HTTP responses:

- `400 invalid_conversation_mode`;
- `403 agent_mode_forbidden` when a permission gate exists;
- `409 conversation_mode_mismatch`;
- `503 agent_mode_disabled`;
- preserve or normalize current runtime-dispatch failure as `503 agent_runtime_unavailable` without Chat fallback.

## Phase C: Storage, Import, Clone, and Share Invariants

### C1. Add failing router/model tests

Cover:

- generic update cannot change explicit mode;
- generic update may omit mode;
- import accepts `chat`, `agent`, or missing mode and rejects other values;
- clone preserves mode;
- shared clone preserves mode;
- read-only responses expose mode;
- a legacy mode is not accidentally overwritten by frontend payload fields.

Likely target tests:

- existing chat router/model test modules under `backend/open_webui/test/`;
- a new focused `test_chat_conversation_mode.py` if existing fixtures are too broad.

### C2. Implement invariant checks

Use the same conversation-mode module in chat router/model boundaries. Avoid duplicate string validation.

## Phase D: Frontend Request and State Contract

### D1. Refactor helper tests first

Extend or replace tests around `src/lib/components/chat/agentModeRequest.ts`:

- Agent capability discovery remains global;
- model narrowing occurs only for `conversationMode === 'agent'`;
- Chat preserves multiple selected models even when Agent capability is enabled;
- first request payload carries the selected mode;
- Chat does not receive Agent-only reasoning shaping.

Rename the helper if needed so it describes conversation mode rather than a global request constraint.

### D2. Add typed Chat state

In `Chat.svelte`:

- add `conversationMode: 'chat' | 'agent'`;
- initialize a new chat to `chat` or an explicit new-chat navigation preselection;
- load existing mode from the server response;
- infer only for display when an older server response lacks mode;
- save mode in draft state only before chat creation;
- include `chat_mode` in completion requests;
- pass mode to model-selection and MessageInput behavior;
- reset it only when a new conversation is initialized.

The server remains authoritative after a chat exists.

## Phase E: Top Mode Selector

### E1. Add component tests first

Create a small component and focused tests for:

- two visible options: Chat and Agent;
- new-chat editable state;
- existing-chat locked state;
- disabled Agent capability state and explanation;
- read-only display state;
- keyboard and ARIA radiogroup behavior;
- dispatching a new-conversation request instead of mutating a locked conversation.

Target files:

- `src/lib/components/chat/ConversationModeSelector.svelte`
- `src/lib/components/chat/ConversationModeSelector.test.ts`

### E2. Integrate into the sticky navbar

Update `Navbar.svelte` and `Chat.svelte`:

- center the selector independently of the left model selector and right actions;
- preserve usable mobile layout;
- use the existing confirmation-dialog pattern;
- confirmation opens a new conversation with the selected mode preselected;
- existing and shared chats never mutate their mode.

### E3. Agent-only composer behavior

- Show Agent reasoning-depth controls only in Agent conversations.
- Keep ordinary integrations available according to existing Chat capabilities.
- Apply Agent single-model constraints only in Agent conversations.
- If an Agent conversation is readable but currently unavailable, disable submission with an explicit reason.

## Phase F: Focused Verification

Backend focused suites:

```bash
PYTHONPATH=backend /Users/liusihang/openwebui/.venv/bin/pytest -q \
  backend/open_webui/test/agent/test_conversation_mode.py \
  backend/open_webui/test/agent/test_chat_entry_agent_mode.py \
  backend/open_webui/test/agent/test_agent_run_routes_db_store.py \
  <chat-router-mode-tests>
```

Frontend focused suites should run with the repository-supported Node and pnpm versions and include:

- conversation-mode helper tests;
- selector component tests;
- Chat request/presentation tests;
- Agent transcript/history-sync regression tests.

Also run:

```bash
git diff --check
```

## Phase G: Regression and Isolated Acceptance

1. Run the relevant backend Agent, chat, config, and storage regression superset.
2. Run the AgentScope runtime suite if backend routing payloads changed.
3. Run the relevant frontend Chat/Agent suite and compile/type checks.
4. Build a new candidate image from the exact feature-branch commit.
5. Deploy only to the isolated PR7 stack with a prepared rollback method.
6. Prove ordinary Chat and Agent conversations take different real runtime paths.
7. Prove refresh/continue/regenerate/clone compatibility.
8. Prove Agent runtime failure does not affect Chat.
9. Re-run representative acceptance with four WebUI workers and prove consistent mode routing across worker PIDs.
10. Restore the isolated stack to its starting configuration and record formal-live before/after read-only anchors.

## Commit Boundaries

1. `test(agent-mode): define immutable conversation mode contract`
2. `feat(agent-mode): route chats by persisted conversation mode`
3. `feat(chat): preserve conversation mode across storage flows`
4. `feat(chat): add immutable Chat and Agent selector`
5. `test(agent-mode): accept dual Chat and Agent runtime paths`

Exact boundaries may be combined when a test and minimal implementation are inseparable, but each commit must be independently verified.

## Completion Criteria

- All approved design requirements are implemented.
- Focused and regression suites pass with fresh evidence.
- A real isolated deployment proves separate Chat and Agent paths.
- Four-worker acceptance proves consistent persisted mode and route choice.
- Isolated stack is restored.
- Formal live remains unchanged.
- Verified changes are committed locally and not pushed without authorization.
