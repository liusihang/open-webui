#!/usr/bin/env bash
set -Eeuo pipefail

STAGING_ROOT=/home/aiserver/staging/openwebui-global-system-prompt-7a3638897078
SOURCE_DIR="$STAGING_ROOT/src"
LOG_PATH="$STAGING_ROOT/runtime-build.log"
STATUS_PATH="$STAGING_ROOT/runtime-build.status"
IMAGE_TAG=open-webui-pr7-agentscope-runtime:7a3638897078-global-system-prompt
SOURCE_SHA=7a36388970788b8e03ee169224a5b3f2760b9986

cd "$SOURCE_DIR"
printf 'state=running\nstarted_at=%s\n' "$(date --iso-8601=seconds)" >"$STATUS_PATH"

set +e
docker build \
  --progress=plain \
  --file "$STAGING_ROOT/Dockerfile.runtime-overlay" \
  --build-arg SOURCE_SHA="$SOURCE_SHA" \
  --tag "$IMAGE_TAG" \
  . >"$LOG_PATH" 2>&1
rc=$?
set -e

printf 'state=finished\nexit_code=%s\nfinished_at=%s\nlog_path=%s\n' \
  "$rc" "$(date --iso-8601=seconds)" "$LOG_PATH" >"$STATUS_PATH"

if [[ "$rc" -eq 0 ]]; then
  docker image inspect "$IMAGE_TAG" \
    --format 'id={{.Id}} size={{.Size}} created={{.Created}} revision={{index .Config.Labels "org.opencontainers.image.revision"}}' \
    >"$STAGING_ROOT/runtime-image.metadata"
fi

exit "$rc"
