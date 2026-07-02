# PR7 Worker B Handoff: Document Image Asset Extraction Config

## Scope

Implement the narrow `RAG_EXTRACT_DOCUMENT_IMAGE_ASSETS` setting for local document image asset fallback extraction.

## Requirements

- Add ConfigVar `RAG_EXTRACT_DOCUMENT_IMAGE_ASSETS` with persisted path `rag.extract_document_image_assets`.
- Default behavior:
  - explicit `RAG_EXTRACT_DOCUMENT_IMAGE_ASSETS` env wins;
  - otherwise initial value follows `ENABLE_MULTIMODAL_KNOWLEDGE_EVIDENCE`;
  - do not derive it from runtime DB values.
- Include the field in RAG config get/update responses.
- Pass the field through `build_loader_from_config()` to `Loader`.
- Use the switch to control local PDF/Office-style fallback extraction of `document_image_assets`.
- Do not confuse this with `PDF_EXTRACT_IMAGES`, which should remain the PDF OCR image extraction switch.
- Do not gate PaddleOCR-VL primary path layout image outputs.
- Avoid `office_image_assets` and OnlyOffice preview helper changes.
- Do not commit `uv.lock`.

## Current Checkpoint

- Branch verified: `codex/pr7-onlyoffice-upload-preview-worker`.
- HEAD verified: `93cc311c9`.
- Worktree initially clean.
- Existing behavior found:
  - `PDF_EXTRACT_IMAGES` is defined in `backend/open_webui/config.py`.
  - `build_loader_from_config()` passes `PDF_EXTRACT_IMAGES` to `Loader`.
  - `Loader.load()` attaches PDF fallback `document_image_assets` when `PDF_EXTRACT_IMAGES` is true.
  - PaddleOCR-VL emits `document_image_assets` from its own loader path.
- RED checkpoint completed:
  - Command: `uv run pytest backend/open_webui/test/util/test_evidence_config_contract.py backend/open_webui/test/util/test_paddleocr_vl_loader.py::test_build_loader_from_config_includes_paddleocr_async_options backend/open_webui/test/util/test_pdf_image_asset_loader.py`
  - Expected failures observed:
    - `RAG_EXTRACT_DOCUMENT_IMAGE_ASSETS` is not importable from config.
    - `build_loader_from_config()` does not include `RAG_EXTRACT_DOCUMENT_IMAGE_ASSETS`.
    - PDF fallback still emits `document_image_assets` when only `PDF_EXTRACT_IMAGES=True`.
- GREEN checkpoint completed:
  - Implemented config/main/router/build-loader/Loader/UI chain.
  - Command rerun: `uv run pytest backend/open_webui/test/util/test_evidence_config_contract.py backend/open_webui/test/util/test_paddleocr_vl_loader.py::test_build_loader_from_config_includes_paddleocr_async_options backend/open_webui/test/util/test_pdf_image_asset_loader.py`
  - Result: 7 passed, 4 existing warnings.
- Final verification checkpoint:
  - `python3 -m py_compile backend/open_webui/config.py backend/open_webui/main.py backend/open_webui/routers/retrieval.py backend/open_webui/retrieval/utils.py backend/open_webui/retrieval/loaders/main.py backend/open_webui/test/util/test_evidence_config_contract.py backend/open_webui/test/util/test_paddleocr_vl_loader.py backend/open_webui/test/util/test_pdf_image_asset_loader.py` passed.
  - `npm run check` failed on existing broad Svelte/type diagnostics outside this change; first errors were in `src/lib/components/common/RichTextInput/AutoCompletion.js` and `listDragHandlePlugin.js`.
  - `npm run build` passed after Pyodide package preparation, with existing Vite/Svelte warnings.
  - Fresh focused pytest rerun after loader diff cleanup: 7 passed, 4 existing warnings.
  - Office guard amend:
    - Added a failing Office fallback test showing `RAG_EXTRACT_DOCUMENT_IMAGE_ASSETS=False` must suppress Office `document_image_assets` even when `OFFICE_EXTRACT_IMAGE_ASSETS=True`.
    - Implemented the minimal Loader guard so Office fallback requires both `RAG_EXTRACT_DOCUMENT_IMAGE_ASSETS` and the existing `OFFICE_EXTRACT_IMAGE_ASSETS`.
    - Fresh command: `uv run pytest backend/open_webui/test/util/test_evidence_config_contract.py backend/open_webui/test/util/test_paddleocr_vl_loader.py::test_build_loader_from_config_includes_paddleocr_async_options backend/open_webui/test/util/test_pdf_image_asset_loader.py backend/open_webui/test/util/test_office_image_asset_loader.py`
    - Result: 11 passed, 4 existing warnings.
  - `uv.lock` changed during local commands but is intentionally not part of this task/commit.

## Plan

1. Add focused failing tests for:
   - config default/env fallback semantics;
   - `build_loader_from_config()` passing `RAG_EXTRACT_DOCUMENT_IMAGE_ASSETS`;
   - `Loader` using the new switch for PDF fallback while leaving `PDF_EXTRACT_IMAGES` available for OCR extraction.
2. Implement the minimal config chain in backend config/main/retrieval utils/router.
3. Add an Admin Documents switch with copy that distinguishes asset fallback from PDF OCR image extraction.
4. Run focused backend tests, py_compile touched backend files, and a frontend build/check covering `Documents.svelte`.
5. Commit only scoped files.
