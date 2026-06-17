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
