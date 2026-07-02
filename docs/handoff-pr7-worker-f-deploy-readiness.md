# PR7 Worker F Deploy Readiness Audit

## Scope

Audit the current PR7 branch for push/rebuild/deploy readiness without touching any live stack.

## Git / Worktree Status

- Branch: `codex/pr7-onlyoffice-upload-preview-worker`
- HEAD: `10e3670c18f4e3665b42c4a40be5d0bf4330a162`
- Worktree is not clean.
- Existing uncommitted changes are present in:
  - `backend/open_webui/retrieval/loaders/paddleocr_vl.py`
  - `backend/open_webui/test/util/test_paddleocr_vl_loader.py`
  - `src/lib/components/chat/FileNav/FilePreview.svelte`
  - `uv.lock`
- This audit did not modify those files.
- This report file is the only file I added.

Main-thread follow-up after this audit:

- `uv.lock` was confirmed as tooling noise and restored.
- The three uncommitted source changes from the review/regression workstream were kept for commit.
- OnlyOffice runtime config wiring was added in `backend/open_webui/config.py` and `backend/open_webui/main.py`.
- `.env.example` now advertises the OnlyOffice, document image asset, and PaddleOCR-VL settings without real secrets.
- `backend/open_webui/test/util/test_onlyoffice_config_contract.py` was added and passed.

## What Is Already Ready

- `RAG_EXTRACT_DOCUMENT_IMAGE_ASSETS` is fully wired through backend config, app startup, RAG config endpoints, loader construction, and the admin Documents UI.
- PaddleOCR-VL async OCR settings are fully wired through backend config, app startup, RAG config endpoints, loader construction, and the admin Documents UI:
  - `PADDLEOCR_VL_BASE_URL`
  - `PADDLEOCR_VL_TOKEN`
  - `PADDLEOCR_VL_MODEL`
  - `PADDLEOCR_VL_OPTIONAL_PAYLOAD`
  - `PADDLEOCR_VL_REQUEST_TIMEOUT`
  - `PADDLEOCR_VL_DOWNLOAD_TIMEOUT`
  - `PADDLEOCR_VL_POLL_TIMEOUT`
  - `PADDLEOCR_VL_POLL_INTERVAL`
- OnlyOffice preview selection on the frontend is already routed to `OnlyOfficeViewer` for supported office-like uploads, and the supported-file allowlist now includes PDF and the broader office formats.

## Deploy Blockers

1. The worktree is not clean enough for a clean git archive build.
   - There are pre-existing uncommitted changes in three source files plus `uv.lock`.
   - Until those changes are committed or otherwise handled by the owning workstream, a clean archive build from this checkout is blocked.

2. RESOLVED BY MAIN THREAD AFTER THIS AUDIT: OnlyOffice runtime config was not yet wired into the backend config surface.
   - The router expects these `app.state.config` values:
     - `ENABLE_ONLYOFFICE_PREVIEW`
     - `ONLYOFFICE_DOCUMENT_SERVER_URL`
     - `ONLYOFFICE_PUBLIC_BASE_URL`
     - `ONLYOFFICE_JWT_SECRET`
     - `ONLYOFFICE_FILE_TOKEN_EXPIRES_IN`
     - `ONLYOFFICE_EDIT_CALLBACK_TOKEN_EXPIRES_IN`
     - `ONLYOFFICE_CALLBACK_ALLOWED_HOSTS`
   - Follow-up fix added `ConfigVar` definitions, `app.state.config` assignments, `.env.example` placeholders, and `backend/open_webui/test/util/test_onlyoffice_config_contract.py`.
   - Result: preview/callback routes now have a deployable runtime config path.

3. Existing deployments may still override env defaults from the persistent config row.
   - `ConfigVar` values can be loaded from the DB config blob at startup.
   - For a live deployment, env changes alone are not enough if the DB already holds older values.

## Required Env / Config

### Backend runtime

- `RAG_EXTRACT_DOCUMENT_IMAGE_ASSETS`:
  - optional env override
  - default is inherited from `ENABLE_MULTIMODAL_KNOWLEDGE_EVIDENCE`
  - persists under `rag.extract_document_image_assets`
- PaddleOCR-VL async OCR:
  - `PADDLEOCR_VL_BASE_URL`
  - `PADDLEOCR_VL_TOKEN`
  - `PADDLEOCR_VL_MODEL`
  - `PADDLEOCR_VL_OPTIONAL_PAYLOAD` as a JSON object
  - `PADDLEOCR_VL_REQUEST_TIMEOUT`
  - `PADDLEOCR_VL_DOWNLOAD_TIMEOUT`
  - `PADDLEOCR_VL_POLL_TIMEOUT`
  - `PADDLEOCR_VL_POLL_INTERVAL`
- Multimodal knowledge evidence:
  - `ENABLE_MULTIMODAL_KNOWLEDGE_EVIDENCE`

### Still missing and needed before OnlyOffice deploy

- `ENABLE_ONLYOFFICE_PREVIEW`
- `ONLYOFFICE_DOCUMENT_SERVER_URL`
- `ONLYOFFICE_PUBLIC_BASE_URL`
- `ONLYOFFICE_JWT_SECRET`
- `ONLYOFFICE_FILE_TOKEN_EXPIRES_IN`
- `ONLYOFFICE_EDIT_CALLBACK_TOKEN_EXPIRES_IN`
- `ONLYOFFICE_CALLBACK_ALLOWED_HOSTS`

## Safe Rebuild / Deploy Boundaries

### Safe in an isolated stack

- Build and start a separate compose project name or isolated worktree-backed stack.
- Rebuild the application image only for that isolated stack.
- Run `uv run pytest ...`, `py_compile`, and frontend build/test commands locally.
- Use the admin Documents UI or API only against the isolated stack.

### Explicitly forbidden against live

- `docker compose up`, `down`, `restart`, `exec`, or `logs` on the production project.
- Any `kubectl rollout restart`, `apply`, or equivalent live service mutation.
- Any config-update or reindex request sent to the live deployment.
- Any database writes outside the isolated stack.

## Knowledge Rebuild / Reindex Order

1. Restart only the isolated stack after setting env/config.
2. Verify the config endpoint returns the expected values for RAG, PaddleOCR-VL, and OnlyOffice fields.
3. Start with one narrow knowledge base or one file set.
4. Run evidence rebuild first:
   - `POST /api/v1/knowledge/{id}/evidence/rebuild`
   - prefer a narrow `file_ids` list
   - let `project_document_images` auto-resolve unless you explicitly need to force it
5. Check the job result and confirm rows exist for:
   - `retrieval_index_job`
   - `knowledge_evidence_asset`
   - `knowledge_evidence` with image modality
6. Only after evidence projection is correct, run lexical/full reindex for the same `collection_ids`.
7. Expand to broader collections only after the narrow run is clean.

## Suggested Minimal Next Step

- Add the missing OnlyOffice config `ConfigVar`s plus `app.state.config` wiring, then update the env/sample deployment docs so the preview route has a real runtime config path.

## File Changed By This Audit

- `docs/handoff-pr7-worker-f-deploy-readiness.md`
