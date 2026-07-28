#!/usr/bin/env bash
set -Eeuo pipefail

STACK_ROOT=/home/aiserver/staging/openwebui-pr7-eea11194ed-test
SOURCE_SHA=4a4e43e2063484d9159e70b0c072866ec55286bd
WEBUI_IMAGE=open-webui:agentmode-v0102-4a4e43e206-slim
RUNTIME_IMAGE=open-webui-pr7-agentscope-runtime:4a4e43e206-live-hardening
WEBUI_IMAGE_ID=sha256:fd6145b041f28269a0766e8f0f1ab91653a998745290041c43ef314c2456c8c4
RUNTIME_IMAGE_ID=sha256:3ce6c0481aa575c856d42fd90587695408093ef98667e0e2d50fc9d29ca2bb22
OLD_WEBUI_IMAGE=open-webui:agentmode-v0102-2a0c4c988884-slim
OLD_RUNTIME_IMAGE=open-webui-pr7-agentscope-runtime:ed6b280f60d2-response-envelopes
OLD_WEBUI_IMAGE_ID=sha256:a79893e01a0ed470d75ee0981e7a7bc4ffd830359bfb89d52ad99ece455c0b95
OLD_RUNTIME_IMAGE_ID=sha256:85ee34a41cda68580d4ffb1e0a6b7acafb69444fb3bc80fa151887f5e78d5aa6
WEBUI_OVERRIDE=compose.webui-4a4e43e206.yaml
RUNTIME_OVERRIDE=compose.agent-runtime-4a4e43e206.yaml
OLD_WEBUI_OVERRIDE=compose.webui-2a0c4c988884.yaml
OLD_RUNTIME_OVERRIDE=compose.agent-runtime-ed6b280f60d2.yaml
RUNTIME_STATE_VOLUME=openwebui-pr7-agentscope-runtime-state
OLD_MIGRATION_HEAD=e7f8a9b0c1d2
NEW_MIGRATION_HEAD=f8a9b0c1d2e3
RECENT_ACTIVE_WINDOW_NS=600000000000
BACKUP_DIR="$STACK_ROOT/backup-before-4a4e43e206-$(date +%Y%m%d-%H%M%S)"
STATUS_PATH="$STACK_ROOT/switch-pr7-4a4e43e206.status"
migrated=0
runtime_state_may_change=0

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
  local attempts=${2:-72}
  for _ in $(seq 1 "$attempts"); do
    local running health
    running=$(docker inspect "$container" --format '{{.State.Running}}')
    health=$(docker inspect "$container" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}')
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

container_env() {
  local name=$1
  docker inspect open-webui-pr7 --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | sed -n "s/^${name}=//p"
}

run_alembic() {
  local action=$1
  docker run --rm \
    --network openwebui-pr7_default \
    --env DATABASE_URL="$(container_env DATABASE_URL)" \
    --env WEBUI_SECRET_KEY="$(container_env WEBUI_SECRET_KEY)" \
    --env ENABLE_DB_MIGRATIONS=false \
    "$WEBUI_IMAGE" \
    sh -lc "cd /app/backend/open_webui && alembic -c alembic.ini $action"
}

alembic_current() {
  run_alembic current 2>&1 | tail -n 1 | awk '{print $1}'
}

