#!/usr/bin/env bash
set -Eeuo pipefail

STACK_ROOT=/home/aiserver/staging/openwebui-pr7-eea11194ed-test
SOURCE_SHA=742f686182d6b1a885889fca803ea31b766bfda1
RUNTIME_IMAGE=open-webui-pr7-agentscope-runtime:742f686182-true-final-stream
RUNTIME_IMAGE_ID=sha256:f7396ba23e49f934216ba8fc4b38c695b7f639722d852b44234769c66ca7f6e9
OLD_RUNTIME_IMAGE=open-webui-pr7-agentscope-runtime:4a4e43e206-live-hardening
OLD_RUNTIME_IMAGE_ID=sha256:3ce6c0481aa575c856d42fd90587695408093ef98667e0e2d50fc9d29ca2bb22
WEBUI_OVERRIDE=compose.webui-4a4e43e206.yaml
RUNTIME_OVERRIDE=compose.agent-runtime-742f686182.yaml
OLD_RUNTIME_OVERRIDE=compose.agent-runtime-4a4e43e206.yaml
RUNTIME_STATE_VOLUME=openwebui-pr7-agentscope-runtime-state
RECENT_ACTIVE_WINDOW_NS=600000000000
BACKUP_DIR="$STACK_ROOT/backup-before-runtime-742f686182-$(date +%Y%m%d-%H%M%S)"
STATUS_PATH="$STACK_ROOT/switch-runtime-742f686182.status"
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
    local running health
    running=$(docker inspect "$container" --format '{{.State.Running}}')
    health=$(docker inspect "$container" | jq -r '.[0].State.Health.Status // "none"')
    if [[ "$health" = healthy ]]; then
      return 0
    fi
    if [[ "$running" != true ]]; then
      return 1
    fi
    sleep 5
  done
  return 1
}

runtime_schema_version() {
  local image=$1
  docker run --rm --network none \
    -v "$RUNTIME_STATE_VOLUME:/state" \
    --entrypoint /service/.venv/bin/python \
    "$image" -c '
import sqlite3
connection = sqlite3.connect("/state/runtime-state.sqlite3")
print(connection.execute("SELECT value FROM runtime_schema WHERE key=?", ("schema_version",)).fetchone()[0])
connection.close()
'
}

backup_runtime_state() {
  docker run --rm --network none \
    -v "$RUNTIME_STATE_VOLUME:/state" \
    -v "$BACKUP_DIR:/backup" \
    --entrypoint /service/.venv/bin/python \
    "$OLD_RUNTIME_IMAGE" -c '
import sqlite3
source = sqlite3.connect("/state/runtime-state.sqlite3")
target = sqlite3.connect("/backup/runtime-state.before.sqlite3")
source.backup(target)
target.close()
source.close()
'
}

rollback() {
  local rc=$?
  local rollback_failed=0
  trap - ERR
  set +e
  if [[ "$switched" -eq 1 ]]; then
    "${rollback_compose[@]}" up -d --no-deps --force-recreate agentscope-runtime \
      || rollback_failed=1
    wait_healthy openwebui-pr7-agentscope-runtime || rollback_failed=1
  fi
  test "$(docker inspect openwebui-pr7-agentscope-runtime --format '{{.Image}}')" = "$OLD_RUNTIME_IMAGE_ID" \
    || rollback_failed=1
  test "$(runtime_schema_version "$OLD_RUNTIME_IMAGE")" = 2 || rollback_failed=1
  if [[ "$rollback_failed" -eq 0 ]]; then
    printf 'state=rolled_back\nexit_code=%s\nfinished_at=%s\nbackup=%s\n' \
      "$rc" "$(date --iso-8601=seconds)" "$BACKUP_DIR" >"$STATUS_PATH"
  else
    printf 'state=rollback_failed\nexit_code=%s\nfinished_at=%s\nbackup=%s\n' \
      "$rc" "$(date --iso-8601=seconds)" "$BACKUP_DIR" >"$STATUS_PATH"
  fi
  exit "$rc"
}
trap rollback ERR

test "$(docker image inspect "$RUNTIME_IMAGE" --format '{{.Id}}')" = "$RUNTIME_IMAGE_ID"
test "$(docker image inspect "$RUNTIME_IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')" = "$SOURCE_SHA"
test "$(docker image inspect "$OLD_RUNTIME_IMAGE" --format '{{.Id}}')" = "$OLD_RUNTIME_IMAGE_ID"

mkdir -m 700 "$BACKUP_DIR"
cp compose.yaml compose.webui-rebuild-eaff69b0d317.yaml compose.webui-eaff69-no-migrations.yaml \
  "$WEBUI_OVERRIDE" "$RUNTIME_OVERRIDE" "$OLD_RUNTIME_OVERRIDE" "$BACKUP_DIR/"
