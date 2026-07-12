#!/usr/bin/env bash
set -Eeuo pipefail

STACK_ROOT=/home/aiserver/staging/openwebui-pr7-eea11194ed-test
SOURCE_SHA=2a0c4c9888848abd38191fecbe7715a687eab165
WEBUI_IMAGE=open-webui:agentmode-v0102-2a0c4c988884-slim
RUNTIME_IMAGE=open-webui-pr7-agentscope-runtime:2a0c4c988884-final-privacy-cancel
WEBUI_OVERRIDE=compose.webui-2a0c4c988884.yaml
RUNTIME_OVERRIDE=compose.agent-runtime-2a0c4c988884.yaml
OLD_WEBUI_OVERRIDE=compose.webui-7dc6afd81f2b.yaml
OLD_RUNTIME_OVERRIDE=compose.agent-runtime-7dc6afd81f2b.yaml
MIGRATION_HEAD=e7f8a9b0c1d2
BACKUP_DIR="$STACK_ROOT/backup-before-2a0c4c988884-$(date +%Y%m%d-%H%M%S)"
STATUS_PATH="$STACK_ROOT/switch-pr7-2a0c4c988884.status"
switched=0

cd "$STACK_ROOT"
printf 'state=running\nstarted_at=%s\n' "$(date --iso-8601=seconds)" >"$STATUS_PATH"

common_compose=(
  docker compose -p openwebui-pr7
  -f compose.yaml
  -f compose.webui-rebuild-eaff69b0d317.yaml
  -f compose.webui-eaff69-no-migrations.yaml
)
target_compose=("${common_compose[@]}" -f "$WEBUI_OVERRIDE" -f "$RUNTIME_OVERRIDE")
rollback_compose=("${common_compose[@]}" -f "$OLD_WEBUI_OVERRIDE" -f "$OLD_RUNTIME_OVERRIDE")

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

container_env() {
  local name=$1
  docker inspect open-webui-pr7 --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | sed -n "s/^${name}=//p"
}

run_alembic() {
  docker run --rm \
    --network openwebui-pr7_default \
    --env DATABASE_URL="$(container_env DATABASE_URL)" \
    --env WEBUI_SECRET_KEY="$(container_env WEBUI_SECRET_KEY)" \
    "$WEBUI_IMAGE" \
    sh -lc 'cd /app/backend/open_webui && alembic -c alembic.ini current'
}

rollback() {
  local rc=$?
  trap - ERR
  if [[ "$switched" -eq 1 ]]; then
    "${rollback_compose[@]}" up -d --no-deps --force-recreate open-webui-pr7 agentscope-runtime || true
    wait_healthy open-webui-pr7 || true
    wait_healthy openwebui-pr7-agentscope-runtime || true
  fi
  printf 'state=rolled_back\nexit_code=%s\nfinished_at=%s\nbackup=%s\n' \
    "$rc" "$(date --iso-8601=seconds)" "$BACKUP_DIR" >"$STATUS_PATH"
  exit "$rc"
}
trap rollback ERR

webui_image_id=$(docker image inspect "$WEBUI_IMAGE" --format '{{.Id}}')
runtime_image_id=$(docker image inspect "$RUNTIME_IMAGE" --format '{{.Id}}')
test "$(docker image inspect "$WEBUI_IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')" = "$SOURCE_SHA"
test "$(docker image inspect "$RUNTIME_IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')" = "$SOURCE_SHA"

mkdir -m 700 "$BACKUP_DIR"
cp compose.yaml compose.webui-rebuild-eaff69b0d317.yaml compose.webui-eaff69-no-migrations.yaml \
  "$WEBUI_OVERRIDE" "$RUNTIME_OVERRIDE" "$OLD_WEBUI_OVERRIDE" "$OLD_RUNTIME_OVERRIDE" "$BACKUP_DIR/"
docker inspect open-webui-pr7 openwebui-pr7-agentscope-runtime >"$BACKUP_DIR/target-containers.before.json"