probe_target_config_schema() {
  docker run --rm \
    --network openwebui-pr7_default \
    --env DATABASE_URL="$(container_env DATABASE_URL)" \
    --env WEBUI_SECRET_KEY="$(container_env WEBUI_SECRET_KEY)" \
    --env ENABLE_DB_MIGRATIONS=false \
    "$WEBUI_IMAGE" \
    sh -lc 'cd /app/backend && python - <<'"'"'PY'"'"'
import asyncio
from open_webui.config import DEFAULT_CONFIG
from open_webui.models.config import Config

async def main():
    values = await Config.get_many("agent.mode.enable")
    assert isinstance(DEFAULT_CONFIG, dict)
    assert "agent.mode.enable" in DEFAULT_CONFIG
    assert isinstance(values, dict)
    print("target_config_schema=compatible")

asyncio.run(main())
PY'
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

restore_runtime_state() {
  docker run --rm --network none \
    -v "$RUNTIME_STATE_VOLUME:/state" \
    -v "$BACKUP_DIR:/backup:ro" \
    --entrypoint /service/.venv/bin/python \
    "$OLD_RUNTIME_IMAGE" -c '
import os
import sqlite3
for suffix in ("-wal", "-shm"):
    try:
        os.remove("/state/runtime-state.sqlite3" + suffix)
    except FileNotFoundError:
        pass
source = sqlite3.connect("file:/backup/runtime-state.before.sqlite3?mode=ro", uri=True)
target = sqlite3.connect("/state/runtime-state.sqlite3")
source.backup(target)
target.close()
source.close()
'
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

rollback() {
  local rc=$?
  local rollback_failed=0
  trap - ERR
  set +e

  if [[ "$runtime_state_may_change" -eq 1 ]]; then
    "${target_compose[@]}" stop -t 30 open-webui-pr7 agentscope-runtime || rollback_failed=1
    restore_runtime_state || rollback_failed=1
  fi
  if [[ "$migrated" -eq 1 ]]; then
    run_alembic "downgrade $OLD_MIGRATION_HEAD" || rollback_failed=1
  fi

  "${rollback_compose[@]}" up -d --no-deps --force-recreate agentscope-runtime || rollback_failed=1
  wait_healthy openwebui-pr7-agentscope-runtime 72 || rollback_failed=1
  "${rollback_compose[@]}" up -d --no-deps --force-recreate open-webui-pr7 || rollback_failed=1
  wait_healthy open-webui-pr7 120 || rollback_failed=1

  test "$(docker inspect open-webui-pr7 --format '{{.Image}}')" = "$OLD_WEBUI_IMAGE_ID" || rollback_failed=1
  test "$(docker inspect openwebui-pr7-agentscope-runtime --format '{{.Image}}')" = "$OLD_RUNTIME_IMAGE_ID" || rollback_failed=1
  test "$(runtime_schema_version "$OLD_RUNTIME_IMAGE")" = 1 || rollback_failed=1

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

test "$(docker image inspect "$WEBUI_IMAGE" --format '{{.Id}}')" = "$WEBUI_IMAGE_ID"
test "$(docker image inspect "$RUNTIME_IMAGE" --format '{{.Id}}')" = "$RUNTIME_IMAGE_ID"
test "$(docker image inspect "$WEBUI_IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')" = "$SOURCE_SHA"
test "$(docker image inspect "$RUNTIME_IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')" = "$SOURCE_SHA"
test "$(docker image inspect "$OLD_WEBUI_IMAGE" --format '{{.Id}}')" = "$OLD_WEBUI_IMAGE_ID"
test "$(docker image inspect "$OLD_RUNTIME_IMAGE" --format '{{.Id}}')" = "$OLD_RUNTIME_IMAGE_ID"

mkdir -m 700 "$BACKUP_DIR"
cp compose.yaml compose.webui-rebuild-eaff69b0d317.yaml compose.webui-eaff69-no-migrations.yaml \
  "$WEBUI_OVERRIDE" "$RUNTIME_OVERRIDE" "$OLD_WEBUI_OVERRIDE" "$OLD_RUNTIME_OVERRIDE" "$BACKUP_DIR/"
docker inspect open-webui-pr7 openwebui-pr7-agentscope-runtime >"$BACKUP_DIR/target-containers.before.json"
docker exec openwebui-pr7-db pg_dump -U webui_pr7 -d webui_pr7 --schema-only \
  >"$BACKUP_DIR/database-schema.before.sql"
docker exec openwebui-pr7-db psql -U webui_pr7 -d webui_pr7 -AtF '|' \
  -c "SELECT id,state,updated_at FROM agent_run WHERE state IN ('queued','running','waiting_approval','waiting_user_input','finalizing') ORDER BY updated_at DESC" \
  >"$BACKUP_DIR/active-runs.before.tsv"

recent_active=$(docker exec openwebui-pr7-db psql -U webui_pr7 -d webui_pr7 -Atc \
  "SELECT count(*) FROM agent_run WHERE state IN ('queued','running','waiting_approval','waiting_user_input','finalizing') AND updated_at >= CAST(EXTRACT(EPOCH FROM clock_timestamp()) * 1000000000 AS BIGINT) - $RECENT_ACTIVE_WINDOW_NS")
test "$recent_active" = 0

before_main=$(docker inspect open-webui --format '{{.Id}}|{{.Image}}|{{.RestartCount}}')
before_db=$(docker inspect openwebui-pr7-db --format '{{.Id}}')
before_redis=$(docker inspect openwebui-pr7-redis --format '{{.Id}}')
before_terminals=$(docker inspect open-webui-pr7-terminals --format '{{.Id}}')

"${target_compose[@]}" config >"$BACKUP_DIR/compose.target.resolved.yaml"
test "$("${target_compose[@]}" config --images | grep -Fx "$WEBUI_IMAGE" | wc -l | tr -d ' ')" = 1
test "$("${target_compose[@]}" config --images | grep -Fx "$RUNTIME_IMAGE" | wc -l | tr -d ' ')" = 1
grep -Fq 'agentscope_runtime.launcher' "$BACKUP_DIR/compose.target.resolved.yaml"
grep -Fq 'AGENT_RUNTIME_STATE_PATH: /var/lib/agentscope-runtime/runtime-state.sqlite3' "$BACKUP_DIR/compose.target.resolved.yaml"
grep -Fq 'UVICORN_WORKERS: "1"' "$BACKUP_DIR/compose.target.resolved.yaml"
grep -Fq 'name: openwebui-pr7-agentscope-runtime-state' "$BACKUP_DIR/compose.target.resolved.yaml"

probe_target_config_schema >"$BACKUP_DIR/target-config-probe.txt"
test "$(alembic_current)" = "$OLD_MIGRATION_HEAD"
test "$(runtime_schema_version "$OLD_RUNTIME_IMAGE")" = 1
backup_runtime_state

run_alembic 'upgrade head'
migrated=1
test "$(alembic_current)" = "$NEW_MIGRATION_HEAD"

runtime_state_may_change=1
"${target_compose[@]}" up -d --no-deps --force-recreate agentscope-runtime
wait_healthy openwebui-pr7-agentscope-runtime 72
"${target_compose[@]}" up -d --no-deps --force-recreate open-webui-pr7
wait_healthy open-webui-pr7 120

test "$(docker inspect open-webui-pr7 --format '{{.Image}}')" = "$WEBUI_IMAGE_ID"
test "$(docker inspect openwebui-pr7-agentscope-runtime --format '{{.Image}}')" = "$RUNTIME_IMAGE_ID"
test "$(docker inspect open-webui-pr7 --format '{{.RestartCount}}')" = 0
test "$(docker inspect openwebui-pr7-agentscope-runtime --format '{{.RestartCount}}')" = 0
test "$(docker inspect open-webui-pr7 --format '{{.State.OOMKilled}}')" = false
test "$(docker inspect openwebui-pr7-agentscope-runtime --format '{{.State.OOMKilled}}')" = false
test "$(runtime_schema_version "$RUNTIME_IMAGE")" = 2
test "$(docker inspect openwebui-pr7-db --format '{{.Id}}')" = "$before_db"
test "$(docker inspect openwebui-pr7-redis --format '{{.Id}}')" = "$before_redis"
test "$(docker inspect open-webui-pr7-terminals --format '{{.Id}}')" = "$before_terminals"
test "$(docker inspect open-webui --format '{{.Id}}|{{.Image}}|{{.RestartCount}}')" = "$before_main"

curl --noproxy '*' -fsS --max-time 15 http://127.0.0.1:18085/health >"$BACKUP_DIR/health.after.json"
docker exec openwebui-pr7-db psql -U webui_pr7 -d webui_pr7 -Atc 'SELECT version_num FROM alembic_version' \
  >"$BACKUP_DIR/alembic.after.txt"
docker inspect open-webui-pr7 openwebui-pr7-agentscope-runtime >"$BACKUP_DIR/target-containers.after.json"
find "$BACKUP_DIR" -maxdepth 1 -type f ! -name SHA256SUMS -print0 \
  | sort -z \
  | xargs -0 sha256sum >"$BACKUP_DIR/SHA256SUMS"

runtime_state_may_change=0
trap - ERR
printf 'state=finished\nexit_code=0\nfinished_at=%s\nbackup=%s\n' \
  "$(date --iso-8601=seconds)" "$BACKUP_DIR" >"$STATUS_PATH"
printf 'backup=%s\n' "$BACKUP_DIR"
docker inspect open-webui-pr7 --format 'webui_id={{.Id}} image_id={{.Image}} health={{.State.Health.Status}} restarts={{.RestartCount}} oom={{.State.OOMKilled}}'
docker inspect openwebui-pr7-agentscope-runtime --format 'runtime_id={{.Id}} image_id={{.Image}} health={{.State.Health.Status}} restarts={{.RestartCount}} oom={{.State.OOMKilled}}'
