#!/usr/bin/env bash
set -Eeuo pipefail

STACK_ROOT=/home/aiserver/staging/openwebui-pr7-eea11194ed-test
SOURCE_SHA=890cf8e0ca52f3f7678fbbca6e1ece913d79d97c
RUNTIME_IMAGE=open-webui-pr7-agentscope-runtime:890cf8e0ca52-phase-marker-clean
RUNTIME_OVERRIDE=compose.agent-runtime-890cf8e0ca52.yaml
OLD_RUNTIME_OVERRIDE=compose.agent-runtime-20d4dddcc130.yaml
WEBUI_OVERRIDE=compose.webui-7a3638897078.yaml
BACKUP_DIR="$STACK_ROOT/backup-before-runtime-890cf8e0ca52-$(date +%Y%m%d-%H%M%S)"
switched=0

cd "$STACK_ROOT"

common_compose=(
  docker compose -p openwebui-pr7
  -f compose.yaml
  -f compose.webui-rebuild-eaff69b0d317.yaml
  -f compose.webui-eaff69-no-migrations.yaml
  -f "$WEBUI_OVERRIDE"
)
target_compose=("${common_compose[@]}" -f "$RUNTIME_OVERRIDE")
rollback_compose=("${common_compose[@]}" -f "$OLD_RUNTIME_OVERRIDE")

wait_healthy() {
  for _ in $(seq 1 48); do
    health=$(docker inspect openwebui-pr7-agentscope-runtime --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}')
    if [[ "$health" = healthy ]]; then
      return 0
    fi
    if [[ "$health" = unhealthy ]]; then
      return 1
    fi
    test "$(docker inspect openwebui-pr7-agentscope-runtime --format '{{.State.Running}}')" = true
    sleep 5
  done
  return 1
}

rollback() {
  local rc=$?
  if [[ "$switched" -eq 1 ]]; then
    "${rollback_compose[@]}" up -d --no-deps --force-recreate agentscope-runtime || true
    wait_healthy || true
  fi
  exit "$rc"
}
trap rollback ERR

runtime_image_id=$(docker image inspect "$RUNTIME_IMAGE" --format '{{.Id}}')
test "$(docker image inspect "$RUNTIME_IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')" = "$SOURCE_SHA"

mkdir -m 700 "$BACKUP_DIR"
cp compose.yaml compose.webui-rebuild-eaff69b0d317.yaml compose.webui-eaff69-no-migrations.yaml \
  "$WEBUI_OVERRIDE" "$RUNTIME_OVERRIDE" "$OLD_RUNTIME_OVERRIDE" "$BACKUP_DIR/"
docker inspect openwebui-pr7-agentscope-runtime >"$BACKUP_DIR/runtime.before.json"

before_webui=$(docker inspect open-webui-pr7 --format '{{.Id}}|{{.Image}}|{{.RestartCount}}')
before_main=$(docker inspect open-webui --format '{{.Id}}|{{.Image}}|{{.RestartCount}}')
before_db=$(docker inspect openwebui-pr7-db --format '{{.Id}}')
before_redis=$(docker inspect openwebui-pr7-redis --format '{{.Id}}')
before_terminals=$(docker inspect open-webui-pr7-terminals --format '{{.Id}}')

"${target_compose[@]}" config >"$BACKUP_DIR/compose.target.resolved.yaml"
test "$("${target_compose[@]}" config --images | grep -Fx "$RUNTIME_IMAGE" | wc -l | tr -d ' ')" = 1

switched=1
"${target_compose[@]}" up -d --no-deps --force-recreate agentscope-runtime
wait_healthy

test "$(docker inspect openwebui-pr7-agentscope-runtime --format '{{.Image}}')" = "$runtime_image_id"
test "$(docker inspect openwebui-pr7-agentscope-runtime --format '{{.RestartCount}}')" = 0
test "$(docker inspect openwebui-pr7-agentscope-runtime --format '{{.State.OOMKilled}}')" = false
test "$(docker inspect open-webui-pr7 --format '{{.Id}}|{{.Image}}|{{.RestartCount}}')" = "$before_webui"
test "$(docker inspect openwebui-pr7-db --format '{{.Id}}')" = "$before_db"
test "$(docker inspect openwebui-pr7-redis --format '{{.Id}}')" = "$before_redis"
test "$(docker inspect open-webui-pr7-terminals --format '{{.Id}}')" = "$before_terminals"
test "$(docker inspect open-webui --format '{{.Id}}|{{.Image}}|{{.RestartCount}}')" = "$before_main"

docker inspect openwebui-pr7-agentscope-runtime >"$BACKUP_DIR/runtime.after.json"
find "$BACKUP_DIR" -maxdepth 1 -type f ! -name SHA256SUMS -print0 \
  | sort -z \
  | xargs -0 sha256sum >"$BACKUP_DIR/SHA256SUMS"

switched=0
trap - ERR
printf 'backup=%s\n' "$BACKUP_DIR"
docker inspect openwebui-pr7-agentscope-runtime --format 'runtime_id={{.Id}} image_id={{.Image}} health={{.State.Health.Status}} restarts={{.RestartCount}} oom={{.State.OOMKilled}}'
