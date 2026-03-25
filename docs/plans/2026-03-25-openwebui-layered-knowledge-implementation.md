# OpenWebUI Layered Knowledge Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add layered knowledge retrieval to Open WebUI using Open Notebook as a shared insight-generation backend for `abstract`, `key_findings`, and `key_data`, while preserving existing `full_text` retrieval.

**Architecture:** Keep Open WebUI as the permission-owning multi-user system and add a sidecar per-file layer store plus new builtin retrieval tools. Use a thin internal integration module to call Open Notebook insight APIs and sync results into Open WebUI-owned tables and UI.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, existing Open WebUI builtin tools, Svelte, TypeScript, Vitest, pytest.

---

### Task 1: Add the database model for file layers

**Files:**
- Create: `backend/open_webui/models/knowledge_layers.py`
- Modify: `backend/open_webui/models/__init__.py` if needed by local import style
- Create: `backend/open_webui/migrations/versions/<timestamp>_add_knowledge_file_layer_table.py`
- Test: `backend/open_webui/test/apps/webui/routers/test_knowledge_layers_model.py`

**Step 1: Write the failing test**

Add a model-level test that asserts:

- a `knowledge_file_layer` row can be created with `knowledge_id`, `file_id`, `layer_type`, and `status`
- duplicate `(knowledge_id, file_id, layer_type)` rows are rejected

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest backend/open_webui/test/apps/webui/routers/test_knowledge_layers_model.py -q`

Expected: FAIL because the model/table does not exist.

**Step 3: Write minimal implementation**

Implement:

- SQLAlchemy table `knowledge_file_layer`
- Pydantic response/form models needed for router/service use
- uniqueness constraint on `(knowledge_id, file_id, layer_type)`
- Alembic migration creating the table and indexes

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest backend/open_webui/test/apps/webui/routers/test_knowledge_layers_model.py -q`

Expected: PASS

**Step 5: Commit**

```bash
git add backend/open_webui/models/knowledge_layers.py backend/open_webui/migrations/versions/<timestamp>_add_knowledge_file_layer_table.py backend/open_webui/test/apps/webui/routers/test_knowledge_layers_model.py
git commit -m "feat: add knowledge file layer model"
```

### Task 2: Add the Open Notebook integration module

**Files:**
- Create: `backend/open_webui/utils/layered_knowledge.py`
- Modify: `backend/open_webui/config.py`
- Modify: `backend/open_webui/main.py`
- Test: `backend/open_webui/test/util/test_layered_knowledge.py`

**Step 1: Write the failing test**

Add utility tests for:

- building authenticated requests to Open Notebook
- mapping configured transformation IDs by layer type
- normalizing Open Notebook insight payloads into local layer payloads

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest backend/open_webui/test/util/test_layered_knowledge.py -q`

Expected: FAIL because the integration module/config does not exist.

**Step 3: Write minimal implementation**

Implement:

- config entries for Open Notebook base URL, password, timeout, and transformation IDs
- helper functions for `abstract`, `key_findings`, `key_data`
- sync helper that fetches/normalizes Open Notebook insights into local rows

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest backend/open_webui/test/util/test_layered_knowledge.py -q`

Expected: PASS

**Step 5: Commit**

```bash
git add backend/open_webui/utils/layered_knowledge.py backend/open_webui/config.py backend/open_webui/main.py backend/open_webui/test/util/test_layered_knowledge.py
git commit -m "feat: add open notebook layered knowledge integration"
```

### Task 3: Add layer management endpoints to the knowledge API

**Files:**
- Modify: `backend/open_webui/routers/knowledge.py`
- Modify: `backend/open_webui/models/knowledge_layers.py`
- Test: `backend/open_webui/test/apps/webui/routers/test_knowledge_layers_router.py`

**Step 1: Write the failing test**

Add router tests for endpoints such as:

- `GET /api/v1/knowledge/{id}/file/{file_id}/layers`
- `POST /api/v1/knowledge/{id}/file/{file_id}/layers/regenerate`
- optional `POST /api/v1/knowledge/{id}/file/{file_id}/layers/regenerate/{layer_type}`

Validate:

- permission checks follow existing knowledge/file access
- response payload includes layer status and content

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest backend/open_webui/test/apps/webui/routers/test_knowledge_layers_router.py -q`

Expected: FAIL because endpoints do not exist.

**Step 3: Write minimal implementation**

Add router handlers that:

- read local layer rows
- trigger background regeneration through the integration helper
- return stable response models for the frontend

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest backend/open_webui/test/apps/webui/routers/test_knowledge_layers_router.py -q`

Expected: PASS

**Step 5: Commit**

```bash
git add backend/open_webui/routers/knowledge.py backend/open_webui/models/knowledge_layers.py backend/open_webui/test/apps/webui/routers/test_knowledge_layers_router.py
git commit -m "feat: add knowledge layer management endpoints"
```

