#!/usr/bin/env bash
set -Eeuo pipefail

STAGING_ROOT=/home/aiserver/staging/openwebui-global-system-prompt-7a3638897078
SOURCE_DIR="$STAGING_ROOT/src"
LOG_PATH="$STAGING_ROOT/webui-build.log"
STATUS_PATH="$STAGING_ROOT/webui-build.status"
SOURCE_SHA=7a36388970788b8e03ee169224a5b3f2760b9986
BUILD_HASH=7a3638897078
IMAGE_TAG=open-webui:agentmode-v0102-7a3638897078
SLIM_TAG=${IMAGE_TAG}-slim
BUILDER=owui-agentmode-v0102-mirror
PROXY_URL=http://192.168.2.201:7897

cd "$SOURCE_DIR"
test "$(cat "$STAGING_ROOT/source-sha.txt")" = "$SOURCE_SHA"
test "$(find static/pyodide -type f | wc -l | tr -d ' ')" = 61
test "$(sha256sum static/pyodide/pyodide-lock.json | awk '{print $1}')" = 030c9421fda2605f3930b26d83e2d08ee04ffdd18b759198f02b6d4e84aae465
printf 'state=running\nstarted_at=%s\n' "$(date --iso-8601=seconds)" >"$STATUS_PATH"

docker buildx inspect "$BUILDER" --bootstrap >>"$LOG_PATH" 2>&1

set +e
docker buildx build \
  --builder "$BUILDER" \
  --progress=plain \
  --build-arg BUILD_HASH="$BUILD_HASH" \
  --build-arg USE_SLIM=true \
  --build-arg USE_EXTERNAL_SERVICES_SLIM=false \
  --build-arg ALPINE_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/alpine \
  --build-arg APT_DEBIAN_MIRROR=http://mirrors.tuna.tsinghua.edu.cn/debian \
  --build-arg APT_SECURITY_MIRROR=http://mirrors.tuna.tsinghua.edu.cn/debian-security \
  --build-arg NPM_REGISTRY=https://registry.npmmirror.com \
  --build-arg UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple \
  --build-arg PYODIDE_CACHE_POLICY=prefer-local \
  --build-arg HTTP_PROXY="$PROXY_URL" \
  --build-arg HTTPS_PROXY="$PROXY_URL" \
  --build-arg ALL_PROXY="$PROXY_URL" \
  --build-arg http_proxy="$PROXY_URL" \
  --build-arg https_proxy="$PROXY_URL" \
  --build-arg all_proxy="$PROXY_URL" \
  --label org.opencontainers.image.revision="$SOURCE_SHA" \
  --tag "$IMAGE_TAG" \
  --tag "$SLIM_TAG" \
  --load \
  . >"$LOG_PATH" 2>&1
rc=$?
set -e

printf 'state=finished\nexit_code=%s\nfinished_at=%s\nlog_path=%s\n' \
  "$rc" "$(date --iso-8601=seconds)" "$LOG_PATH" >"$STATUS_PATH"

if [[ "$rc" -eq 0 ]]; then
  docker image inspect "$SLIM_TAG" \
    --format 'id={{.Id}} size={{.Size}} created={{.Created}} revision={{index .Config.Labels "org.opencontainers.image.revision"}}' \
    >"$STAGING_ROOT/webui-image.metadata"
fi

exit "$rc"
