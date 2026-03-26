# OpenWebUI Layer Generation Documents Entry Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a Layer Generation section under `Admin -> Settings -> Documents` so admins can manage Open Notebook connection and transformation settings from the existing documents settings page.

**Architecture:** Extend the existing retrieval config GET/UPDATE contract to include Open Notebook layer settings, then render/edit those fields inside `Documents.svelte`. Keep the change minimal by reusing the current Documents load/save flow and existing `PersistentConfig` wiring.

**Tech Stack:** FastAPI, Pydantic, Svelte, TypeScript, Vitest, pytest

---

### Task 1: Add failing backend tests for retrieval config exposure

**Files:**
- Modify: `backend/open_webui/test/apps/webui/routers/test_retrieval_router.py`

**Step 1: Write the failing test**
- Add one test that asserts retrieval config GET includes:
  - `OPEN_NOTEBOOK_BASE_URL`
  - `OPEN_NOTEBOOK_API_PASSWORD`
  - `OPEN_NOTEBOOK_TIMEOUT_SECS`
  - `OPEN_NOTEBOOK_TRANSFORMATION_ABSTRACT`
  - `OPEN_NOTEBOOK_TRANSFORMATION_KEY_FINDINGS`
  - `OPEN_NOTEBOOK_TRANSFORMATION_KEY_DATA`
- Add one test that posts updated values and asserts `request.app.state.config` receives them.

**Step 2: Run test to verify it fails**
Run: `pytest backend/open_webui/test/apps/webui/routers/test_retrieval_router.py -q`
Expected: FAIL because retrieval config does not yet expose/update these fields.

**Step 3: Write minimal implementation**
- Update retrieval router response and form schema only as needed to satisfy the tests.

**Step 4: Run test to verify it passes**
Run: `pytest backend/open_webui/test/apps/webui/routers/test_retrieval_router.py -q`
Expected: PASS

### Task 2: Add failing frontend tests for Documents layer section

**Files:**
- Create or modify: `src/lib/components/admin/Settings/Documents.test.ts`
- Modify if needed: `src/lib/apis/retrieval/index.ts`

**Step 1: Write the failing test**
- Add a focused test that checks the Documents settings source contains a `Layer Generation` section and binds the Open Notebook config keys.

**Step 2: Run test to verify it fails**
Run: `npm run test:frontend -- --run src/lib/components/admin/Settings/Documents.test.ts`
Expected: FAIL because the section is not present yet.

**Step 3: Write minimal implementation**
- Add the new section and bind it to `RAGConfig` fields loaded/saved through the existing flow.

**Step 4: Run test to verify it passes**
Run: `npm run test:frontend -- --run src/lib/components/admin/Settings/Documents.test.ts`
Expected: PASS

### Task 3: Implement backend retrieval config support

**Files:**
- Modify: `backend/open_webui/routers/retrieval.py`

**Step 1: Extend response payload**
- Include the Open Notebook fields in the config returned by `get_rag_config`.

**Step 2: Extend update form**
- Add typed optional fields for the Open Notebook values.

**Step 3: Persist updates**
- In the update handler, assign the submitted values back onto `request.app.state.config`.

**Step 4: Re-run backend tests**
Run: `pytest backend/open_webui/test/apps/webui/routers/test_retrieval_router.py -q`
Expected: PASS

### Task 4: Implement Documents UI section

**Files:**
- Modify: `src/lib/components/admin/Settings/Documents.svelte`

**Step 1: Add UI fields**
- Add a visible `Layer Generation` heading and the six fields.

**Step 2: Reuse existing save flow**
- Ensure `submitHandler` sends them through `updateRAGConfig` without creating a second save button.

**Step 3: Keep behavior minimal**
- Do not add validation beyond simple existing form semantics.

**Step 4: Re-run frontend tests**
Run: `npm run test:frontend -- --run src/lib/components/admin/Settings/Documents.test.ts`
Expected: PASS

### Task 5: Verify related flows

**Files:**
- No new files required

**Step 1: Run focused backend verification**
Run: `pytest backend/open_webui/test/apps/webui/routers/test_retrieval_router.py backend/open_webui/test/apps/webui/routers/test_knowledge_layers_router.py -q`
Expected: PASS

**Step 2: Run focused frontend verification**
Run: `npm run test:frontend -- --run src/lib/components/admin/Settings/Documents.test.ts src/lib/apis/knowledge/index.test.ts src/lib/components/workspace/Knowledge/KnowledgeBase.imports.test.ts src/lib/components/workspace/Knowledge/KnowledgeBase/LayerRegenerateMenu.test.ts src/lib/components/workspace/Knowledge/KnowledgeBase/LayersPanel.test.ts`
Expected: PASS
