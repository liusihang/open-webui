#!/usr/bin/env bash
set -Eeuo pipefail

REHEARSAL_PREFIX=pr7-live-rehearsal
REHEARSAL_ROOT=${REHEARSAL_ROOT:-/home/aiserver/staging/openwebui-pr7-live-release-rehearsal-20260725}
NETWORK=${REHEARSAL_PREFIX}-net
PG_CONTAINER=${REHEARSAL_PREFIX}-db
REDIS_CONTAINER=${REHEARSAL_PREFIX}-redis
RUNTIME_CONTAINER=${REHEARSAL_PREFIX}-runtime
WEBUI_CONTAINER=${REHEARSAL_PREFIX}-webui

LIVE_WEBUI_CONTAINER=open-webui
LIVE_DB_CONTAINER=openwebui-db
LIVE_REDIS_CONTAINER=openwebui-redis
LIVE_DATA_DIR=/srv/openwebui-migration/data/openwebui
LIVE_NETWORK=openwebui-migration_default
EXPECTED_LIVE_CONTAINER_ID=78faa81d479d8c5ef33a85277feeb3dc5de68861c3f25dcaac67285935f9c13e
EXPECTED_LIVE_IMAGE_ID=sha256:7ec820b71fa94205b273cb8cd00344a130e1921ae8e643ba6192b0e58933bd45
EXPECTED_LIVE_STARTED_AT=2026-07-07T03:53:51.178582025Z

PG_IMAGE=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/pgvector/pgvector:pg16
REDIS_IMAGE=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/redis:7-alpine
OLD_WEBUI_IMAGE=open-webui:live-f8106c651-to-v0102-pr7-b3-7-onlyoffice-mergefix-slim-20260707013738
OLD_WEBUI_IMAGE_ID=sha256:7ec820b71fa94205b273cb8cd00344a130e1921ae8e643ba6192b0e58933bd45
CANDIDATE_WEBUI_IMAGE=open-webui:agentmode-v0102-d67e1af818-slim-release
CANDIDATE_WEBUI_IMAGE_ID=sha256:3dbfd378c03cc2262d8e1855cd19e99fa207aaf9c8adb3e7b2c5c65218db8da8
RUNTIME_IMAGE=open-webui-pr7-agentscope-runtime:742f686182-true-final-stream
RUNTIME_IMAGE_ID=sha256:f7396ba23e49f934216ba8fc4b38c695b7f639722d852b44234769c66ca7f6e9

