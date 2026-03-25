# OpenWebUI Layered Knowledge Source Sync Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make layered knowledge generation production-usable by adding Open WebUI file ↔ Open Notebook source mapping, manual selective regeneration, large-file chunked insight generation, and backfill support for existing knowledge-base files.

**Architecture:** Keep Open WebUI as the user-facing system of record, but stop assuming `file_id == source_id`. Add a durable source-mapping layer in Open WebUI, create Open Notebook sources on demand, and generate either single-file or chunked insights depending on token size. Expose file-level selective regenerate actions and knowledge-level backfill actions through the existing knowledge APIs and UI.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, requests, existing Open WebUI knowledge/file models, Svelte, TypeScript, Vitest, pytest, `tiktoken`.

---

### Task 1: Extend the layer data model for chunked outputs

**Files:**
- Modify: `backend/open_webui/models/knowledge_layers.py`
- Modify: `backend/open_webui/migrations/versions/c3d4e5f6a7b8_add_knowledge_file_layer_table.py`
- Create: `backend/open_webui/migrations/versions/<timestamp>_update_knowledge_file_layer_for_chunking.py`
- Test: `backend/open_webui/test/apps/webui/routers/test_knowledge_layers_model.py`

**Step 1: Write the failing test**

Extend the model test to assert:

- a file can store multiple rows for the same `layer_type` when `part_index` differs
- the uniqueness rule is now `(knowledge_id, file_id, layer_type, part_index)`
- rows expose `part_index`, `part_total`, and `display_title`

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest backend/open_webui/test/apps/webui/routers/test_knowledge_layers_model.py -q`

Expected: FAIL because chunk metadata fields and uniqueness behavior do not exist yet.

**Step 3: Write minimal implementation**

Update `knowledge_file_layer` to include:

- `part_index`
- `part_total`
- `display_title`

Update model/query helpers so chunk rows are sorted deterministically by:

- `layer_type`
- `part_index`
- `updated_at`

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest backend/open_webui/test/apps/webui/routers/test_knowledge_layers_model.py -q`

Expected: PASS

**Step 5: Commit**

```bash
git add backend/open_webui/models/knowledge_layers.py backend/open_webui/migrations/versions/*knowledge_file_layer* backend/open_webui/test/apps/webui/routers/test_knowledge_layers_model.py
git commit -m "feat: support chunked knowledge layer rows"
```

### Task 2: Add file ↔ Open Notebook source mapping storage

**Files:**
- Modify: `backend/open_webui/models/files.py`
- Modify: `backend/open_webui/utils/layered_knowledge.py`
- Test: `backend/open_webui/test/util/test_layered_knowledge.py`

**Step 1: Write the failing test**

Add tests for:

- reading `file.meta.open_notebook_source_id`
- reading `file.meta.open_notebook_source_ids`
- writing mapping info back into `file.meta`
- preserving unrelated file metadata keys

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest backend/open_webui/test/util/test_layered_knowledge.py -q`

Expected: FAIL because mapping read/write helpers do not exist.

**Step 3: Write minimal implementation**

Store mapping in `file.meta`:

- normal file: `open_notebook_source_id`
- chunked file: `open_notebook_source_ids`
- optional status fields:
  - `open_notebook_sync_status`
  - `open_notebook_last_synced_at`
  - `open_notebook_is_large_file`
  - `open_notebook_part_count`

Use file metadata instead of a new table to keep the implementation small and localized.

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest backend/open_webui/test/util/test_layered_knowledge.py -q`

Expected: PASS

**Step 5: Commit**

```bash
git add backend/open_webui/models/files.py backend/open_webui/utils/layered_knowledge.py backend/open_webui/test/util/test_layered_knowledge.py
git commit -m "feat: store open notebook source mapping in file metadata"
```

### Task 3: Create Open Notebook sources on demand instead of assuming matching IDs

**Files:**
- Modify: `backend/open_webui/utils/layered_knowledge.py`
- Test: `backend/open_webui/test/util/test_layered_knowledge.py`

**Step 1: Write the failing test**

Add tests asserting:

- when a file has no source mapping, the integration calls `POST /api/sources`
- the returned source ID is persisted into file metadata
- later regenerate/sync calls use mapped source IDs rather than `file_id`

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest backend/open_webui/test/util/test_layered_knowledge.py -q`

Expected: FAIL because current logic still calls `/api/sources/{file_id}/insights`.

**Step 3: Write minimal implementation**

Implement source creation for normal files:

- create Open Notebook `text` or `upload` source from Open WebUI file content
- use existing file title/filename as source title
- persist returned `source.id`
- call insights against the mapped source ID

Do not rely on Open Notebook generating an ID that matches Open WebUI.

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest backend/open_webui/test/util/test_layered_knowledge.py -q`

