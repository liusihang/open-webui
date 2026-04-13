# OnlyOffice Preview Frontend Research Handoff (Phase 0)

## Task Goal
- Investigate current Office preview rendering paths in `FilePreview.svelte` and `FileItemModal.svelte`.
- Propose minimal insertion points for integrating `OnlyOfficeViewer.svelte` (read-only first).
- List concrete files/state/props to touch.
- Identify regression risks and fallback strategy for current local converters (`docx/xlsx/pptx`).

## Context
- Workspace: `/Users/liusihang/openwebui/.worktrees/codex-onlyoffice-phase0-1`
- Date: `2026-04-13`

## Checkpoints
1. **Repo and skill bootstrap**
   - Confirmed target worktree is accessible and loaded required process skills.
   - Confirmed no existing `OnlyOfficeViewer.svelte` in `src/`.

2. **Path discovery for FileNav/FilePreview pipeline**
   - Traced from `FileNav.svelte` file selection/open logic to `FilePreview.svelte`.
   - Identified Office branch states: `fileOfficeHtml`, `fileOfficeSlides`, `excelSheetNames`, `selectedExcelSheet`, `currentSlide`.
   - Verified render branches in `FilePreview.svelte`:
     - HTML-based office preview (`{@html fileOfficeHtml}`)
     - Slide-image preview (`fileOfficeSlides` with pager)

3. **Path discovery for FilesModal/FileItemModal pipeline**
   - Traced calls:
     - `FilesModal.svelte` -> `FileItemModal.svelte`
     - `FileItem.svelte` -> `FileItemModal.svelte`
   - Verified Office detection and load path in `FileItemModal.svelte`:
     - `isExcel/isDocx/isPptx` flags
     - local conversion via `xlsx`, `mammoth`, `pptxToHtml`
   - Verified preview-tab rendering branches for each Office type.

4. **API/data-access constraints collected**
   - FileNav path uses terminal API (`/files/view?path=...` with Bearer key) and local blob conversion.
   - FileItemModal path uses webui file endpoint (`/files/{id}/content`, credentials include) and local conversion.
   - This implies OnlyOffice integration likely needs two different document-source adapters (terminal path vs webui file-id path) or a shared normalized source layer.

5. **Integration scope and fallback decision captured**
   - Confirmed there is no existing `OnlyOfficeViewer.svelte` in this branch; integration starts from a new component.
   - Confirmed current Office preview rendering is duplicated in two places and should be switched by a small feature gate (`preferOnlyOffice` style computed flag).
   - Confirmed legacy local converters should remain as fallback:
     - `mammoth` (`docx`) for robust no-service preview path.
     - `xlsx + excelToTable` (`xlsx/csv`) for sheet-level fallback.
     - `pptxToHtml` (`pptx` to images) for deck fallback.
   - Confirmed additional path to watch: `PyodideFileNav.svelte` also reuses `FilePreview.svelte`, so FilePreview-level insertion naturally covers both terminal file nav and pyodide file nav.

## Current Status
- Research completed; no business code modified.
- Handoff updated with final insertion strategy and fallback posture.
