# OpenWebUI Abstract-Only Layer Alignment Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Re-align the current branch to the `9e89a7a107` abstract-only layer behavior while preserving minimal compatibility for old `key_findings` / `key_data` data and config.

**Architecture:** Remove `key_findings` / `key_data` from visible UI and active config surfaces, and centralize compatibility in backend layer normalization/alias handling so old data can still be tolerated without remaining a first-class feature.

**Tech Stack:** FastAPI, Pydantic, Svelte, TypeScript, pytest, Vitest

---

### Task 1: Add failing frontend tests for abstract-only UI

**Files:**
- Modify: `src/lib/components/workspace/Knowledge/KnowledgeBase/LayersPanel.test.ts`
- Modify: `src/lib/components/workspace/Knowledge/KnowledgeBase/LayerRegenerateMenu.test.ts`
- Modify: `src/lib/components/admin/Settings/Documents.test.ts`

**Step 1: Write the failing tests**
- Assert `LAYER_TYPE_ORDER` is `['abstract']`
- Assert layer menu defaults/selectors only mention `abstract`
- Assert Documents layer generation section no longer contains key findings / key data config fields

**Step 2: Run tests to verify they fail**
Run: `npm run test:frontend -- --run src/lib/components/workspace/Knowledge/KnowledgeBase/LayersPanel.test.ts src/lib/components/workspace/Knowledge/KnowledgeBase/LayerRegenerateMenu.test.ts src/lib/components/admin/Settings/Documents.test.ts`
Expected: FAIL because current UI still includes removed layers/configs

**Step 3: Write minimal implementation**
- Update UI constants and config section to abstract-only

**Step 4: Run tests to verify they pass**
Run the same command and expect PASS

### Task 2: Add failing backend tests for compatibility mapping

**Files:**
- Modify: `backend/open_webui/test/util/test_layered_knowledge.py`
- Modify: `backend/open_webui/test/apps/webui/routers/test_retrieval_config_layer_generation.py`

**Step 1: Write the failing tests**
- Assert old `key_findings` / `key_data` inputs normalize to `abstract`
- Assert retrieval config exposure/update is abstract-only on the admin surface

**Step 2: Run tests to verify they fail**
Run: `uv run pytest backend/open_webui/test/util/test_layered_knowledge.py backend/open_webui/test/apps/webui/routers/test_retrieval_config_layer_generation.py -q`
Expected: FAIL because code still exposes multiple layers / configs

**Step 3: Write minimal implementation**
- Centralize alias mapping in backend normalization and retrieval config handling

**Step 4: Run tests to verify they pass**
Run the same command and expect PASS

### Task 3: Implement backend abstract-only semantics with compatibility

**Files:**
- Modify: `backend/open_webui/models/knowledge_layers.py`
- Modify: `backend/open_webui/utils/layered_knowledge.py`
- Modify: `backend/open_webui/tools/builtin.py`
- Modify: `backend/open_webui/utils/tools.py`
- Modify: `backend/open_webui/routers/retrieval.py`

**Step 1: Limit active layer enum/selection surfaces to `abstract`**
**Step 2: Preserve alias compatibility for old names**
**Step 3: Remove key findings/data from admin-facing config API**
**Step 4: Re-run focused backend tests**

### Task 4: Implement frontend abstract-only UI

**Files:**
- Modify: `src/lib/components/workspace/Knowledge/KnowledgeBase/LayersPanel.svelte`
- Modify: `src/lib/components/workspace/Knowledge/KnowledgeBase/LayerRegenerateMenu.svelte`
- Modify: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
- Modify: `src/lib/components/admin/Settings/Documents.svelte`
- Modify: `src/lib/apis/knowledge/index.ts`
- Modify: `src/lib/apis/retrieval/index.ts`

**Step 1: Remove visible key findings/data options**
**Step 2: Keep only abstract transformation field in Documents**
**Step 3: Re-run focused frontend tests**

### Task 5: Verify end-to-end focused regressions

**Step 1: Backend verification**
Run: `uv run pytest backend/open_webui/test/apps/webui/routers/test_knowledge_layers_router.py backend/open_webui/test/util/test_layered_knowledge.py backend/open_webui/test/apps/webui/routers/test_retrieval_config_layer_generation.py -q`
Expected: PASS

**Step 2: Frontend verification**
Run: `npm run test:frontend -- --run src/lib/components/admin/Settings/Documents.test.ts src/lib/components/workspace/Knowledge/KnowledgeBase/LayersPanel.test.ts src/lib/components/workspace/Knowledge/KnowledgeBase/LayerRegenerateMenu.test.ts src/lib/components/workspace/Knowledge/KnowledgeBase.imports.test.ts src/lib/apis/knowledge/index.test.ts`
Expected: PASS
