# OpenWebUI Stateful Anchor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an explicit-anchor hybrid stateful mode for responses-capable providers so OpenWebUI can safely move toward incremental sending on linear chat paths while preserving replay fallback.

**Architecture:** Build this in phases. Phase 1 only observes and persists upstream `response.id` anchors without changing request shape. Phase 2 adds a narrow server-side experiment that uses `previous_response_id + latest user turn` only on safe linear paths. Phase 3 adds frontend-aware explicit incremental sending once branch invalidation and fallback semantics are proven.

**Tech Stack:** Svelte, TypeScript, FastAPI, SQLAlchemy, Python, pytest/vitest, OpenAI Responses-compatible routing, Open WebUI chat history model

---

### Task 1: Persist provider response anchors without changing send behavior

**Files:**
- Modify: `backend/open_webui/models/chat_messages.py`
- Modify: `backend/open_webui/utils/middleware.py`
- Modify: `backend/open_webui/routers/openai.py`
- Modify: `src/lib/components/chat/Chat.svelte`
- Modify: `src/lib/components/chat/Messages/messageSync.ts` (only if message metadata syncing needs update)
- Test: `backend/open_webui/test/util/test_chat_message_anchor_state.py`
- Test: `backend/open_webui/test/util/test_openai_responses_anchor_capture.py`
- Test: `src/lib/components/chat/statefulAnchorCapture.test.ts`

**Step 1: Write the failing backend persistence test**

Add a test that upserts an assistant message carrying provider anchor metadata and asserts the message round-trips with:
- `provider_response_id`
- `provider_route`
- `anchor_valid`

Example shape to validate:

```python
data = {
    "role": "assistant",
    "content": "PING",
    "provider_response_id": "resp_123",
    "provider_route": "responses",
    "anchor_valid": True,
}
```

**Step 2: Run test to verify it fails**

Run:

```bash
./.venv/bin/python -m pytest backend/open_webui/test/util/test_chat_message_anchor_state.py -q
```

Expected:
- FAIL because chat message persistence does not yet store anchor fields

**Step 3: Write the failing responses capture test**

Add a focused middleware/router test proving that when a Responses API completion finishes with an upstream `response.id`, the system exposes that value in the assistant message state that will later be saved.

The test should cover:
- non-stream responses
- stream completion (`response.completed`)

**Step 4: Run test to verify it fails**

Run:

```bash
./.venv/bin/python -m pytest backend/open_webui/test/util/test_openai_responses_anchor_capture.py -q
```

Expected:
- FAIL because anchor extraction/persistence is not wired through the completion pipeline yet

**Step 5: Write minimal backend implementation**

Implement the smallest possible support for persisted anchors:
- extend chat message persistence to store provider anchor metadata
- capture upstream `response.id` from Responses routing and middleware completion handling
- mark anchors as observational only in Phase 1
- do not change request send behavior yet

**Step 6: Write the failing frontend state test**

Add a Svelte/TS test proving the chat state can retain assistant message anchor metadata without breaking current history behavior.

Expected state shape:

```ts
{
  id: "...",
  role: "assistant",
  provider_response_id: "resp_123",
  provider_route: "responses",
  anchor_valid: true
}
```

**Step 7: Run targeted tests to verify green**

Run:

```bash
./.venv/bin/python -m pytest backend/open_webui/test/util/test_chat_message_anchor_state.py backend/open_webui/test/util/test_openai_responses_anchor_capture.py -q
npx vitest run src/lib/components/chat/statefulAnchorCapture.test.ts
```

Expected:
- all new anchor persistence/capture tests pass
- no send-path behavior has changed yet

**Step 8: Commit**

```bash
git add backend/open_webui/models/chat_messages.py backend/open_webui/utils/middleware.py backend/open_webui/routers/openai.py src/lib/components/chat/Chat.svelte src/lib/components/chat/Messages/messageSync.ts backend/open_webui/test/util/test_chat_message_anchor_state.py backend/open_webui/test/util/test_openai_responses_anchor_capture.py src/lib/components/chat/statefulAnchorCapture.test.ts
git commit -m "feat: persist responses anchors for chat state"
```

### Task 2: Add safe server-side shadow mode for linear-path incremental replay reduction

