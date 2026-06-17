# PR7 Worker E Regression Report

## Scope

Non-OFD regression evidence for the document image asset / evidence chain:

- PDF and Office uploads produce `file.data.document_image_assets`.
- Reindex/projector emits `knowledge_evidence_asset` plus `knowledge_evidence` rows with `modality='image'`.
- OnlyOffice routing still sends supported office documents to OnlyOffice and keeps text/image-native previews native.
- OFD is out of scope and was not tested.

## Test Material

Generated locally during this run under:

- `/var/folders/tj/vxszzp392mz_f8gh8w7kbd780000gn/T/pr7-e-regression-1o3otvpj/`

Key artifacts from that run:

- `/var/folders/tj/vxszzp392mz_f8gh8w7kbd780000gn/T/pr7-e-regression-1o3otvpj/sample.pdf`
- `/var/folders/tj/vxszzp392mz_f8gh8w7kbd780000gn/T/pr7-e-regression-1o3otvpj/sample.pptx`
- `/var/folders/tj/vxszzp392mz_f8gh8w7kbd780000gn/T/pr7-e-regression-1o3otvpj/config.db`

The PDF and PPTX both contained a visible text block plus one embedded raster image.

## Commands Run

### 1) Backend integration / regression tests

```bash
uv run pytest -q \
  backend/open_webui/test/apps/webui/routers/test_retrieval_hybrid_query.py::test_process_file_persists_loader_document_image_assets \
  backend/open_webui/test/apps/webui/routers/test_knowledge_reindex_api.py::test_rebuild_evidence_auto_projects_document_images_for_whole_knowledge \
  backend/open_webui/test/util/test_pdf_image_asset_loader.py \
  backend/open_webui/test/util/test_office_image_asset_loader.py \
  backend/open_webui/test/util/test_evidence_projector.py::test_document_image_assets_projection_creates_real_asset_and_evidence
```

Result:

- 10 passed
- warnings only, no failures

### 2) Real local loader -> file.data -> projector smoke

```bash
uv run python - <<'PY'
... generated PDF/PPTX, loaded them with `Loader`, wrote `document_image_assets` into a temp SQLite file row, and ran `project_evidence_for_knowledge_file` ...
PY
```

Result:

- PDF loader produced 1 document and 1 `document_image_assets` entry
- PDF asset backend: `pypdf`
- PDF projector result: `image_assets_upserted=1`, `image_evidence_upserted=1`
- PPTX loader produced 1 document and 1 `document_image_assets` entry
- PPTX asset backend: `office_zip`
- PPTX projector result: `image_assets_upserted=1`, `image_evidence_upserted=1`
- DB check after both projections: 2 `knowledge_evidence_asset` rows, 2 `knowledge_evidence` rows, 2 image evidence rows

### 3) Frontend preview routing

```bash
npm run test:frontend -- --run src/lib/utils/filePreviewTypes.test.ts
```

Result:

- 30 passed
- Verified pdf/docx/odt/ods/odp/pptx-family files route to OnlyOffice
- Verified txt/md/json/html/js/png/mp4/zip/bin stay out of OnlyOffice routing

## Findings

- The PDF path with embedded images now yields `document_image_assets`, and projector turns those into `knowledge_evidence_asset` plus `knowledge_evidence(modality='image')`.
- The Office path with embedded images now yields `document_image_assets`, and projector produces the same image evidence objects.
- The process-file persistence path is covered by the backend router test, which confirms loader asset metadata is written into `file.data`.
- Reindex/evidence rebuild auto-enables document image projection when scoped files or whole knowledge contain `document_image_assets`.
- OnlyOffice routing still keeps native text/image previews native and routes supported office documents to OnlyOffice.

## Unrun / Not Needed

- Live stack / live container smoke was not run. The instruction explicitly forbids touching live stack/container, and no isolated-stack endpoint or credential set was provided in this turn.
- OFD was not exercised because it is out of scope.

## Status

Real local regression evidence is complete for the non-OFD document image asset chain.