before_main=$(docker inspect open-webui --format '{{.Id}}|{{.Image}}|{{.RestartCount}}')
before_db=$(docker inspect openwebui-pr7-db --format '{{.Id}}')
before_redis=$(docker inspect openwebui-pr7-redis --format '{{.Id}}')
before_terminals=$(docker inspect open-webui-pr7-terminals --format '{{.Id}}')

"${target_compose[@]}" config >"$BACKUP_DIR/compose.target.resolved.yaml"
test "$("${target_compose[@]}" config --images | grep -Fx "$WEBUI_IMAGE" | wc -l | tr -d ' ')" = 1
test "$("${target_compose[@]}" config --images | grep -Fx "$RUNTIME_IMAGE" | wc -l | tr -d ' ')" = 1
grep -Fq 'agentscope_runtime.launcher' "$BACKUP_DIR/compose.target.resolved.yaml"
grep -Fq 'AGENT_RUNTIME_STATE_PATH: /var/lib/agentscope-runtime/runtime-state.sqlite3' "$BACKUP_DIR/compose.target.resolved.yaml"
grep -Fq 'source: agentscope-runtime-state' "$BACKUP_DIR/compose.target.resolved.yaml"
grep -Fq 'name: openwebui-pr7-agentscope-runtime-state' "$BACKUP_DIR/compose.target.resolved.yaml"
test "$(run_alembic 2>&1 | tail -n 1 | awk '{print $1}')" = "$MIGRATION_HEAD"

switched=1
"${target_compose[@]}" up -d --no-deps --force-recreate open-webui-pr7 agentscope-runtime
wait_healthy open-webui-pr7
wait_healthy openwebui-pr7-agentscope-runtime

test "$(docker inspect open-webui-pr7 --format '{{.Image}}')" = "$webui_image_id"
test "$(docker inspect openwebui-pr7-agentscope-runtime --format '{{.Image}}')" = "$runtime_image_id"
test "$(docker inspect open-webui-pr7 --format '{{.RestartCount}}')" = 0
test "$(docker inspect openwebui-pr7-agentscope-runtime --format '{{.RestartCount}}')" = 0
test "$(docker inspect open-webui-pr7 --format '{{.State.OOMKilled}}')" = false
test "$(docker inspect openwebui-pr7-agentscope-runtime --format '{{.State.OOMKilled}}')" = false
test "$(docker inspect openwebui-pr7-db --format '{{.Id}}')" = "$before_db"
test "$(docker inspect openwebui-pr7-redis --format '{{.Id}}')" = "$before_redis"
test "$(docker inspect open-webui-pr7-terminals --format '{{.Id}}')" = "$before_terminals"
test "$(docker inspect open-webui --format '{{.Id}}|{{.Image}}|{{.RestartCount}}')" = "$before_main"

curl --noproxy '*' -fsS --max-time 15 http://127.0.0.1:18085/health >"$BACKUP_DIR/health.after.json"
docker inspect open-webui-pr7 openwebui-pr7-agentscope-runtime >"$BACKUP_DIR/target-containers.after.json"
find "$BACKUP_DIR" -maxdepth 1 -type f ! -name SHA256SUMS -print0 \
  | sort -z \
  | xargs -0 sha256sum >"$BACKUP_DIR/SHA256SUMS"

switched=0
trap - ERR
printf 'state=finished\nexit_code=0\nfinished_at=%s\nbackup=%s\n' \
  "$(date --iso-8601=seconds)" "$BACKUP_DIR" >"$STATUS_PATH"
printf 'backup=%s\n' "$BACKUP_DIR"
docker inspect open-webui-pr7 --format 'webui_id={{.Id}} image_id={{.Image}} health={{.State.Health.Status}} restarts={{.RestartCount}} oom={{.State.OOMKilled}}'
docker inspect openwebui-pr7-agentscope-runtime --format 'runtime_id={{.Id}} image_id={{.Image}} health={{.State.Health.Status}} restarts={{.RestartCount}} oom={{.State.OOMKilled}}'
