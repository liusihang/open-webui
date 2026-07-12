#!/usr/bin/env bash
set -Eeuo pipefail

STACK_ROOT=/home/aiserver/staging/openwebui-pr7-eea11194ed-test
SOURCE_SHA=ed6b280f60d248f2c909ae8e67b243c7bb16fe67
RUNTIME_IMAGE=open-webui-pr7-agentscope-runtime:ed6b280f60d2-response-envelopes
WEBUI_OVERRIDE=compose.webui-2a0c4c988884.yaml
RUNTIME_OVERRIDE=compose.agent-runtime-ed6b280f60d2.yaml
OLD_RUNTIME_OVERRIDE=compose.agent-runtime-2a0c4c988884.yaml
BACKUP_DIR="$STACK_ROOT/backup-before-runtime-ed6b280f60d2-$(date +%Y%m%d-%H%M%S)"
STATUS_PATH="$STACK_ROOT/switch-runtime-ed6b280f60d2.status"
switched=0

cd "$STACK_ROOT"
printf 'state=running\nstarted_at=%s\n' "$(date --iso-8601=seconds)" >"$STATUS_PATH"

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
  local container=$1
  for _ in $(seq 1 72); do
    local health
    health=$(docker inspect "$container" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}')
    if [[ "$health" = healthy ]]; then
      return 0
    fi
    if [[ "$(docker inspect "$container" --format '{{.State.Running}}')" != true ]]; then
      return 1
    fi
    sleep 5
  done
  return 1
}

rollback() {
  local rc=$?
  trap - ERR
  if [[ "$switched" -eq 1 ]]; then
    "${rollback_compose[@]}" up -d --no-deps --force-recreate agentscope-runtime || true
    wait_healthy openwebui-pr7-agentscope-runtime || true
  fi
  printf 'state=rolled_back\nexit_code=%s\nfinished_at=%s\nbackup=%s\n' \
    "$rc" "$(date --iso-8601=seconds)" "$BACKUP_DIR" >"$STATUS_PATH"
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
before_db=$(docker inspect openwebui-pr7-db --format '{{.Id}}')
before_redis=$(docker inspect openwebui-pr7-redis --format '{{.Id}}')
before_terminals=$(docker inspect open-webui-pr7-terminals --format '{{.Id}}')
before_main=$(docker inspect open-webui --format '{{.Id}}|{{.Image}}|{{.RestartCount}}')
before_state_volume=$(docker volume inspect openwebui-pr7-agentscope-runtime-state --format '{{.Name}}|{{.Mountpoint}}')

"${target_compose[@]}" config >"$BACKUP_DIR/compose.target.resolved.yaml"
test "$("${target_compose[@]}" config --images | grep -Fx "$RUNTIME_IMAGE" | wc -l | tr -d ' ')" = 1
grep -Fq 'agentscope_runtime.launcher' "$BACKUP_DIR/compose.target.resolved.yaml"
grep -Fq 'AGENT_RUNTIME_STATE_PATH: /var/lib/agentscope-runtime/runtime-state.sqlite3' "$BACKUP_DIR/compose.target.resolved.yaml"
grep -Fq 'name: openwebui-pr7-agentscope-runtime-state' "$BACKUP_DIR/compose.target.resolved.yaml"

switched=1
"${target_compose[@]}" up -d --no-deps --force-recreate agentscope-runtime
wait_healthy openwebui-pr7-agentscope-runtime

test "$(docker inspect openwebui-pr7-agentscope-runtime --format '{{.Image}}')" = "$runtime_image_id"
test "$(docker inspect openwebui-pr7-agentscope-runtime --format '{{.RestartCount}}')" = 0
test "$(docker inspect openwebui-pr7-agentscope-runtime --format '{{.State.OOMKilled}}')" = false
test "$(docker inspect open-webui-pr7 --format '{{.Id}}|{{.Image}}|{{.RestartCount}}')" = "$before_webui"
test "$(docker inspect openwebui-pr7-db --format '{{.Id}}')" = "$before_db"
test "$(docker inspect openwebui-pr7-redis --format '{{.Id}}')" = "$before_redis"
test "$(docker inspect open-webui-pr7-terminals --format '{{.Id}}')" = "$before_terminals"
test "$(docker inspect open-webui --format '{{.Id}}|{{.Image}}|{{.RestartCount}}')" = "$before_main"
test "$(docker volume inspect openwebui-pr7-agentscope-runtime-state --format '{{.Name}}|{{.Mountpoint}}')" = "$before_state_volume"

docker exec openwebui-pr7-agentscope-runtime python -c \
  'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=10).read().decode())' \
  >"$BACKUP_DIR/runtime-health.after.json"
curl --noproxy '*' -fsS --max-time 15 http://127.0.0.1:18085/health \
  >"$BACKUP_DIR/webui-health.after.json"
docker inspect openwebui-pr7-agentscope-runtime >"$BACKUP_DIR/runtime.after.json"
find "$BACKUP_DIR" -maxdepth 1 -type f ! -name SHA256SUMS -print0 \
  | sort -z \
  | xargs -0 sha256sum >"$BACKUP_DIR/SHA256SUMS"

switched=0
trap - ERR
printf 'state=finished\nexit_code=0\nfinished_at=%s\nbackup=%s\n' \
  "$(date --iso-8601=seconds)" "$BACKUP_DIR" >"$STATUS_PATH"
printf 'backup=%s\n' "$BACKUP_DIR"
docker inspect openwebui-pr7-agentscope-runtime \
  --format 'runtime_id={{.Id}} image_id={{.Image}} health={{.State.Health.Status}} restarts={{.RestartCount}} oom={{.State.OOMKilled}}'