### Task 4: Generate and refresh layers during knowledge file lifecycle

**Files:**
- Modify: `backend/open_webui/routers/knowledge.py`
- Modify: `backend/open_webui/routers/retrieval.py` only if needed for existing file processing hooks
- Modify: `backend/open_webui/utils/layered_knowledge.py`
- Test: `backend/open_webui/test/apps/webui/routers/test_knowledge_layer_sync.py`

**Step 1: Write the failing test**

Add lifecycle tests that assert:

- adding a file to a knowledge base schedules layer generation
- updating/resetting a file can mark layers stale or refresh them
- failures do not block the file from remaining usable in the knowledge base

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest backend/open_webui/test/apps/webui/routers/test_knowledge_layer_sync.py -q`

Expected: FAIL

**Step 3: Write minimal implementation**

Wire layer sync into the existing knowledge-file lifecycle:

- after file association and successful ingestion
- on explicit user-triggered refresh
- on knowledge reset where applicable

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest backend/open_webui/test/apps/webui/routers/test_knowledge_layer_sync.py -q`

Expected: PASS

**Step 5: Commit**

```bash
git add backend/open_webui/routers/knowledge.py backend/open_webui/routers/retrieval.py backend/open_webui/utils/layered_knowledge.py backend/open_webui/test/apps/webui/routers/test_knowledge_layer_sync.py
git commit -m "feat: sync layered knowledge with file lifecycle"
```

### Task 5: Add layered builtin retrieval tools

**Files:**
- Modify: `backend/open_webui/tools/builtin.py`
- Modify: `backend/open_webui/utils/tools.py`
- Test: `backend/open_webui/test/util/test_attached_knowledge_tool_resolution.py`
- Test: `backend/open_webui/test/util/test_attached_knowledge_native_flow.py`
- Test: `backend/open_webui/test/util/test_layered_knowledge_tools.py`

**Step 1: Write the failing test**

Add tests for:

- new scoped tool names are exposed when effective knowledge scope exists
- `query_knowledge_abstract`
- `query_knowledge_key_findings`
- `query_knowledge_key_data`
- `query_knowledge_full_text`
- `view_knowledge_layers`

Also verify the old scoped `query_knowledge_files` behavior is replaced or intentionally aliased.

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest backend/open_webui/test/util/test_attached_knowledge_tool_resolution.py backend/open_webui/test/util/test_attached_knowledge_native_flow.py backend/open_webui/test/util/test_layered_knowledge_tools.py -q`

Expected: FAIL

**Step 3: Write minimal implementation**

Implement all layered builtin tool functions and update tool-resolution logic in:

- `backend/open_webui/tools/builtin.py`
- `backend/open_webui/utils/tools.py`

Write explicit docstrings that guide model tool choice by query type.

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest backend/open_webui/test/util/test_attached_knowledge_tool_resolution.py backend/open_webui/test/util/test_attached_knowledge_native_flow.py backend/open_webui/test/util/test_layered_knowledge_tools.py -q`

Expected: PASS

**Step 5: Commit**

```bash
git add backend/open_webui/tools/builtin.py backend/open_webui/utils/tools.py backend/open_webui/test/util/test_attached_knowledge_tool_resolution.py backend/open_webui/test/util/test_attached_knowledge_native_flow.py backend/open_webui/test/util/test_layered_knowledge_tools.py
git commit -m "feat: add layered knowledge builtin tools"
```

### Task 6: Add frontend API bindings for layers

**Files:**
- Modify: `src/lib/apis/knowledge/index.ts`
- Test: `src/lib/apis/knowledge/index.test.ts` or colocated tests if the repo pattern prefers a different test path

**Step 1: Write the failing test**

Add API client tests for:

- fetching file layers
- triggering regenerate-all
- triggering regenerate-single-layer

**Step 2: Run test to verify it fails**

Run: `npx vitest run src/lib/apis/knowledge/index.test.ts`

Expected: FAIL because the client helpers do not exist.

**Step 3: Write minimal implementation**

Add typed API functions for the new layer endpoints in `src/lib/apis/knowledge/index.ts`.

**Step 4: Run test to verify it passes**

Run: `npx vitest run src/lib/apis/knowledge/index.test.ts`

Expected: PASS

**Step 5: Commit**

```bash
git add src/lib/apis/knowledge/index.ts src/lib/apis/knowledge/index.test.ts
git commit -m "feat: add knowledge layer frontend api bindings"
```

### Task 7: Add layer management UI to knowledge files

**Files:**
- Modify: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
- Modify: `src/lib/components/workspace/Knowledge/KnowledgeBase/Files.svelte`
- Create: `src/lib/components/workspace/Knowledge/KnowledgeBase/LayersPanel.svelte`
- Test: `src/lib/components/workspace/Knowledge/KnowledgeBase/LayersPanel.test.ts`