Expected: PASS

**Step 5: Commit**

```bash
git add backend/open_webui/utils/layered_knowledge.py backend/open_webui/test/util/test_layered_knowledge.py
git commit -m "feat: create and persist open notebook sources on demand"
```

### Task 4: Add token estimation and chunk planning for large files

**Files:**
- Modify: `backend/open_webui/utils/layered_knowledge.py`
- Test: `backend/open_webui/test/util/test_layered_knowledge_chunking.py`

**Step 1: Write the failing test**

Create chunking tests for:

- token estimation using `tiktoken`
- files at or under `24000` tokens stay single-source
- files over `24000` tokens split into chunks
- tail chunk `<1000` tokens is dropped
- chunk boundaries preserve paragraph grouping where possible

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest backend/open_webui/test/util/test_layered_knowledge_chunking.py -q`

Expected: FAIL because chunk planning helpers do not exist.

**Step 3: Write minimal implementation**

Implement:

- token counting with `tiktoken`
- chunk planning with:
  - max chunk size `24000 tokens`
  - drop trailing chunk `<1000 tokens`
- paragraph/block-aware accumulation before hard token slicing fallback

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest backend/open_webui/test/util/test_layered_knowledge_chunking.py -q`

Expected: PASS

**Step 5: Commit**

```bash
git add backend/open_webui/utils/layered_knowledge.py backend/open_webui/test/util/test_layered_knowledge_chunking.py
git commit -m "feat: add token-aware chunk planning for large knowledge files"
```

### Task 5: Support chunked source creation and chunked insight rows

**Files:**
- Modify: `backend/open_webui/utils/layered_knowledge.py`
- Modify: `backend/open_webui/models/knowledge_layers.py`
- Test: `backend/open_webui/test/util/test_layered_knowledge.py`
- Test: `backend/open_webui/test/util/test_layered_knowledge_chunking.py`

**Step 1: Write the failing test**

Add tests asserting:

- large files create multiple Open Notebook text sources
- each chunk source gets its own `source_ref_id`
- generated rows use:
  - `part_index`
  - `part_total`
  - `display_title` like `Abstract 1/3`
- layer queries return chunk rows as ordinary searchable items

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest backend/open_webui/test/util/test_layered_knowledge.py backend/open_webui/test/util/test_layered_knowledge_chunking.py -q`

Expected: FAIL because chunked source orchestration does not exist.

**Step 3: Write minimal implementation**

Implement chunked sync behavior:

- create one Open Notebook source per chunk
- store returned source IDs in `file.meta.open_notebook_source_ids`
- run selected transformations on each chunk source
- write one `knowledge_file_layer` row per generated chunk insight
- set `display_title` to:
  - `Abstract 1/3`
  - `Key Findings 2/4`
  - `Key Data 1/2`

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest backend/open_webui/test/util/test_layered_knowledge.py backend/open_webui/test/util/test_layered_knowledge_chunking.py -q`

Expected: PASS

**Step 5: Commit**

```bash
git add backend/open_webui/utils/layered_knowledge.py backend/open_webui/models/knowledge_layers.py backend/open_webui/test/util/test_layered_knowledge.py backend/open_webui/test/util/test_layered_knowledge_chunking.py
git commit -m "feat: generate chunked insights for large knowledge files"
```

### Task 6: Add selective regenerate inputs for file-level layer sync

**Files:**
- Modify: `backend/open_webui/routers/knowledge.py`
- Modify: `backend/open_webui/utils/layered_knowledge.py`
- Test: `backend/open_webui/test/apps/webui/routers/test_knowledge_layers_router.py`

**Step 1: Write the failing test**

Add router tests asserting:

- `POST /api/v1/knowledge/{id}/file/{file_id}/layers/regenerate` accepts explicit `layer_types`
- regenerating only `abstract` does not force `key_findings` / `key_data`
- invalid layer types return `400`

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest backend/open_webui/test/apps/webui/routers/test_knowledge_layers_router.py -q`

Expected: FAIL because current file-level regenerate only supports all-layers or a path-param single layer.

**Step 3: Write minimal implementation**

Update regenerate input schema to support:

- `layer_types: list[str]`
- `force: bool`

Keep the path-based single-layer endpoint for convenience, but route both paths through the same service helper.

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest backend/open_webui/test/apps/webui/routers/test_knowledge_layers_router.py -q`

Expected: PASS

**Step 5: Commit**

```bash
git add backend/open_webui/routers/knowledge.py backend/open_webui/utils/layered_knowledge.py backend/open_webui/test/apps/webui/routers/test_knowledge_layers_router.py
git commit -m "feat: allow selective layer regeneration for a file"
```

