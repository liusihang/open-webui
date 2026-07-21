#!/usr/bin/env bash
set -Eeuo pipefail

STAGING_ROOT=/home/aiserver/staging/openwebui-pr7-runtime-742f686182
SOURCE_DIR="$STAGING_ROOT/src"
LOG_PATH="$STAGING_ROOT/runtime-build.log"
STATUS_PATH="$STAGING_ROOT/runtime-build.status"
IMAGE_TAG=open-webui-pr7-agentscope-runtime:742f686182-true-final-stream
SOURCE_SHA=742f686182d6b1a885889fca803ea31b766bfda1
CONTAINERS=(
  open-webui-pr7
  openwebui-pr7-agentscope-runtime
  openwebui-pr7-db
  openwebui-pr7-redis
  open-webui-pr7-terminals
  open-webui
)

cd "$SOURCE_DIR"
test "$(<"$STAGING_ROOT/source-sha-742f686182.txt")" = "$SOURCE_SHA"
docker inspect "${CONTAINERS[@]}" --format '{{.Name}} {{.Id}}' \
  >"$STAGING_ROOT/containers.before"
printf 'state=running\nstarted_at=%s\n' "$(date --iso-8601=seconds)" \
  >"$STATUS_PATH"

set +e
docker build \
  --progress=plain \
  --file "$STAGING_ROOT/Dockerfile.runtime-overlay-742f686182" \
  --build-arg SOURCE_SHA="$SOURCE_SHA" \
  --tag "$IMAGE_TAG" \
  . >"$LOG_PATH" 2>&1
rc=$?
set -e

if [[ "$rc" -eq 0 ]]; then
  test "$(docker image inspect "$IMAGE_TAG" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')" = "$SOURCE_SHA"
  source_hash=$(sha256sum services/agentscope-runtime/agentscope_runtime/agentscope_bridge.py | awk '{print $1}')
  image_hash=$(docker run --rm --network none --entrypoint sha256sum "$IMAGE_TAG" \
    /service/agentscope_runtime/agentscope_bridge.py | awk '{print $1}')
  test "$image_hash" = "$source_hash"
  docker run --rm --network none \
    --env AGENT_RUNTIME_STATE_PATH=/tmp/runtime-state.sqlite3 \
    --entrypoint /service/.venv/bin/python \
    "$IMAGE_TAG" -c \
    'from agentscope_runtime.agentscope_bridge import _declared_final_prefix_is_safe_to_stream as safe; assert safe(["Final answer."]); assert not safe(["<thinking>"])'
  docker image inspect "$IMAGE_TAG" \
    --format 'id={{.Id}} size={{.Size}} created={{.Created}} revision={{index .Config.Labels "org.opencontainers.image.revision"}}' \
    >"$STAGING_ROOT/runtime-image.metadata"
fi

docker inspect "${CONTAINERS[@]}" --format '{{.Name}} {{.Id}}' \
  >"$STAGING_ROOT/containers.after"
cmp "$STAGING_ROOT/containers.before" "$STAGING_ROOT/containers.after"

printf 'state=finished\nexit_code=%s\nfinished_at=%s\nlog_path=%s\n' \
  "$rc" "$(date --iso-8601=seconds)" "$LOG_PATH" >"$STATUS_PATH"
exit "$rc"
