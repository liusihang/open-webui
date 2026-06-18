# PR7 非 OFD 范围规格合规与风险审查

## Findings

### 高：FileNav 预览路径会让 PDF 先落到 `PDFViewer`
`src/lib/components/chat/FileNav/FilePreview.svelte` 原先在 `filePdfData !== null` 之前就渲染 PDF 预览，导致支持的 PDF/Office 上传文件在这个入口绕过 OnlyOffice，和“常见上传文档/表格/演示/PDF 走 OnlyOffice”的要求不一致。已修正为先走 `canUseOnlyOffice`，再回退到 `PDFViewer`，并复用 `isOnlyOfficePreviewFile` 做路由判断。

### 中：PaddleOCR 图像 materializer 用错了超时参数
`backend/open_webui/retrieval/loaders/paddleocr_vl.py` 里把 `ImageAssetMaterializer` 的 `download_timeout_s` 误传成了 `request_timeout_s`。这会让 `PADDLEOCR_VL_DOWNLOAD_TIMEOUT` 对嵌入图片下载不生效，和异步设置链的语义不一致。已修正为使用真正的 `download_timeout_s`，并在测试里区分 request/download 两个超时。

## 验证命令

- `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest backend/open_webui/test/util/test_paddleocr_vl_loader.py -q`
- `node --input-type=module -e "import { readFileSync } from 'node:fs'; import { parse } from 'svelte/compiler'; parse(readFileSync('src/lib/components/chat/FileNav/FilePreview.svelte', 'utf8')); console.log('FilePreview.svelte parse ok');"`
- `npm run test:frontend -- --run src/lib/utils/filePreviewTypes.test.ts`
- `git diff --check`

## 结论

当前树里我没有看到未修复的 blocking issue。上面两处是实打实的规格偏离，已经在本次审查中直接修掉；其余 `npm run check` 报错主要来自仓库里既有的类型噪音，不是本次改动引入的。

## 2026-06-18 Task D: evidence projection activation consistency

### Scope

- Worktree: `/Users/liusihang/.codex/worktrees/pr7-review-security-fixes/openwebui`
- Branch: `codex/pr7-review-security-fixes`
- Files in scope: `backend/open_webui/retrieval/evidence_projector.py`, `backend/open_webui/test/util/test_evidence_projector.py`
- Constraint: no new `knowledge_evidence_index_job` table; keep using existing retrieval index job/state flow.

### Checkpoints

- Confirmed current `project_evidence_for_knowledge_file()` deactivates active evidence for the target file before text/image projection. Because `deactivate_evidence_for_knowledge_file()` commits immediately, a later projection exception can leave old evidence inactive.
- Planned narrow TDD fix: first add a failing regression proving old active evidence remains active when a projection phase reports failure, then stage new projected evidence inactive and only activate this run plus deactivate superseded rows after all projection phases complete without failures.
- Added focused regression `test_project_knowledge_file_keeps_previous_active_evidence_when_projection_fails`: text projection stages a replacement row, then a document image asset without `storage_uri` makes projection report `failed == 1`; expected behavior is that the pre-existing active text evidence is still the only active evidence.
- RED confirmed with `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false DATABASE_ENABLE_SESSION_SHARING=true uv run pytest backend/open_webui/test/util/test_evidence_projector.py::test_project_knowledge_file_keeps_previous_active_evidence_when_projection_fails -q`: failed at `assert previous_row.is_active is True` because the old row was already inactive.
- Implemented inactive staging plus success-only finalize in `evidence_projector.py`. GREEN confirmed for the regression with the same pytest command: `1 passed, 1 warning`.
- Full focused verification passed with `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false DATABASE_ENABLE_SESSION_SHARING=true uv run --frozen pytest backend/open_webui/test/util/test_evidence_projector.py -q`: `12 passed, 3 warnings`.
- `git diff --check` passed. `uv.lock` was restored after initial non-frozen `uv run` refreshed it; no `uv.lock` diff remains.