OLD_DB_HEAD=f3a4b5c6d7e8
NEW_DB_HEAD=f8a9b0c1d2e3
REHEARSAL_DB_USER=webui_rehearsal
REHEARSAL_DB_NAME=webui_rehearsal
REHEARSAL_DB_URL=postgresql://${REHEARSAL_DB_USER}@${PG_CONTAINER}:5432/${REHEARSAL_DB_NAME}
REHEARSAL_URL=${REHEARSAL_URL:-http://192.168.2.238:18086}

DUMP_PATH=$REHEARSAL_ROOT/production-f3.dump
DATA_SNAPSHOT=$REHEARSAL_ROOT/data/openwebui
PGDATA_DIR=$REHEARSAL_ROOT/data/postgres
RUNTIME_STATE_DIR=$REHEARSAL_ROOT/data/runtime
AUDIT_DIR=$REHEARSAL_ROOT/audit
LOG_DIR=$REHEARSAL_ROOT/logs
TOKEN_PATH=$REHEARSAL_ROOT/.runtime-token

timestamp() {
  date --iso-8601=seconds
}

die() {
  printf 'error=%s\n' "$*" >&2
  exit 1
}

assert_rehearsal_name() {
  local name=$1
  [[ "$name" == "${REHEARSAL_PREFIX}"-* ]] || die "unsafe rehearsal object name: $name"
}

live_anchor() {
  docker inspect "$LIVE_WEBUI_CONTAINER" \
    --format '{{.Id}}|{{.Image}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.RestartCount}}|{{.State.StartedAt}}'
}

assert_live_anchor() {
  local anchor id image health restarts started
  anchor=$(live_anchor)
  IFS='|' read -r id image health restarts started <<<"$anchor"
  [[ "$id" == "$EXPECTED_LIVE_CONTAINER_ID" ]] || die "formal live container id changed"
  [[ "$image" == "$EXPECTED_LIVE_IMAGE_ID" ]] || die "formal live image id changed"
  [[ "$health" == healthy ]] || die "formal live is not healthy"
  [[ "$restarts" == 0 ]] || die "formal live restart count changed"
  [[ "$started" == "$EXPECTED_LIVE_STARTED_AT" ]] || die "formal live start time changed"
}

live_env_value() {
  local key=$1
  docker inspect "$LIVE_WEBUI_CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | sed -n "s/^${key}=//p" \
    | tail -n 1
}

wait_healthy() {
  local container=$1
  local attempts=${2:-120}
  local health running
  for _ in $(seq 1 "$attempts"); do
    running=$(docker inspect "$container" --format '{{.State.Running}}' 2>/dev/null || true)
    health=$(docker inspect "$container" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' 2>/dev/null || true)
    if [[ "$running" == true && "$health" == healthy ]]; then
      return 0
    fi
    [[ "$running" != false ]] || return 1
    sleep 2
  done
  return 1
}

wait_postgres() {
  for _ in $(seq 1 120); do
    if docker exec "$PG_CONTAINER" pg_isready -U "$REHEARSAL_DB_USER" -d "$REHEARSAL_DB_NAME" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

remove_rehearsal_container() {
  local name=$1
  assert_rehearsal_name "$name"
  if docker inspect "$name" >/dev/null 2>&1; then
    docker rm -f "$name" >/dev/null
  fi
}

ensure_directories() {
  install -d -m 700 "$REHEARSAL_ROOT" "$REHEARSAL_ROOT/data" "$AUDIT_DIR" "$LOG_DIR"
}

ensure_network() {
  assert_rehearsal_name "$NETWORK"
  docker network inspect "$NETWORK" >/dev/null 2>&1 || docker network create "$NETWORK" >/dev/null
}

ensure_runtime_token() {
  if [[ ! -s "$TOKEN_PATH" ]]; then
    umask 077
    openssl rand -hex 32 >"$TOKEN_PATH"
  fi
  chmod 600 "$TOKEN_PATH"
}

runtime_token() {
  ensure_runtime_token
  tr -d '\r\n' <"$TOKEN_PATH"
}

db_head() {
  docker exec "$PG_CONTAINER" psql -U "$REHEARSAL_DB_USER" -d "$REHEARSAL_DB_NAME" -Atc \
    'select version_num from alembic_version'
}

record_safe_anchors() {
  local label=$1
  {
    printf 'recorded_at=%s\n' "$(timestamp)"
    printf 'formal_live=%s\n' "$(live_anchor)"
    docker inspect "$PG_CONTAINER" \
      --format 'rehearsal_db={{.Id}}|{{.Image}}|{{.State.Status}}|{{.RestartCount}}|{{.State.StartedAt}}' 2>/dev/null || true
    docker inspect "$REDIS_CONTAINER" \
      --format 'rehearsal_redis={{.Id}}|{{.Image}}|{{.State.Status}}|{{.RestartCount}}|{{.State.StartedAt}}' 2>/dev/null || true
    docker inspect "$RUNTIME_CONTAINER" \
      --format 'rehearsal_runtime={{.Id}}|{{.Image}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.RestartCount}}|{{.State.StartedAt}}' 2>/dev/null || true
    docker inspect "$WEBUI_CONTAINER" \
      --format 'rehearsal_webui={{.Id}}|{{.Image}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.RestartCount}}|{{.State.StartedAt}}' 2>/dev/null || true
  } >"$AUDIT_DIR/${label}-anchors.txt"
}

prepare_snapshot() {
  ensure_directories
  assert_live_anchor
  record_safe_anchors prepare-before

  test "$(docker image inspect "$OLD_WEBUI_IMAGE" --format '{{.Id}}')" = "$OLD_WEBUI_IMAGE_ID"
  test "$(docker image inspect "$CANDIDATE_WEBUI_IMAGE" --format '{{.Id}}')" = "$CANDIDATE_WEBUI_IMAGE_ID"
  test "$(docker image inspect "$RUNTIME_IMAGE" --format '{{.Id}}')" = "$RUNTIME_IMAGE_ID"

  if [[ ! -s "$DUMP_PATH" ]]; then
    local dump_tmp=${DUMP_PATH}.tmp
    rm -f "$dump_tmp"
    local db_user db_name started finished
    db_user=$(docker inspect "$LIVE_DB_CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^POSTGRES_USER=//p' | tail -n 1)
    db_name=$(docker inspect "$LIVE_DB_CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^POSTGRES_DB=//p' | tail -n 1)
    started=$(date +%s)
    docker exec "$LIVE_DB_CONTAINER" pg_dump -U "$db_user" -d "$db_name" \
      --format=custom --no-owner --no-acl >"$dump_tmp"
    mv "$dump_tmp" "$DUMP_PATH"
    finished=$(date +%s)
    printf 'dump_seconds=%s\ndump_bytes=%s\n' "$((finished - started))" "$(stat -c %s "$DUMP_PATH")" \
      >"$AUDIT_DIR/production-dump.txt"
  fi

  docker run --rm --network none -v "$REHEARSAL_ROOT:/backup:ro" --entrypoint pg_restore "$PG_IMAGE" \
    -l /backup/production-f3.dump >"$AUDIT_DIR/production-dump-list.txt"

  if [[ ! -d "$DATA_SNAPSHOT" ]]; then
    local snapshot_tmp=${DATA_SNAPSHOT}.tmp
    rm -rf "$snapshot_tmp"
    cp -a --reflink=auto "$LIVE_DATA_DIR" "$snapshot_tmp"
    mv "$snapshot_tmp" "$DATA_SNAPSHOT"
  fi

  {
    printf 'source_bytes=%s\n' "$(du -sb "$LIVE_DATA_DIR" | awk '{print $1}')"
    printf 'snapshot_bytes=%s\n' "$(du -sb "$DATA_SNAPSHOT" | awk '{print $1}')"
    printf 'source_entries=%s\n' "$(find "$LIVE_DATA_DIR" -xdev | wc -l | tr -d ' ')"
    printf 'snapshot_entries=%s\n' "$(find "$DATA_SNAPSHOT" -xdev | wc -l | tr -d ' ')"
  } >"$AUDIT_DIR/production-data-snapshot.txt"

  ensure_network
  install -d -m 700 "$PGDATA_DIR" "$RUNTIME_STATE_DIR"

  if ! docker inspect "$PG_CONTAINER" >/dev/null 2>&1; then
    docker run -d \
      --name "$PG_CONTAINER" \
      --network "$NETWORK" \
      --network-alias db \
      -e POSTGRES_HOST_AUTH_METHOD=trust \
      -e POSTGRES_USER="$REHEARSAL_DB_USER" \
      -e POSTGRES_DB="$REHEARSAL_DB_NAME" \
      -v "$PGDATA_DIR:/var/lib/postgresql/data" \
      --health-cmd "pg_isready -U $REHEARSAL_DB_USER -d $REHEARSAL_DB_NAME" \
      --health-interval 2s \
      --health-timeout 5s \
      --health-retries 30 \
      "$PG_IMAGE" >/dev/null
  fi
  wait_postgres

  if ! docker inspect "$REDIS_CONTAINER" >/dev/null 2>&1; then
    docker run -d \
      --name "$REDIS_CONTAINER" \
      --network "$NETWORK" \
      --network-alias redis \
      --health-cmd 'redis-cli ping | grep -q PONG' \
      --health-interval 2s \
      --health-timeout 5s \
      --health-retries 30 \
      "$REDIS_IMAGE" redis-server --appendonly yes --timeout 1800 --maxclients 10000 >/dev/null
  fi
  wait_healthy "$REDIS_CONTAINER" 60

  if [[ ! -f "$AUDIT_DIR/restore-f3.initial.done" ]]; then
    local started finished
    started=$(date +%s)
    docker exec -i "$PG_CONTAINER" pg_restore \
      -U "$REHEARSAL_DB_USER" \
      -d "$REHEARSAL_DB_NAME" \
      --no-owner --no-acl --exit-on-error <"$DUMP_PATH" \
      >"$LOG_DIR/restore-f3.initial.log" 2>&1
    finished=$(date +%s)
    printf 'restore_seconds=%s\nrestored_head=%s\n' "$((finished - started))" "$(db_head)" \
      >"$AUDIT_DIR/restore-f3.initial.done"
  fi
  test "$(db_head)" = "$OLD_DB_HEAD"
  docker exec "$PG_CONTAINER" psql -U "$REHEARSAL_DB_USER" -d "$REHEARSAL_DB_NAME" -AtF '|' \
    -c 'select (select count(*) from "user"),(select count(*) from chat),(select count(*) from file),(select count(*) from knowledge),(select count(*) from function),(select count(*) from tool)' \
    >"$AUDIT_DIR/production-counts.f3.txt"
  assert_live_anchor
  record_safe_anchors prepare-after
  printf 'state=prepared\nhead=%s\nroot=%s\n' "$(db_head)" "$REHEARSAL_ROOT"
}

migrate_snapshot() {
  ensure_directories
  assert_live_anchor
  test "$(db_head)" = "$OLD_DB_HEAD"
  local live_secret started finished
  live_secret=$(live_env_value WEBUI_SECRET_KEY)
  [[ -n "$live_secret" ]] || die 'formal live WEBUI_SECRET_KEY is missing'
  started=$(date +%s)
  docker run --rm --network "$NETWORK" \
    -e DATABASE_URL="$REHEARSAL_DB_URL" \
    -e WEBUI_SECRET_KEY="$live_secret" \
    -e ENABLE_DB_MIGRATIONS=false \
    --entrypoint sh \
    "$CANDIDATE_WEBUI_IMAGE" \
    -lc 'cd /app/backend/open_webui && alembic -c alembic.ini current && alembic -c alembic.ini upgrade head && alembic -c alembic.ini current' \
    >"$LOG_DIR/migrate-f3-to-f8.log" 2>&1
  finished=$(date +%s)
  test "$(db_head)" = "$NEW_DB_HEAD"
  printf 'migration_seconds=%s\nhead=%s\n' "$((finished - started))" "$(db_head)" \
    >"$AUDIT_DIR/migrate-f3-to-f8.txt"
  assert_live_anchor
  record_safe_anchors migrate-after
  printf 'state=migrated\nhead=%s\nseconds=%s\n' "$(db_head)" "$((finished - started))"
}

start_runtime() {
  local token no_proxy
  token=$(runtime_token)
  no_proxy=$(live_env_value NO_PROXY)
  remove_rehearsal_container "$RUNTIME_CONTAINER"
  docker run -d \
    --name "$RUNTIME_CONTAINER" \
    --network "$NETWORK" \
    --network-alias agentscope-runtime \
    --env-file <(docker inspect openwebui-pr7-agentscope-runtime --format '{{range .Config.Env}}{{println .}}{{end}}') \
    -e AGENT_RUNTIME_SERVICE_TOKEN="$token" \
    -e OPENWEBUI_SERVICE_TOKEN="$token" \
    -e OPENWEBUI_BASE_URL=http://open-webui:8080 \
    -e AGENT_RUNTIME_STATE_PATH=/var/lib/agentscope-runtime/runtime-state.sqlite3 \
    -e UVICORN_WORKERS=1 \
    -e WEB_CONCURRENCY=1 \
    -e NO_PROXY="${no_proxy},open-webui,agentscope-runtime,${PG_CONTAINER},${REDIS_CONTAINER},bifrost,onlyoffice" \
    -e no_proxy="${no_proxy},open-webui,agentscope-runtime,${PG_CONTAINER},${REDIS_CONTAINER},bifrost,onlyoffice" \
    -v "$RUNTIME_STATE_DIR:/var/lib/agentscope-runtime" \
    --health-cmd 'python -c "import urllib.request; urllib.request.urlopen(\"http://127.0.0.1:8000/health\", timeout=5).read()"' \
    --health-interval 2s \
    --health-timeout 8s \
    --health-retries 30 \
    "$RUNTIME_IMAGE" \
    uv run python -m agentscope_runtime.launcher --host 0.0.0.0 --port 8000 --workers 1 >/dev/null
  wait_healthy "$RUNTIME_CONTAINER" 120
}

create_webui() {
  local image=$1
  local mode=$2
  local live_secret no_proxy token
  live_secret=$(live_env_value WEBUI_SECRET_KEY)
  no_proxy=$(live_env_value NO_PROXY)
  token=$(runtime_token)
  remove_rehearsal_container "$WEBUI_CONTAINER"

  local -a agent_env=()
  if [[ "$mode" == candidate ]]; then
    agent_env=(
      -e ENABLE_AGENT_MODE=true
      -e AGENT_RUNTIME_BASE_URL=http://agentscope-runtime:8000
      -e AGENT_RUNTIME_SERVICE_TOKEN="$token"
      -e AGENT_RUN_DEFAULT_TIMEOUT_SECONDS=900
      -e AGENT_RUN_MAX_MODEL_CALLS=24
      -e AGENT_RUN_MAX_TOOL_CALLS=48
      -e AGENT_TEAM_MAX_SUBAGENTS=8
    )
  fi

  docker create \
    --name "$WEBUI_CONTAINER" \
    --network "$NETWORK" \
    --network-alias open-webui \
    -p 18086:8080 \
    --env-file <(docker inspect "$LIVE_WEBUI_CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}') \
    -e DATABASE_URL="$REHEARSAL_DB_URL" \
    -e REDIS_URL=redis://${REDIS_CONTAINER}:6379/0 \
    -e WEBSOCKET_REDIS_URL=redis://${REDIS_CONTAINER}:6379/0 \
    -e WEBUI_SECRET_KEY="$live_secret" \
    -e ENABLE_DB_MIGRATIONS=false \
    -e UVICORN_WORKERS=4 \
    -e WEBUI_URL="$REHEARSAL_URL" \
    -e DEPLOYMENT_ID=pr7-live-release-rehearsal \
    -e INSTANCE_ID=pr7-live-release-rehearsal \
    -e NO_PROXY="${no_proxy},open-webui,agentscope-runtime,${PG_CONTAINER},${REDIS_CONTAINER},bifrost,onlyoffice" \
    -e no_proxy="${no_proxy},open-webui,agentscope-runtime,${PG_CONTAINER},${REDIS_CONTAINER},bifrost,onlyoffice" \
    "${agent_env[@]}" \
    -v "$DATA_SNAPSHOT:/app/backend/data" \
    "$image" >/dev/null
  docker network connect "$LIVE_NETWORK" "$WEBUI_CONTAINER"
  docker start "$WEBUI_CONTAINER" >/dev/null
  wait_healthy "$WEBUI_CONTAINER" 180
}

probe_webui() {
  local label=$1
  local admin_id
  admin_id=$(docker exec "$PG_CONTAINER" psql -U "$REHEARSAL_DB_USER" -d "$REHEARSAL_DB_NAME" -Atc \
    "select id from \"user\" where role='admin' order by created_at limit 1")
  [[ -n "$admin_id" ]] || die 'rehearsal snapshot has no admin user'
  docker exec -e ADMIN_USER_ID="$admin_id" -i "$WEBUI_CONTAINER" python - <<'PY' \
    >"$AUDIT_DIR/${label}-api-probe.json"
import json
import os
import time
import urllib.request
from datetime import timedelta
from open_webui.utils.auth import create_token

token = create_token({'id': os.environ['ADMIN_USER_ID']}, expires_delta=timedelta(minutes=20))
headers = {'Authorization': f'Bearer {token}'}
checks = {}
for name, path in (
    ('models', '/api/models?refresh=true'),
    ('knowledge', '/api/v1/knowledge/?page=1'),
    ('files_count', '/api/v1/files/count'),
    ('functions', '/api/v1/functions/'),
    ('tools', '/api/v1/tools/'),
):
    started = time.perf_counter()
    request = urllib.request.Request('http://127.0.0.1:8080' + path, headers=headers)
    with urllib.request.urlopen(request, timeout=180) as response:
        raw = response.read()
    payload = json.loads(raw) if raw else None
    if isinstance(payload, list):
        count = len(payload)
    elif isinstance(payload, dict) and isinstance(payload.get('data'), list):
        count = len(payload['data'])
    elif isinstance(payload, dict) and isinstance(payload.get('items'), list):
        count = len(payload['items'])
    elif isinstance(payload, (int, float)):
        count = payload
    else:
        count = None
    checks[name] = {
        'status': response.status,
        'bytes': len(raw),
        'count': count,
        'elapsed_ms': round((time.perf_counter() - started) * 1000, 2),
    }
print(json.dumps({'ok': all(v['status'] == 200 for v in checks.values()), 'checks': checks}, indent=2, sort_keys=True))
PY
  curl --noproxy '*' -fsS --max-time 20 http://127.0.0.1:18086/health \
    >"$AUDIT_DIR/${label}-health.json"
  docker exec "$PG_CONTAINER" psql -U "$REHEARSAL_DB_USER" -d "$REHEARSAL_DB_NAME" -AtF '|' \
    -c 'select (select count(*) from "user"),(select count(*) from chat),(select count(*) from file),(select count(*) from knowledge),(select count(*) from function),(select count(*) from tool)' \
    >"$AUDIT_DIR/${label}-counts.txt"
}

start_candidate() {
  ensure_directories
  assert_live_anchor
  test "$(db_head)" = "$NEW_DB_HEAD"
  start_runtime
  create_webui "$CANDIDATE_WEBUI_IMAGE" candidate
  probe_webui candidate-f8
  test "$(docker inspect "$WEBUI_CONTAINER" --format '{{.Image}}')" = "$CANDIDATE_WEBUI_IMAGE_ID"
  test "$(docker inspect "$RUNTIME_CONTAINER" --format '{{.Image}}')" = "$RUNTIME_IMAGE_ID"
  test "$(docker inspect "$WEBUI_CONTAINER" --format '{{.RestartCount}}')" = 0
  test "$(docker inspect "$RUNTIME_CONTAINER" --format '{{.RestartCount}}')" = 0
  assert_live_anchor
  record_safe_anchors candidate-after
  printf 'state=candidate_running\nhead=%s\nurl=%s\n' "$(db_head)" "$REHEARSAL_URL"
}

start_old_on_f8() {
  ensure_directories
  assert_live_anchor
  test "$(db_head)" = "$NEW_DB_HEAD"
  remove_rehearsal_container "$WEBUI_CONTAINER"
  remove_rehearsal_container "$RUNTIME_CONTAINER"
  create_webui "$OLD_WEBUI_IMAGE" old
  probe_webui old-on-f8
  test "$(docker inspect "$WEBUI_CONTAINER" --format '{{.Image}}')" = "$OLD_WEBUI_IMAGE_ID"
  test "$(docker inspect "$WEBUI_CONTAINER" --format '{{.RestartCount}}')" = 0
  test "$(db_head)" = "$NEW_DB_HEAD"
  assert_live_anchor
  record_safe_anchors old-on-f8-after
  printf 'state=old_on_f8_running\nhead=%s\n' "$(db_head)"
}

restore_f3() {
  ensure_directories
  assert_live_anchor
  remove_rehearsal_container "$WEBUI_CONTAINER"
  remove_rehearsal_container "$RUNTIME_CONTAINER"
  local started finished
  started=$(date +%s)
  docker exec "$PG_CONTAINER" dropdb -U "$REHEARSAL_DB_USER" --force "$REHEARSAL_DB_NAME"
  docker exec "$PG_CONTAINER" createdb -U "$REHEARSAL_DB_USER" "$REHEARSAL_DB_NAME"
  docker exec -i "$PG_CONTAINER" pg_restore \
    -U "$REHEARSAL_DB_USER" \
    -d "$REHEARSAL_DB_NAME" \
    --no-owner --no-acl --exit-on-error <"$DUMP_PATH" \
    >"$LOG_DIR/restore-f3.rollback.log" 2>&1
  docker exec "$REDIS_CONTAINER" redis-cli FLUSHALL >/dev/null
  finished=$(date +%s)
  test "$(db_head)" = "$OLD_DB_HEAD"
  printf 'restore_seconds=%s\nhead=%s\n' "$((finished - started))" "$(db_head)" \
    >"$AUDIT_DIR/restore-f3.rollback.txt"
  create_webui "$OLD_WEBUI_IMAGE" old
  probe_webui old-on-f3-restored
  test "$(docker inspect "$WEBUI_CONTAINER" --format '{{.Image}}')" = "$OLD_WEBUI_IMAGE_ID"
  test "$(docker inspect "$WEBUI_CONTAINER" --format '{{.RestartCount}}')" = 0
  assert_live_anchor
  record_safe_anchors old-on-f3-restored-after
  printf 'state=restored_f3\nhead=%s\nseconds=%s\n' "$(db_head)" "$((finished - started))"
}

show_status() {
  assert_live_anchor
  record_safe_anchors status
  printf 'formal_live=%s\n' "$(live_anchor)"
  if docker inspect "$PG_CONTAINER" >/dev/null 2>&1; then
    printf 'rehearsal_head=%s\n' "$(db_head)"
  fi
  for name in "$PG_CONTAINER" "$REDIS_CONTAINER" "$RUNTIME_CONTAINER" "$WEBUI_CONTAINER"; do
    docker inspect "$name" \
      --format '{{.Name}}|{{.Image}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.RestartCount}}|{{.State.StartedAt}}' \
      2>/dev/null || true
  done
}

cleanup_runtime() {
  assert_live_anchor
  remove_rehearsal_container "$WEBUI_CONTAINER"
  remove_rehearsal_container "$RUNTIME_CONTAINER"
  remove_rehearsal_container "$REDIS_CONTAINER"
  remove_rehearsal_container "$PG_CONTAINER"
  assert_rehearsal_name "$NETWORK"
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
  printf 'state=containers_cleaned\nroot_preserved=%s\n' "$REHEARSAL_ROOT"
}

case "${1:-}" in
  prepare) prepare_snapshot ;;
  migrate) migrate_snapshot ;;
  candidate) start_candidate ;;
  old-on-f8) start_old_on_f8 ;;
  restore-f3) restore_f3 ;;
  status) show_status ;;
  cleanup) cleanup_runtime ;;
  *)
    printf 'usage: %s {prepare|migrate|candidate|old-on-f8|restore-f3|status|cleanup}\n' "$0" >&2
    exit 2
    ;;
esac