### Task 7: Add knowledge-level backfill for existing files

**Files:**
- Modify: `backend/open_webui/routers/knowledge.py`
- Modify: `backend/open_webui/utils/layered_knowledge.py`
- Test: `backend/open_webui/test/apps/webui/routers/test_knowledge_layer_backfill.py`

**Step 1: Write the failing test**

Add tests for:

- `POST /api/v1/knowledge/{id}/layers/backfill`
- only files with missing/failed/stale rows are selected by default
- `force=true` reprocesses all files
- selective `layer_types` work for backfill

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest backend/open_webui/test/apps/webui/routers/test_knowledge_layer_backfill.py -q`

Expected: FAIL because the endpoint and helper do not exist.

**Step 3: Write minimal implementation**

Implement a knowledge-level backfill helper that:

- enumerates files in the knowledge base
- decides eligibility based on:
  - missing
  - failed
  - stale
  - force
- triggers the same sync path used by new files

Return a simple summary payload:

- `total_files`
- `scheduled_files`
- `skipped_files`

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest backend/open_webui/test/apps/webui/routers/test_knowledge_layer_backfill.py -q`

Expected: PASS

**Step 5: Commit**

```bash
git add backend/open_webui/routers/knowledge.py backend/open_webui/utils/layered_knowledge.py backend/open_webui/test/apps/webui/routers/test_knowledge_layer_backfill.py
git commit -m "feat: add layered knowledge backfill for existing files"
```

### Task 8: Ensure old-file reindex/reset flows can trigger regeneration intentionally

**Files:**
- Modify: `backend/open_webui/routers/knowledge.py`
- Test: `backend/open_webui/test/apps/webui/routers/test_knowledge_layer_sync.py`

**Step 1: Write the failing test**

Add tests asserting:

- `reset` marks rows stale but does not silently create new remote sources
- backfill is the supported path for older files after upgrade
- reindex does not regress the new mapping/chunking logic

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest backend/open_webui/test/apps/webui/routers/test_knowledge_layer_sync.py -q`

Expected: FAIL until the intended behavior is explicit.

**Step 3: Write minimal implementation**

Keep behavior explicit:

- `reset` -> stale only
- `backfill` -> actual old-file catch-up generation

Document this in the router comments and tests to avoid future regression.

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest backend/open_webui/test/apps/webui/routers/test_knowledge_layer_sync.py -q`

Expected: PASS

**Step 5: Commit**

```bash
git add backend/open_webui/routers/knowledge.py backend/open_webui/test/apps/webui/routers/test_knowledge_layer_sync.py
git commit -m "test: lock old-file layered sync behavior"
```

### Task 9: Add frontend controls for selective regenerate and backfill

**Files:**
- Modify: `src/lib/apis/knowledge/index.ts`
- Modify: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
- Modify: `src/lib/components/workspace/Knowledge/KnowledgeBase/LayersPanel.svelte`
- Create: `src/lib/components/workspace/Knowledge/KnowledgeBase/LayerRegenerateMenu.svelte`
- Test: `src/lib/apis/knowledge/index.test.ts`
- Test: `src/lib/components/workspace/Knowledge/KnowledgeBase/LayersPanel.test.ts`
- Test: `src/lib/components/workspace/Knowledge/KnowledgeBase/LayerRegenerateMenu.test.ts`

**Step 1: Write the failing test**

Add UI/API tests asserting:

- file-level regenerate can send selected layer types
- knowledge-level backfill can be triggered from the knowledge view
- default selection is all three layers
- the UI still behaves correctly for chunked layer rows

**Step 2: Run test to verify it fails**

Run:

```bash
npx vitest run \
  src/lib/apis/knowledge/index.test.ts \
  src/lib/components/workspace/Knowledge/KnowledgeBase/LayersPanel.test.ts \
  src/lib/components/workspace/Knowledge/KnowledgeBase/LayerRegenerateMenu.test.ts
```

Expected: FAIL because selective regenerate and backfill controls do not exist yet.

**Step 3: Write minimal implementation**

Add a compact selection UI:

- checkboxes for `Abstract`, `Key Findings`, `Key Data`
- default all selected
- action buttons for:
  - regenerate selected layers for the current file
  - backfill current knowledge base

Do not redesign the page; extend the existing side panel and top controls only.

**Step 4: Run test to verify it passes**

Run:

```bash
npx vitest run \
  src/lib/apis/knowledge/index.test.ts \
  src/lib/components/workspace/Knowledge/KnowledgeBase/LayersPanel.test.ts \
  src/lib/components/workspace/Knowledge/KnowledgeBase/LayerRegenerateMenu.test.ts
```

