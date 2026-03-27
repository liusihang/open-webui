# OpenWebUI Internal Layer Generation Settings Design

**Goal:** Expose the real internal layered-knowledge generation settings in Admin > Documents without reintroducing the legacy Open Notebook configuration surface.

## Current State

The layered knowledge pipeline already generates layer content natively inside Open WebUI by calling `generate_chat_completion(...)` from `backend/open_webui/utils/layered_knowledge.py`.

However, the admin UI still hides the old Open Notebook configuration fields, and there is no replacement UI for the internal flow. This makes the feature feel missing even though the internal generation path is active.

## Desired Admin Experience

Add a dedicated `Layer Generation` section under Admin > Documents that exposes only the internal controls that actually affect the current runtime path:

- generation model
- generation prompt template
- maximum chunk tokens
- minimum tail tokens

This section should make it clear that the current active internal layer generation flow is abstract-focused and does not depend on an external Open Notebook service.

## Backend Design

Add first-class persistent config entries for the internal flow:

- `LAYER_GENERATION_MODEL`
- `LAYER_GENERATION_PROMPT_ABSTRACT`
- `LAYER_GENERATION_MAX_CHUNK_TOKENS`
- `LAYER_GENERATION_MIN_TAIL_TOKENS`

Expose these via `GET /retrieval/config` and accept them via `POST /retrieval/config/update`.

Update `backend/open_webui/utils/layered_knowledge.py` so runtime helpers prefer these explicit internal settings over implicit defaults:

- `_layer_generation_chunk_limits(...)`
- `_get_layer_generation_model_id(...)`
- `_get_layer_generation_prompt(...)`

## UI Design

In `src/lib/components/admin/Settings/Documents.svelte`, add a new `Layer Generation` subsection near the existing chunking/retrieval controls.

Fields:

- `Generation Model`
- `Max Chunk Tokens`
- `Min Tail Tokens`
- `Abstract Prompt`

The UI copy should reference internal generation and avoid all `Open Notebook` wording.

## Compatibility

Do not restore the hidden `OPEN_NOTEBOOK_*` settings to the public admin UI.

Keep the old backend config values in place for now to avoid broad cleanup risk, but stop treating them as the admin-facing source of truth for layer generation.

## Verification

Add or update tests to prove:

- retrieval config returns the new internal layer generation fields
- retrieval config updates persist the new fields
- admin documents settings source includes the new internal labels
- admin documents settings source does not expose old Open Notebook labels
- layered knowledge helper functions read the new explicit settings