**Step 1: Write the failing test**

Add UI tests for:

- rendering `Abstract`, `Key Findings`, `Key Data`
- showing `pending`, `ready`, `failed`
- invoking regenerate actions

**Step 2: Run test to verify it fails**

Run: `npx vitest run src/lib/components/workspace/Knowledge/KnowledgeBase/LayersPanel.test.ts`

Expected: FAIL

**Step 3: Write minimal implementation**

Implement a reusable `LayersPanel` and mount it into the existing knowledge detail view.

Keep the UI minimal:

- display layer contents
- display statuses
- display updated time
- provide regenerate buttons

**Step 4: Run test to verify it passes**

Run: `npx vitest run src/lib/components/workspace/Knowledge/KnowledgeBase/LayersPanel.test.ts`

Expected: PASS

**Step 5: Commit**

```bash
git add src/lib/components/workspace/Knowledge/KnowledgeBase.svelte src/lib/components/workspace/Knowledge/KnowledgeBase/Files.svelte src/lib/components/workspace/Knowledge/KnowledgeBase/LayersPanel.svelte src/lib/components/workspace/Knowledge/KnowledgeBase/LayersPanel.test.ts
git commit -m "feat: add knowledge layer management ui"
```

### Task 8: Add layer-prefixed chat source rendering

**Files:**
- Modify: `backend/open_webui/tools/builtin.py`
- Modify: chat source formatting code discovered during implementation, likely under `src/lib/components/chat/` and existing source/citation helpers
- Test: `backend/open_webui/test/util/test_layered_knowledge_tools.py`
- Test: relevant frontend source rendering test file if needed

**Step 1: Write the failing test**

Add tests verifying source labels are prefixed as:

- `Abstract: ...`
- `Key Findings: ...`
- `Key Data: ...`
- `Full Text: ...`

**Step 2: Run test to verify it fails**

Run: targeted pytest/vitest commands for the touched files.

Expected: FAIL

**Step 3: Write minimal implementation**

Ensure tool results include a stable `layer` field and that rendered source labels show the layer prefix.

**Step 4: Run test to verify it passes**

Run: targeted pytest/vitest commands for the touched files.

Expected: PASS

**Step 5: Commit**

```bash
git add backend/open_webui/tools/builtin.py src/lib/components/chat/
git commit -m "feat: add layer tags to chat knowledge sources"
```

### Task 9: End-to-end verification

**Files:**
- Modify only if verification exposes a real defect in the above changes

**Step 1: Run backend targeted suite**

Run:

```bash
./.venv/bin/python -m pytest \
  backend/open_webui/test/apps/webui/routers/test_knowledge_layers_model.py \
  backend/open_webui/test/apps/webui/routers/test_knowledge_layers_router.py \
  backend/open_webui/test/apps/webui/routers/test_knowledge_layer_sync.py \
  backend/open_webui/test/util/test_layered_knowledge.py \
  backend/open_webui/test/util/test_layered_knowledge_tools.py \
  backend/open_webui/test/util/test_attached_knowledge_tool_resolution.py \
  backend/open_webui/test/util/test_attached_knowledge_native_flow.py -q
```

Expected: PASS

**Step 2: Run frontend targeted suite**

Run:

```bash
npx vitest run \
  src/lib/apis/knowledge/index.test.ts \
  src/lib/components/workspace/Knowledge/KnowledgeBase/LayersPanel.test.ts
```

Expected: PASS

**Step 3: Manual smoke checklist**

Verify:

- create or open a knowledge base
- add a file
- wait for `abstract`, `key_findings`, `key_data`
- attach that knowledge in chat
- confirm the model can call the layered tools
- confirm source labels include layer prefixes

**Step 4: Commit**

```bash
git add .
git commit -m "test: verify layered knowledge integration"
```

### Task 10: Documentation update

**Files:**
- Modify: `docs/plans/2026-03-25-openwebui-layered-knowledge-design.md`
- Modify: user-facing or developer-facing knowledge docs if implementation introduces operator-facing setup requirements

**Step 1: Update operator documentation**

Document:

- Open Notebook service configuration
- transformation ID requirements
- regeneration behavior
- shared-service deployment assumption

**Step 2: Verify docs are accurate**

Check all commands, env vars, and file paths against implementation.

**Step 3: Commit**

```bash
git add docs/plans/2026-03-25-openwebui-layered-knowledge-design.md
git commit -m "docs: document layered knowledge integration"
```

Plan complete and saved to `docs/plans/2026-03-25-openwebui-layered-knowledge-implementation.md`. Two execution options:

**1. Subagent-Driven (this session)** - I dispatch fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** - Open new session with executing-plans, batch execution with checkpoints

Which approach?
