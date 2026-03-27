# OpenWebUI Internal Layer Generation Settings Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a real admin settings surface for the internal layered-knowledge generation flow and wire it to the active backend runtime helpers.

**Architecture:** Expose a small set of internal layer generation config values through the retrieval config API, render them inside the existing Admin > Documents page, and switch layered-knowledge helpers to prefer these explicit settings instead of implicit defaults. Keep the legacy Open Notebook settings hidden and untouched for now.

**Tech Stack:** FastAPI, Pydantic, Svelte, Vitest, pytest

---

### Task 1: Add failing backend retrieval config tests

**Files:**
- Modify: `backend/open_webui/test/apps/webui/routers/test_retrieval_config_layer_generation.py`

**Step 1: Write failing tests**

Add assertions that `get_rag_config(...)` includes:

- `LAYER_GENERATION_MODEL`
- `LAYER_GENERATION_PROMPT_ABSTRACT`
- `LAYER_GENERATION_MAX_CHUNK_TOKENS`
- `LAYER_GENERATION_MIN_TAIL_TOKENS`

Add assertions that `update_rag_config(...)` persists updated values for those same fields.

**Step 2: Run test to verify it fails**

Run: `uv run --python 3.11 python -m pytest backend/open_webui/test/apps/webui/routers/test_retrieval_config_layer_generation.py -q`

**Step 3: Implement minimal backend config exposure**

Update `backend/open_webui/routers/retrieval.py` config response and update form handling.

**Step 4: Run test to verify it passes**

Run the same pytest command.

### Task 2: Add failing layered-knowledge helper tests

**Files:**
- Modify: `backend/open_webui/test/util/test_layered_knowledge.py`

**Step 1: Write failing tests**

Add tests that:

- `_get_layer_generation_model_id(...)` prefers `LAYER_GENERATION_MODEL`
- `_layer_generation_chunk_limits(...)` reads explicit chunk limit config
- `_get_layer_generation_prompt(...)` reads `LAYER_GENERATION_PROMPT_ABSTRACT`

**Step 2: Run test to verify it fails**

Run: `uv run --python 3.11 python -m pytest backend/open_webui/test/util/test_layered_knowledge.py -q`

**Step 3: Implement minimal helper changes**

Update `backend/open_webui/utils/layered_knowledge.py`.

**Step 4: Run test to verify it passes**

Run the same pytest command.

### Task 3: Add failing admin UI source test

**Files:**
- Modify: `src/lib/components/admin/Settings/Documents.test.ts`

**Step 1: Write failing test**

Assert that the documents settings source contains:

- `Layer Generation`
- `Generation Model`
- `Max Chunk Tokens`
- `Min Tail Tokens`
- `Abstract Prompt`

And still does not expose:

- `Open Notebook Base URL`
- `OPEN_NOTEBOOK_BASE_URL`

**Step 2: Run test to verify it fails**

Run: `npm test -- src/lib/components/admin/Settings/Documents.test.ts`

**Step 3: Implement minimal UI**

Update `src/lib/components/admin/Settings/Documents.svelte` to render and submit the new fields.

**Step 4: Run test to verify it passes**

Run the same test command.

### Task 4: Add config definitions and app-state wiring

**Files:**
- Modify: `backend/open_webui/config.py`
- Modify: `backend/open_webui/main.py`

**Step 1: Add persistent config fields**

Create:

- `LAYER_GENERATION_MODEL`
- `LAYER_GENERATION_PROMPT_ABSTRACT`
- `LAYER_GENERATION_MAX_CHUNK_TOKENS`
- `LAYER_GENERATION_MIN_TAIL_TOKENS`

**Step 2: Wire app state**

Attach them onto `app.state.config` in `main.py`.

**Step 3: Run focused backend tests**

Run the retrieval and layered knowledge pytest files again.

### Task 5: Full targeted verification

**Files:**
- No new files

**Step 1: Run backend verification**

Run:

`uv run --python 3.11 python -m pytest backend/open_webui/test/apps/webui/routers/test_retrieval_config_layer_generation.py backend/open_webui/test/util/test_layered_knowledge.py -q`

**Step 2: Run frontend verification**

Run:

`npm test -- src/lib/components/admin/Settings/Documents.test.ts`

**Step 3: Check worktree**

Run:

`git status --short`

**Step 4: Commit**

Create a commit with a message like:

`feat: expose internal layer generation admin settings`