**Files:**
- Modify: `backend/open_webui/main.py`
- Modify: `backend/open_webui/utils/middleware.py`
- Modify: `backend/open_webui/routers/openai.py`
- Modify: `backend/open_webui/config.py` or `backend/open_webui/env.py`
- Modify: `backend/open_webui/utils/chat_context_budget.py` (only if diagnostics need to distinguish local request budget vs retained upstream state)
- Test: `backend/open_webui/test/util/test_stateful_anchor_shadow_mode.py`
- Test: `backend/open_webui/test/util/test_stateful_anchor_fallback.py`

**Step 1: Write the failing safe-path classifier test**

Add a test that defines a request as eligible for shadow mode only when all conditions hold:
- provider/route is Responses-capable
- persisted anchor exists and is valid
- request is a linear append on the current branch
- no edit/regenerate/continue-branch invalidation has happened
- no forced fallback/tool-recursion incompatible condition is present

**Step 2: Run test to verify it fails**

Run:

```bash
./.venv/bin/python -m pytest backend/open_webui/test/util/test_stateful_anchor_shadow_mode.py -q
```

Expected:
- FAIL because no explicit stateful-anchor eligibility logic exists yet

**Step 3: Write the failing fallback test**

Add a test proving the system falls back to full replay when:
- provider is not Responses-capable
- `responses -> chat/completions` fallback happens
- anchor is missing or invalid
- request is not a linear append
- tool recursion path requires replay semantics

**Step 4: Run test to verify it fails**

Run:

```bash
./.venv/bin/python -m pytest backend/open_webui/test/util/test_stateful_anchor_fallback.py -q
```

Expected:
- FAIL because fallback behavior is not centrally defined yet

**Step 5: Write minimal server-side shadow-mode implementation**

Implement a gated experiment that:
- is disabled by default behind a dedicated feature flag
- leaves frontend payloads unchanged
- detects safe linear append cases on the server
- rewrites the upstream request to use:
  - current system/instructions
  - latest user turn
  - `previous_response_id`
- immediately falls back to the existing full replay path on any unsafe condition

**Step 6: Run targeted verification**

Run:

```bash
./.venv/bin/python -m pytest backend/open_webui/test/util/test_stateful_anchor_shadow_mode.py backend/open_webui/test/util/test_stateful_anchor_fallback.py -q
```

Expected:
- safe-path shadow mode activates only in narrow eligible cases
- all invalidation/fallback tests pass

**Step 7: Commit**

```bash
git add backend/open_webui/main.py backend/open_webui/utils/middleware.py backend/open_webui/routers/openai.py backend/open_webui/config.py backend/open_webui/env.py backend/open_webui/utils/chat_context_budget.py backend/open_webui/test/util/test_stateful_anchor_shadow_mode.py backend/open_webui/test/util/test_stateful_anchor_fallback.py
git commit -m "feat: add shadow stateful anchor mode"
```

### Task 3: Add explicit frontend incremental send mode on top of proven anchor semantics

**Files:**
- Modify: `src/lib/components/chat/Chat.svelte`
- Modify: `src/lib/utils/index.ts`
- Modify: `src/lib/apis/openai/index.ts`
- Modify: `src/lib/components/chat/historySync.ts`
- Modify: `src/lib/components/chat/Messages/messageSync.ts`
- Test: `src/lib/components/chat/statefulAnchorSendPath.test.ts`
- Test: `src/lib/components/chat/statefulAnchorInvalidation.test.ts`
- Test: `backend/open_webui/test/util/test_stateful_anchor_end_to_end_contract.py`

**Step 1: Write the failing frontend send-path test**

Add a test proving that when the current branch is eligible and an anchor exists, the frontend sends a slim payload containing:
- the latest user turn
- `previous_response_id`
- enough route/provider metadata for the backend to honor or reject the request

It must also prove that the full replay payload is still used when the branch is not eligible.

**Step 2: Run test to verify it fails**

Run:

```bash
npx vitest run src/lib/components/chat/statefulAnchorSendPath.test.ts
```

Expected:
- FAIL because frontend currently always derives full payloads from `createMessagesList(...)`

**Step 3: Write the failing invalidation test**

Add a test proving anchors are invalidated when:
- the user edits a historical message
- regenerate creates a sibling branch
- continue response changes branch lineage
- model or route changes

**Step 4: Run test to verify it fails**

Run:

```bash
npx vitest run src/lib/components/chat/statefulAnchorInvalidation.test.ts
```

Expected:
- FAIL because no explicit anchor invalidation rules exist in frontend state

**Step 5: Write minimal frontend implementation**

Implement:
- explicit anchor-aware send-path selection
- branch lineage invalidation logic
- compatibility with existing replay mode and history rendering

Do not remove `createMessagesList(...)`; keep it for UI/history/export needs and for replay fallback.

**Step 6: Add end-to-end contract test**

Write a backend or integration-style contract test that verifies:
- linear follow-up uses `previous_response_id`
- branch/edit/regenerate forces replay fallback
- tool recursion remains replay-safe

**Step 7: Run targeted verification**

Run:

```bash
npx vitest run src/lib/components/chat/statefulAnchorSendPath.test.ts src/lib/components/chat/statefulAnchorInvalidation.test.ts
./.venv/bin/python -m pytest backend/open_webui/test/util/test_stateful_anchor_end_to_end_contract.py -q
```

Expected:
- frontend and backend agree on anchor eligibility and fallback behavior

**Step 8: Commit**

```bash
git add src/lib/components/chat/Chat.svelte src/lib/utils/index.ts src/lib/apis/openai/index.ts src/lib/components/chat/historySync.ts src/lib/components/chat/Messages/messageSync.ts src/lib/components/chat/statefulAnchorSendPath.test.ts src/lib/components/chat/statefulAnchorInvalidation.test.ts backend/open_webui/test/util/test_stateful_anchor_end_to_end_contract.py
git commit -m "feat: add explicit frontend stateful anchor mode"
```

### Task 4: Rollout and rollback controls

**Files:**
- Modify: `backend/open_webui/env.py`
- Modify: `backend/open_webui/config.py`
- Modify: `src/lib/components/admin/Settings/General.svelte` (only if exposing admin UI control is desired)
- Modify: `docs/plans/2026-03-25-openwebui-chat-performance-handoff.md`

**Step 1: Add rollout flags**

Define phased flags such as:
- `ENABLE_RESPONSES_ANCHOR_CAPTURE`
- `ENABLE_RESPONSES_ANCHOR_SHADOW_MODE`
- `ENABLE_RESPONSES_ANCHOR_FRONTEND_MODE`

Phase defaults:
- capture: on only in canary/testing
- shadow mode: off by default
- frontend mode: off by default

**Step 2: Document rollback**

Rollback must be a config-only fallback:
- disable shadow/frontend flags
- ignore persisted anchors
- retain DB history and messages unchanged

**Step 3: Add handoff checkpoint**

Record:
- which phase was implemented
- which flags were enabled
- what verification was run
- what fallback behavior remains

**Step 4: Commit**

```bash
git add backend/open_webui/env.py backend/open_webui/config.py src/lib/components/admin/Settings/General.svelte docs/plans/2026-03-25-openwebui-chat-performance-handoff.md
git commit -m "chore: add rollout controls for stateful anchors"
```

### Data Model Recommendation

Store the minimum anchor metadata on assistant messages:

```json
{
  "provider_response_id": "resp_...",
  "provider_route": "responses",
  "anchor_valid": true,
  "anchor_model_id": "bifrostapi.ZenMuxOAI/openai/gpt-5.4-mini"
}
```

Notes:
- Phase 1 can store this directly on `chat_message` rows as new nullable columns or a single JSON metadata column.
- If schema churn must stay low, prefer one JSON field first, but document the exact keys and treat it as stable schema.
- The stored anchor should belong to assistant turns only.

### Anchor Invalidation Rules

Invalidate anchor immediately when:
- a historical user or assistant message is edited
- regenerate creates a sibling branch
- continue response changes lineage
- model changes
- route changes away from Responses-capable path
- provider fallback from `responses` to `chat/completions` occurs
- tool recursion path requires replay semantics

Keep anchor valid when:
- the user adds a new message directly on the current linear branch
- the model and route are unchanged
- the provider remains Responses-capable and stateful

### Phase 1 Blockers

Do not start Phase 2 until all of the following are resolved:
- upstream `response.id` can be captured in both stream and non-stream paths
- persisted anchors survive reload and chat reopen
- a clear source of truth exists for whether an anchor is valid on the current branch
- fallback to full replay is proven on unsupported providers