Expected: PASS

**Step 5: Commit**

```bash
git add src/lib/apis/knowledge/index.ts src/lib/components/workspace/Knowledge/KnowledgeBase.svelte src/lib/components/workspace/Knowledge/KnowledgeBase/LayersPanel.svelte src/lib/components/workspace/Knowledge/KnowledgeBase/LayerRegenerateMenu.svelte src/lib/apis/knowledge/index.test.ts src/lib/components/workspace/Knowledge/KnowledgeBase/LayersPanel.test.ts src/lib/components/workspace/Knowledge/KnowledgeBase/LayerRegenerateMenu.test.ts
git commit -m "feat: add selective regenerate and backfill controls"
```

### Task 10: Add tool/runtime tests for chunked retrieval behavior

**Files:**
- Modify: `backend/open_webui/tools/builtin.py`
- Modify: `backend/open_webui/utils/tools.py`
- Test: `backend/open_webui/test/util/test_layered_knowledge_tools.py`
- Test: `backend/open_webui/test/util/test_attached_knowledge_native_flow.py`

**Step 1: Write the failing test**

Add tests asserting:

- chunked rows are returned as ordinary results
- sources are prefixed like `Abstract 1/3: file.pdf`
- `view_knowledge_layers` returns multiple part entries cleanly

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest backend/open_webui/test/util/test_layered_knowledge_tools.py backend/open_webui/test/util/test_attached_knowledge_native_flow.py -q`

Expected: FAIL because chunk-aware display formatting does not exist yet.

**Step 3: Write minimal implementation**

Update tool formatting so chunk rows are surfaced as normal results with human-readable source labels.

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest backend/open_webui/test/util/test_layered_knowledge_tools.py backend/open_webui/test/util/test_attached_knowledge_native_flow.py -q`

Expected: PASS

**Step 5: Commit**

```bash
git add backend/open_webui/tools/builtin.py backend/open_webui/utils/tools.py backend/open_webui/test/util/test_layered_knowledge_tools.py backend/open_webui/test/util/test_attached_knowledge_native_flow.py
git commit -m "feat: surface chunked layered knowledge results in tools"
```

### Task 11: End-to-end targeted verification

**Files:**
- Modify only if verification reveals a real defect

**Step 1: Run backend targeted suite**

Run:

```bash
./.venv/bin/python -m pytest \
  backend/open_webui/test/apps/webui/routers/test_knowledge_layers_model.py \
  backend/open_webui/test/apps/webui/routers/test_knowledge_layers_router.py \
  backend/open_webui/test/apps/webui/routers/test_knowledge_layer_sync.py \
  backend/open_webui/test/apps/webui/routers/test_knowledge_layer_backfill.py \
  backend/open_webui/test/util/test_layered_knowledge.py \
  backend/open_webui/test/util/test_layered_knowledge_chunking.py \
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
  src/lib/components/workspace/Knowledge/KnowledgeBase/LayersPanel.test.ts \
  src/lib/components/workspace/Knowledge/KnowledgeBase/LayerRegenerateMenu.test.ts
```

Expected: PASS

**Step 3: Manual smoke checklist**

Verify:

- new file in knowledge base auto-generates layers
- large file chunks into multiple Open Notebook sources
- tail chunk under `1000 tokens` is skipped
- selective regenerate works
- old knowledge base files can be backfilled
- tool results show chunk titles cleanly

**Step 4: Commit**

```bash
git add .
git commit -m "test: verify layered knowledge source sync and chunking"
```

### Task 12: Update docs and operator notes

**Files:**
- Modify: `docs/plans/2026-03-25-openwebui-layered-knowledge-design.md`
- Modify: `docs/plans/2026-03-25-openwebui-layered-knowledge-source-sync-implementation.md`

**Step 1: Update design docs**

Document:

- the source mapping approach
- large-file chunking rules
- `24000` token chunk maximum
- discard tail chunk `<1000 tokens`
- default auto-generation for new files
- manual selective regenerate
- backfill path for existing files

**Step 2: Verify docs are accurate**

Check all env vars, APIs, and UI behavior against the implementation.

**Step 3: Commit**

```bash
git add docs/plans/2026-03-25-openwebui-layered-knowledge-design.md docs/plans/2026-03-25-openwebui-layered-knowledge-source-sync-implementation.md
git commit -m "docs: update layered knowledge sync and chunking plan"
```

Plan complete and saved to `docs/plans/2026-03-25-openwebui-layered-knowledge-source-sync-implementation.md`. Two execution options:

**1. Subagent-Driven (this session)** - I dispatch fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** - Open new session with executing-plans, batch execution with checkpoints

Which approach?
