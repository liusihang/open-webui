#!/usr/bin/env bash
set -Eeuo pipefail

STAGING_ROOT=/home/aiserver/staging/openwebui-durable-agent-7dc6afd81f2b
SOURCE_DIR="$STAGING_ROOT/src"
LOG_PATH="$STAGING_ROOT/runtime-build.log"
STATUS_PATH="$STAGING_ROOT/runtime-build.status"
IMAGE_TAG=open-webui-pr7-agentscope-runtime:7dc6afd81f2b-durable-execution
SOURCE_SHA=7dc6afd81f2b3828b612452de70425c21d0d3d58

cd "$SOURCE_DIR"
test "$(cat "$STAGING_ROOT/source-sha.txt")" = "$SOURCE_SHA"
printf 'state=running\nstarted_at=%s\n' "$(date --iso-8601=seconds)" >"$STATUS_PATH"

set +e
docker build \
  --progress=plain \
  --file "$STAGING_ROOT/Dockerfile.runtime-overlay-7dc6afd81f2b" \
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
