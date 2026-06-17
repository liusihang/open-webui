# OpenWebUI v0.9.5 External-Services Slim Profile

This repository now supports an explicit external-services slim image profile.
The default Docker build path is unchanged. The slim dependency set is only
selected when `USE_EXTERNAL_SERVICES_SLIM=true` is passed at build time.

## Build

```bash
BUILD_HASH="$(git -C /Users/liusihang/openwebui-v095-imagefix rev-parse --short HEAD)"
docker buildx build \
  --load \
  --build-arg BUILD_HASH="${BUILD_HASH}" \
  --build-arg USE_EXTERNAL_SERVICES_SLIM=true \
  -t "open-webui:v095-external-slim-${BUILD_HASH}" \
  /Users/liusihang/openwebui-v095-imagefix
```

Notes:

- `USE_EXTERNAL_SERVICES_SLIM=true` is intentionally incompatible with
  `USE_CUDA=true`.
- This profile defaults `VECTOR_DB` to `pgvector` when no explicit `VECTOR_DB`
  is provided.
- The slim requirements now pin `typer`, `python-dotenv`, `PyYAML`, `black`,
  and `huggingface-hub` explicitly because the default image had been getting
  some of them transitively from heavier optional dependencies. `black` is kept
  because `/api/v1/utils/code/format` imports it at runtime.
- This profile is meant for deployments that already use external embedding,
  reranking, OCR/document extraction, web loading/search, and image generation.

## Cached PR7 Slim Rebuild

For repeated PR7 slim rebuilds on a remote builder, prefer the repository
script instead of patching the staged Dockerfile by hand:

```bash
scripts/rebuild-pr7-slim-cache.sh \
  --remote aiserver \
  --git-ref HEAD \
  --image-tag "open-webui:pr7-slim-$(git rev-parse --short=10 HEAD)" \
  --build-dir /home/aiserver/staging/openwebui-pr7-slim-build \
  --cache-dir /home/aiserver/.cache/openwebui-pr7-slim-buildx
```

The script keeps the clean `git archive` input, runs `docker buildx build
--load`, imports cache from `<cache-dir>/current`, writes a new
`type=local,mode=max` cache directory, and promotes the `current` pointer only
after a successful build.

When public package fetches fail, prefer explicit mirror build args first:

```bash
scripts/rebuild-pr7-slim-cache.sh \
  --remote aiserver \
  --git-ref HEAD \
  --image-tag "open-webui:pr7-slim-$(git rev-parse --short=10 HEAD)" \
  --build-dir /home/aiserver/staging/openwebui-pr7-slim-build \
  --cache-dir /home/aiserver/.cache/openwebui-pr7-slim-buildx \
  --apt-debian-mirror https://mirrors.tuna.tsinghua.edu.cn/debian \
  --apt-security-mirror https://mirrors.tuna.tsinghua.edu.cn/debian-security \
  --npm-registry https://registry.npmmirror.com \
  --uv-default-index https://pypi.tuna.tsinghua.edu.cn/simple
```

If mirrors are still insufficient, pass the local Clash HTTP proxy with
`--proxy-url`.

## Smoke Test

The slim image no longer ships local Chroma, so smoke tests should run with a
Postgres/pgvector database.

```bash
docker run -d \
  --name open-webui-v095-external-slim-test \
  -p 18080:8080 \
  -e VECTOR_DB=pgvector \
  -e DATABASE_URL=postgresql://postgres:postgres@host.docker.internal:5432/openwebui \
  -e PGVECTOR_DB_URL=postgresql://postgres:postgres@host.docker.internal:5432/openwebui \
  -e REDIS_URL=redis://host.docker.internal:6379/0 \
  -e WEBUI_SECRET_KEY=test-secret-key \
  "open-webui:v095-external-slim-${BUILD_HASH}"
```

Then verify:

```bash
curl -fsS http://127.0.0.1:18080/health
curl -fsS http://127.0.0.1:18080/api/version
docker logs --tail=200 open-webui-v095-external-slim-test
```

Expected:

- `/health` returns JSON with `"status": true`
- `/api/version` returns the app version payload
- startup should not import local Chroma, Torch, Whisper, Playwright, or local
  OCR stacks

## Intentionally Excluded

- Local embedding/reranking models: `torch`, `torchvision`, `torchaudio`,
  `transformers`, `sentence-transformers`, `accelerate`, `sentencepiece`,
  `einops`, `colbert-ai`
- Local STT/TTS/OCR runtimes: `faster-whisper`, `soundfile`, `onnxruntime`,
  `rapidocr-onnxruntime`, `opencv-python-headless`
- Local/unused vector backends: `chromadb`, `weaviate-client`, `pymilvus`,
  `qdrant-client`, `elasticsearch`, `pinecone`, `oracledb`, `opensearch-py`
- Browser loader runtime: `playwright`
- Production-image test/dev tools: `docker`, `pytest`, `pytest-docker`
- Optional parser bundle: `unstructured`

## Functional Boundaries

- `VECTOR_DB=chroma` is unsupported in this profile and now fails early with a
  clear runtime error.
- Local Whisper STT, local transformers TTS, local sentence-transformers
  embedding/reranking, local ColBERT reranking, local Chroma, and local
  Playwright loader are not available in this image.
- Document fallback remains available for common file types through
  `pypdf`, `docx2txt`, `python-pptx`, `pandas`, `openpyxl`, and external
  loader APIs, but formats that rely on `unstructured` (for example some
  `.epub` / `.odt` paths and richer Excel/PPTX parsing) are not bundled in
  this profile.
