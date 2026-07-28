#!/usr/bin/env bash
set -euo pipefail

BUILD_DIR=${BUILD_DIR:?BUILD_DIR is required}
FULL_SHA=${FULL_SHA:?FULL_SHA is required}
BUILD_HASH=${BUILD_HASH:?BUILD_HASH is required}
IMAGE_TAG=${IMAGE_TAG:?IMAGE_TAG is required}
PROXY_URL=${PROXY_URL:-http://192.168.2.201:7897}
BUILDER=${BUILDER:-codex-pr7-slim-cache}
CACHE_FROM=${CACHE_FROM:-/home/aiserver/.cache/openwebui-pr7-slim-buildx/current}
LOG_FILE=${LOG_FILE:-$BUILD_DIR/docker-build-$BUILD_HASH.log}
PYODIDE_CACHE_POLICY=${PYODIDE_CACHE_POLICY:-prefer-local}

printf 'source_sha=%s\n' "$FULL_SHA"
printf 'build_hash=%s\n' "$BUILD_HASH"
printf 'image_tag=%s\n' "$IMAGE_TAG"
printf 'builder=%s\n' "$BUILDER"
printf 'cache_from=%s\n' "$CACHE_FROM"
printf 'pyodide_cache_policy=%s\n' "$PYODIDE_CACHE_POLICY"

docker buildx inspect "$BUILDER" --bootstrap >/dev/null

set +e
docker buildx build \
  --builder "$BUILDER" \
  --progress=plain \
  --build-arg BUILD_HASH="$BUILD_HASH" \
  --build-arg USE_SLIM=true \
  --build-arg USE_EXTERNAL_SERVICES_SLIM=true \
  --build-arg ALPINE_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/alpine \
  --build-arg APT_DEBIAN_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian \
  --build-arg APT_SECURITY_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian-security \
  --build-arg NPM_REGISTRY=https://registry.npmmirror.com \
  --build-arg UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple \
  --build-arg PYODIDE_CACHE_POLICY="$PYODIDE_CACHE_POLICY" \
  --build-arg HTTP_PROXY="$PROXY_URL" \
  --build-arg HTTPS_PROXY="$PROXY_URL" \
  --build-arg NO_PROXY=127.0.0.1,localhost \
  --cache-from "type=local,src=$CACHE_FROM" \
  --label "org.opencontainers.image.revision=$FULL_SHA" \
  --tag "$IMAGE_TAG" \
  --load \
  "$BUILD_DIR/src" 2>&1 | tee "$LOG_FILE"
status=${PIPESTATUS[0]}
set -e

if [[ $status -ne 0 ]]; then
  exit "$status"
fi

docker image inspect "$IMAGE_TAG" \
  --format 'id={{.Id}} size={{.Size}} created={{.Created}} revision={{index .Config.Labels "org.opencontainers.image.revision"}}'