docker inspect openwebui-pr7-agentscope-runtime >"$BACKUP_DIR/runtime.before.json"
docker exec openwebui-pr7-db psql -U webui_pr7 -d webui_pr7 -AtF '|' \
  -c "SELECT id,state,updated_at FROM agent_run WHERE state IN ('queued','running','waiting_approval','waiting_user_input','finalizing') ORDER BY updated_at DESC" \
  >"$BACKUP_DIR/active-runs.before.tsv"

recent_active=$(docker exec openwebui-pr7-db psql -U webui_pr7 -d webui_pr7 -Atc \
  "SELECT count(*) FROM agent_run WHERE state IN ('queued','running','waiting_approval','waiting_user_input','finalizing') AND updated_at >= CAST(EXTRACT(EPOCH FROM clock_timestamp()) * 1000000000 AS BIGINT) - $RECENT_ACTIVE_WINDOW_NS")
test "$recent_active" = 0

before_webui=$(docker inspect open-webui-pr7 --format '{{.Id}}|{{.Image}}|{{.RestartCount}}')
before_db=$(docker inspect openwebui-pr7-db --format '{{.Id}}')
before_redis=$(docker inspect openwebui-pr7-redis --format '{{.Id}}')
before_terminals=$(docker inspect open-webui-pr7-terminals --format '{{.Id}}')
before_main=$(docker inspect open-webui --format '{{.Id}}|{{.Image}}|{{.RestartCount}}')
before_state_volume=$(docker volume inspect "$RUNTIME_STATE_VOLUME" --format '{{.Name}}|{{.Mountpoint}}')

"${target_compose[@]}" config >"$BACKUP_DIR/compose.target.resolved.yaml"
test "$("${target_compose[@]}" config --images | grep -Fx "$RUNTIME_IMAGE" | wc -l | tr -d ' ')" = 1
grep -Fq 'agentscope_runtime.launcher' "$BACKUP_DIR/compose.target.resolved.yaml"
grep -Fq 'AGENT_RUNTIME_STATE_PATH: /var/lib/agentscope-runtime/runtime-state.sqlite3' "$BACKUP_DIR/compose.target.resolved.yaml"
grep -Fq 'UVICORN_WORKERS: "1"' "$BACKUP_DIR/compose.target.resolved.yaml"
grep -Fq 'name: openwebui-pr7-agentscope-runtime-state' "$BACKUP_DIR/compose.target.resolved.yaml"
test "$(runtime_schema_version "$OLD_RUNTIME_IMAGE")" = 2
test "$(runtime_schema_version "$RUNTIME_IMAGE")" = 2
backup_runtime_state

switched=1
"${target_compose[@]}" up -d --no-deps --force-recreate agentscope-runtime
wait_healthy openwebui-pr7-agentscope-runtime

test "$(docker inspect openwebui-pr7-agentscope-runtime --format '{{.Image}}')" = "$RUNTIME_IMAGE_ID"
test "$(docker inspect openwebui-pr7-agentscope-runtime --format '{{.RestartCount}}')" = 0
test "$(docker inspect openwebui-pr7-agentscope-runtime --format '{{.State.OOMKilled}}')" = false
test "$(docker inspect open-webui-pr7 --format '{{.Id}}|{{.Image}}|{{.RestartCount}}')" = "$before_webui"
test "$(docker inspect openwebui-pr7-db --format '{{.Id}}')" = "$before_db"
test "$(docker inspect openwebui-pr7-redis --format '{{.Id}}')" = "$before_redis"
test "$(docker inspect open-webui-pr7-terminals --format '{{.Id}}')" = "$before_terminals"
test "$(docker inspect open-webui --format '{{.Id}}|{{.Image}}|{{.RestartCount}}')" = "$before_main"
test "$(docker volume inspect "$RUNTIME_STATE_VOLUME" --format '{{.Name}}|{{.Mountpoint}}')" = "$before_state_volume"
test "$(runtime_schema_version "$RUNTIME_IMAGE")" = 2

docker exec openwebui-pr7-agentscope-runtime /service/.venv/bin/python -c \
  'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=10).read().decode())' \
  >"$BACKUP_DIR/runtime-health.after.json"
curl --noproxy '*' -fsS --max-time 15 http://127.0.0.1:18085/health \
  >"$BACKUP_DIR/webui-health.after.json"
docker inspect openwebui-pr7-agentscope-runtime >"$BACKUP_DIR/runtime.after.json"
find "$BACKUP_DIR" -maxdepth 1 -type f ! -name SHA256SUMS -print0 \
  | sort -z | xargs -0 sha256sum >"$BACKUP_DIR/SHA256SUMS"

switched=0
trap - ERR
printf 'state=finished\nexit_code=0\nfinished_at=%s\nbackup=%s\n' \
  "$(date --iso-8601=seconds)" "$BACKUP_DIR" >"$STATUS_PATH"
printf 'backup=%s\n' "$BACKUP_DIR"
docker inspect openwebui-pr7-agentscope-runtime \
  --format 'runtime_id={{.Id}} image_id={{.Image}} health={{.State.Health.Status}} restarts={{.RestartCount}} oom={{.State.OOMKilled}}'
