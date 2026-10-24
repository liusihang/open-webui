# Document image repair (2026-10-24)

## Accepted behavior

Text ingestion publishes independently of optional document images. Image decoding,
conversion and embedding run in a durable child job after publication. A failed,
cancelled or expired image job cannot mark the completed original/text as failed.
This supersedes the previous all-or-nothing text/image publication rule.

Individual warnings include format, stage, stable reason code and a safe readable
message. No service response body, signed URL, credential or document body is
included in diagnostics. Unsupported core document parsing still fails explicitly
and retains the original; it must not claim successful text indexing.

## Implementation and acceptance

- Branch: `codex/office-wmf-assets`; base main `dba4629e2a42`.
- Two rejected Live uploads have identical DOCX content and one WMF image each.
  Root cause: raster-only image acceptance plus all-or-nothing publication.
- Primary/child queue separation and non-fatal image warning regressions pass:
  18 real SQL tests on both SQLite and isolated PostgreSQL (psycopg driver).
- 38 format/converter/diagnostic tests pass in the isolated Slim runtime with
  networking disabled, read-only root, all capabilities dropped, 1 GiB RAM,
  1 CPU and a 512 MiB temporary filesystem. The test process also runs as UID 1000.
- Existing document-image and Slim Office dispatch regressions: 42 pass.
- Admission/status regressions: 12 pass. Modified Svelte viewer compiles with
  zero warnings; no production-browser acceptance is claimed.
- The exact original DOCX parses to nonempty text, stages one raw WMF image,
  then converts it to PNG without skips. No private body is printed and neither
  rejected Live job is replayed.
- Isolated image dependency probe: aiserver staging
  `/home/aiserver/staging/office-wmf-repair-20261024-7MzRX1fD`, image
  `open-webui:office-repair-verified-20261024`, with latest source mounted read-only
  for final acceptance. No Live credentials or live data directory mount.
- No production repair replay is included. The 8 GiB Web limit already applied
  to Live is persisted in `deploy/compose.indexing.yaml` for subsequent recreates.

## Format handling

- Native bytes retained: PNG, JPEG, WebP, GIF, TIFF, BMP.
- Raster normalization to PNG: AVIF, HEIC/HEIF, ICO, ICNS, JPEG2000, MPO, PCX,
  PPM-family, PSD composite, TGA, DIB, DDS.
- Bounded vector conversion to PNG: WMF/WMZ, EMF/EMZ (EMF+ enabled), SVG/SVGZ,
  EPS and the first page of an embedded PDF graphic. This does not replace the
  normal multi-page PDF document parser.
- Layered/multi-frame extra formats use the composite/first frame, not an
  animation or layer-editing pipeline. Unknown formats and unsupported drawing
  commands remain explicit warnings. No universal-format guarantee is made.
- Raw source image bytes and originals are retained. Existing native image
  hashes remain unchanged. No embedding model change or vector-store rebuild.

## Release boundary

The user subsequently authorized committing, pushing and merging to main, then
deploying to Live only while no one is using the service. Release must preserve
four Web processes (8 GiB total) and two isolated indexers, and must not replace
the database, Redis, terminal or application data mounts.

The backend-only indexing Dockerfile does not rebuild frontend assets: the new
warning viewer requires the normal frontend build when releasing. Full API
upload/production-browser acceptance remains a deployment step; isolated
parser/converter and SQL checks are not represented as that test.
