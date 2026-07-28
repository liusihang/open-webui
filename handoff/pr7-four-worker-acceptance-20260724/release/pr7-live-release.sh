#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

STACK_ROOT=${STACK_ROOT:-/srv/openwebui-migration}
BASE_COMPOSE=${BASE_COMPOSE:-$STACK_ROOT/compose.yaml}
OVERRIDE_COMPOSE=${OVERRIDE_COMPOSE:-$SCRIPT_DIR/compose.pr7-live.yaml}
PROJECT_NAME=${PROJECT_NAME:-openwebui-migration}
WEBUI_SERVICE=${WEBUI_SERVICE:-open-webui}
WEBUI_CONTAINER=${WEBUI_CONTAINER:-open-webui}
DB_CONTAINER=${DB_CONTAINER:-openwebui-db}
REDIS_CONTAINER=${REDIS_CONTAINER:-openwebui-redis}
RUNTIME_CONTAINER=${RUNTIME_CONTAINER:-openwebui-agentscope-runtime}
DATA_DIR=${DATA_DIR:-$STACK_ROOT/data/openwebui}
STATE_DIR=${STATE_DIR:-$STACK_ROOT/.pr7-release}
BACKUP_ROOT=${BACKUP_ROOT:-$STATE_DIR/backups}
RUNTIME_ENV=${RUNTIME_ENV:-$STATE_DIR/runtime.env}
RUNTIME_STATE_DIR=${RUNTIME_STATE_DIR:-$STACK_ROOT/data/agentscope-runtime}
BIFROST_SOURCE=${BIFROST_SOURCE:-$SCRIPT_DIR/../../../tools/openwebui/functions/bifrostapi.py}
PID_PROBE=${PID_PROBE:-$SCRIPT_DIR/four-worker-pid-probe.py}

CANDIDATE_IMAGE=open-webui:agentmode-v0102-5b35e9f1b-slim-release
CANDIDATE_IMAGE_ID=sha256:2d3a9138f8a83d18f1e7d72fbb7052b80aba2ddb3f11137a74f52f0f1607bf60
RUNTIME_IMAGE=open-webui-pr7-agentscope-runtime:742f686182-true-final-stream
RUNTIME_IMAGE_ID=sha256:f7396ba23e49f934216ba8fc4b38c695b7f639722d852b44234769c66ca7f6e9
OLD_IMAGE=open-webui:live-f8106c651-to-v0102-pr7-b3-7-onlyoffice-mergefix-slim-20260707013738
OLD_IMAGE_ID=sha256:7ec820b71fa94205b273cb8cd00344a130e1921ae8e643ba6192b0e58933bd45

EXPECTED_LIVE_CONTAINER_ID=78faa81d479d8c5ef33a85277feeb3dc5de68861c3f25dcaac67285935f9c13e
EXPECTED_LIVE_STARTED_AT=2026-07-07T03:53:51.178582025Z
EXPECTED_BASE_COMPOSE_SHA256=7fff73a9037687460bd6c27669e9224203241546928173106c9999d6b3425da1
EXPECTED_BASE_ENV_SHA256=419b002b069c62d7ff2978bcf9b4a005dedde0a8d7de17df34a3e8d7d14583f0
OLD_DB_HEAD=f3a4b5c6d7e8
NEW_DB_HEAD=f8a9b0c1d2e3
OLD_PIPE_MD5=42c535affdb1a4d145b973fa0d91c52e
NEW_PIPE_MD5=0d629a726b022cde297e64679798b97c
PIPE_VALVES_MD5=5506e9ba05d17eb604b0133252f076ba
NEW_PIPE_VERSION=0.2.17
REDIS_KEY_PREFIX=open-webui
MIN_FREE_BYTES=${MIN_FREE_BYTES:-85899345920}
MAX_BACKUP_AGE_SECONDS=${MAX_BACKUP_AGE_SECONDS:-21600}

RUN_DIR=
DB_USER=
DB_NAME=
FAILED_STAGE=none

timestamp() {
  date +%Y%m%d-%H%M%S
}

die() {
  printf 'error=%s\n' "$*" >&2
  exit 1
}

on_error() {
  local rc=$?
  printf 'failed_stage=%s\nexit_code=%s\n' "$FAILED_STAGE" "$rc" >&2
  if [[ -n "$RUN_DIR" && -d "$RUN_DIR" ]]; then
    printf '%s|%s|%s\n' "$(date --iso-8601=seconds)" "$FAILED_STAGE" "$rc" >"$RUN_DIR/failure.txt" || true
  fi
  exit "$rc"
}
trap on_error ERR

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

container_env_value() {
  local container=$1 key=$2
  docker inspect "$container" --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | sed -n "s/^${key}=//p" \
    | tail -n 1
}

live_anchor() {
  docker inspect "$WEBUI_CONTAINER" \
    --format '{{.Id}}|{{.Image}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.RestartCount}}|{{.State.StartedAt}}'
}

assert_original_live_anchor() {
  local id image health restarts started
  IFS='|' read -r id image health restarts started <<<"$(live_anchor)"
  [[ "$id" == "$EXPECTED_LIVE_CONTAINER_ID" ]] || die 'formal live container ID no longer matches the accepted preflight anchor'
  [[ "$image" == "$OLD_IMAGE_ID" ]] || die 'formal live image no longer matches the accepted old image'
  [[ "$health" == healthy ]] || die 'formal live is not healthy'
  [[ "$restarts" == 0 ]] || die 'formal live restart count is not zero'
  [[ "$started" == "$EXPECTED_LIVE_STARTED_AT" ]] || die 'formal live start time changed'
}

load_db_identity() {
  DB_USER=$(container_env_value "$DB_CONTAINER" POSTGRES_USER)
  DB_NAME=$(container_env_value "$DB_CONTAINER" POSTGRES_DB)
  [[ -n "$DB_USER" && -n "$DB_NAME" ]] || die 'could not resolve formal PostgreSQL role/database'
}

db_head() {
  docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -Atc \
    "select version_num from alembic_version;"
}

db_counts() {
  docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -AtF '|' -c \
    'select (select count(*) from "user"),(select count(*) from chat),(select count(*) from file),(select count(*) from knowledge),(select count(*) from function),(select count(*) from tool)'
}

pipe_fingerprint() {
  docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -AtF '|' -c \
    "select md5(content),md5(coalesce(valves::text,'')),coalesce((meta::jsonb)->'manifest'->>'version','') from function where id='bifrostapi';"
}

image_id() {
  docker image inspect "$1" --format '{{.Id}}'
}

wait_healthy() {
  local container=$1 attempts=${2:-180}
  local running health
  for _ in $(seq 1 "$attempts"); do
    running=$(docker inspect "$container" --format '{{.State.Running}}' 2>/dev/null || true)
    health=$(docker inspect "$container" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' 2>/dev/null || true)
    if [[ "$running" == true && "$health" == healthy ]]; then
      return 0
    fi
    sleep 2
  done
  die "container did not become healthy: $container"
}

write_env_line() {
  local file=$1 key=$2 value=$3
  [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]] || die "newline in generated env value: $key"
  printf '%s=%s\n' "$key" "$value" >>"$file"
}

append_no_proxy() {
  local current=$1
  python3 - "$current" <<'PY'
import sys
items = [item.strip() for item in sys.argv[1].split(',') if item.strip()]
for item in ('127.0.0.1', 'localhost', 'open-webui', 'agentscope-runtime', 'db', 'redis', 'bifrost', 'onlyoffice'):
    if item not in items:
        items.append(item)
print(','.join(items))
PY
}

prepare_runtime_env() {
  FAILED_STAGE=prepare_runtime_env
  install -d -m 700 "$STATE_DIR" "$RUNTIME_STATE_DIR"
  local token http_proxy https_proxy all_proxy no_proxy temp
  token=$(openssl rand -hex 32)
  http_proxy=$(container_env_value "$WEBUI_CONTAINER" HTTP_PROXY)
  https_proxy=$(container_env_value "$WEBUI_CONTAINER" HTTPS_PROXY)
  all_proxy=$(container_env_value "$WEBUI_CONTAINER" ALL_PROXY)
  no_proxy=$(container_env_value "$WEBUI_CONTAINER" NO_PROXY)
  no_proxy=$(append_no_proxy "$no_proxy")
  temp="$RUNTIME_ENV.tmp.$$"
  umask 077
  : >"$temp"
  write_env_line "$temp" AGENT_RUNTIME_SERVICE_TOKEN "$token"
  write_env_line "$temp" OPENWEBUI_SERVICE_TOKEN "$token"
  write_env_line "$temp" PR7_RUNTIME_ENV_FILE "$RUNTIME_ENV"
  write_env_line "$temp" PR7_RUNTIME_STATE_DIR "$RUNTIME_STATE_DIR"
  write_env_line "$temp" PR7_HTTP_PROXY "$http_proxy"
  write_env_line "$temp" PR7_HTTPS_PROXY "$https_proxy"
  write_env_line "$temp" PR7_ALL_PROXY "$all_proxy"
  write_env_line "$temp" PR7_NO_PROXY "$no_proxy"
  chmod 600 "$temp"
  mv "$temp" "$RUNTIME_ENV"
}

compose_candidate() {
  docker compose \
    --project-name "$PROJECT_NAME" \
    --env-file "$STACK_ROOT/.env" \
    --env-file "$RUNTIME_ENV" \
    -f "$BASE_COMPOSE" \
    -f "$OVERRIDE_COMPOSE" \
    "$@"
}

compose_base() {
  docker compose \
    --project-name "$PROJECT_NAME" \
    --env-file "$STACK_ROOT/.env" \
    -f "$BASE_COMPOSE" \
    "$@"
}

render_candidate_config() {
  FAILED_STAGE=render_candidate_config
  local temp
  temp=$(mktemp)
  chmod 600 "$temp"
  {
    printf 'AGENT_RUNTIME_SERVICE_TOKEN=preflight-not-a-real-token\n'
    printf 'OPENWEBUI_SERVICE_TOKEN=preflight-not-a-real-token\n'
    printf 'PR7_RUNTIME_ENV_FILE=%s\n' "$temp"
    printf 'PR7_RUNTIME_STATE_DIR=/tmp/pr7-runtime-preflight\n'
    printf 'PR7_HTTP_PROXY=\nPR7_HTTPS_PROXY=\nPR7_ALL_PROXY=\n'
    printf 'PR7_NO_PROXY=127.0.0.1,localhost,open-webui,agentscope-runtime,db,redis,bifrost,onlyoffice\n'
  } >"$temp"
  docker compose \
    --project-name "$PROJECT_NAME" \
    --env-file "$STACK_ROOT/.env" \
    --env-file "$temp" \
    -f "$BASE_COMPOSE" \
    -f "$OVERRIDE_COMPOSE" \
    config --quiet
  rm -f "$temp"
}

preflight() {
  FAILED_STAGE=preflight
  for cmd in bash docker python3 openssl sha256sum md5sum; do
    require_command "$cmd"
  done
  [[ -f "$BASE_COMPOSE" && -f "$STACK_ROOT/.env" && -f "$OVERRIDE_COMPOSE" ]] || die 'release compose inputs are missing'
  [[ -f "$BIFROST_SOURCE" && -f "$PID_PROBE" ]] || die 'release source/probe artifact is missing'
  [[ "$(sha256sum "$BASE_COMPOSE" | awk '{print $1}')" == "$EXPECTED_BASE_COMPOSE_SHA256" ]] || die 'formal compose file changed after acceptance'
  [[ "$(sha256sum "$STACK_ROOT/.env" | awk '{print $1}')" == "$EXPECTED_BASE_ENV_SHA256" ]] || die 'formal .env changed after acceptance'
  [[ "$(md5sum "$BIFROST_SOURCE" | awk '{print $1}')" == "$NEW_PIPE_MD5" ]] || die 'Bifrost 0.2.17 source hash mismatch'
  [[ "$(image_id "$CANDIDATE_IMAGE")" == "$CANDIDATE_IMAGE_ID" ]] || die 'candidate image ID mismatch'
  [[ "$(image_id "$RUNTIME_IMAGE")" == "$RUNTIME_IMAGE_ID" ]] || die 'runtime image ID mismatch'
  [[ "$(image_id "$OLD_IMAGE")" == "$OLD_IMAGE_ID" ]] || die 'old image ID mismatch'
  assert_original_live_anchor
  load_db_identity
  [[ "$(db_head)" == "$OLD_DB_HEAD" ]] || die 'formal database is not at the accepted f3 head'
  [[ "$(pipe_fingerprint)" == "$OLD_PIPE_MD5|$PIPE_VALVES_MD5|0.2.10-cache.1" ]] || die 'formal bifrostapi row changed after acceptance'
  [[ "$(docker exec "$WEBUI_CONTAINER" python -c 'from open_webui.env import REDIS_KEY_PREFIX; print(REDIS_KEY_PREFIX)')" == "$REDIS_KEY_PREFIX" ]] || die 'Redis cache key prefix changed'
  [[ "$(docker inspect "$DB_CONTAINER" --format '{{.State.Health.Status}}')" == healthy ]] || die 'formal DB is not healthy'
  [[ "$(docker inspect "$REDIS_CONTAINER" --format '{{.State.Health.Status}}')" == healthy ]] || die 'formal Redis is not healthy'
  local free_bytes
  free_bytes=$(df -PB1 "$STACK_ROOT" | awk 'NR==2 {print $4}')
  (( free_bytes >= MIN_FREE_BYTES )) || die "insufficient free space: $free_bytes bytes"
  render_candidate_config
  printf 'preflight=pass\nlive_anchor=%s\ndb_head=%s\npipe=%s\nfree_bytes=%s\n' \
    "$(live_anchor)" "$(db_head)" "$(pipe_fingerprint)" "$free_bytes"
}

backup_live() {
  preflight >/dev/null
  FAILED_STAGE=backup_live
  local id started finished source_bytes snapshot_bytes source_entries snapshot_entries
  id=$(timestamp)
  RUN_DIR="$BACKUP_ROOT/$id"
  install -d -m 700 "$RUN_DIR"
  live_anchor >"$RUN_DIR/live-anchor-before.txt"
  db_counts >"$RUN_DIR/counts.txt"
  printf '%s\n' "$(db_head)" >"$RUN_DIR/db-head.txt"
  printf '%s\n' "$(pipe_fingerprint)" >"$RUN_DIR/pipe-fingerprint.txt"

  started=$(date +%s)
  docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc >"$RUN_DIR/production-f3.dump"
  finished=$(date +%s)
  printf '%s\n' "$((finished-started))" >"$RUN_DIR/db-dump-seconds.txt"
  docker run --rm --network none -v "$RUN_DIR:/backup:ro" --entrypoint pg_restore \
    "$(docker inspect "$DB_CONTAINER" --format '{{.Config.Image}}')" -l /backup/production-f3.dump \
    >"$RUN_DIR/pg-restore-list.txt"

  started=$(date +%s)
  cp -a --reflink=auto "$DATA_DIR" "$RUN_DIR/openwebui"
  finished=$(date +%s)
  printf '%s\n' "$((finished-started))" >"$RUN_DIR/file-copy-seconds.txt"
  source_bytes=$(du -sb "$DATA_DIR" | awk '{print $1}')
  snapshot_bytes=$(du -sb "$RUN_DIR/openwebui" | awk '{print $1}')
  source_entries=$(find "$DATA_DIR" -xdev | wc -l | tr -d ' ')
  snapshot_entries=$(find "$RUN_DIR/openwebui" -xdev | wc -l | tr -d ' ')
  [[ "$source_bytes" == "$snapshot_bytes" && "$source_entries" == "$snapshot_entries" ]] || die 'file snapshot size/entry verification failed'
  printf '%s|%s|%s|%s\n' "$source_bytes" "$snapshot_bytes" "$source_entries" "$snapshot_entries" >"$RUN_DIR/file-snapshot.txt"
  sha256sum "$RUN_DIR/production-f3.dump" >"$RUN_DIR/production-f3.dump.sha256"
  assert_original_live_anchor
  live_anchor >"$RUN_DIR/live-anchor-after.txt"
  touch "$RUN_DIR/backup.ok"
  ln -sfn "$RUN_DIR" "$BACKUP_ROOT/latest"
  printf 'backup=pass\nbackup_dir=%s\ndump_bytes=%s\n' "$RUN_DIR" "$(stat -c %s "$RUN_DIR/production-f3.dump")"
}

require_latest_backup() {
  local latest created age
  latest=$(readlink -f "$BACKUP_ROOT/latest" 2>/dev/null || true)
  [[ -n "$latest" && -f "$latest/backup.ok" && -s "$latest/production-f3.dump" ]] || die 'no completed release backup is available'
  [[ "$(<"$latest/db-head.txt")" == "$OLD_DB_HEAD" ]] || die 'latest backup is not an f3 backup'
  created=$(stat -c %Y "$latest/backup.ok")
  age=$(( $(date +%s) - created ))
  (( age <= MAX_BACKUP_AGE_SECONDS )) || die "latest backup is too old: $age seconds"
  printf '%s\n' "$latest"
}

run_migrations() {
  FAILED_STAGE=migrate_f3_to_f8
  local database_url webui_secret
  database_url=$(container_env_value "$WEBUI_CONTAINER" DATABASE_URL)
  webui_secret=$(container_env_value "$WEBUI_CONTAINER" WEBUI_SECRET_KEY)
  [[ -n "$database_url" && -n "$webui_secret" ]] || die 'could not read current database/secret environment'
  docker run --rm --network "${PROJECT_NAME}_default" \
    -e DATABASE_URL="$database_url" \
    -e WEBUI_SECRET_KEY="$webui_secret" \
    -e ENABLE_DB_MIGRATIONS=false \
    --entrypoint sh \
    "$CANDIDATE_IMAGE" \
    -lc 'cd /app/backend/open_webui && alembic -c alembic.ini current && alembic -c alembic.ini upgrade head && alembic -c alembic.ini current' \
    >"$RUN_DIR/migration.log" 2>&1
  [[ "$(db_head)" == "$NEW_DB_HEAD" ]] || die 'migration did not reach f8'
}

backup_pipe_row() {
  docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -At -c \
    "select encode(convert_to(content,'UTF8'),'base64') from function where id='bifrostapi';" \
    | base64 -d >"$RUN_DIR/bifrostapi-before.py"
  docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -At -c \
    "select encode(convert_to(meta,'UTF8'),'base64') from function where id='bifrostapi';" \
    | base64 -d >"$RUN_DIR/bifrostapi-before-meta.json"
  chmod 600 "$RUN_DIR/bifrostapi-before.py" "$RUN_DIR/bifrostapi-before-meta.json"
  [[ "$(md5sum "$RUN_DIR/bifrostapi-before.py" | awk '{print $1}')" == "$OLD_PIPE_MD5" ]] || die 'exact old Bifrost content backup failed verification'
}

apply_pipe_upgrade() {
  FAILED_STAGE=upgrade_bifrost_pipe
  local current readback function_version models_version
  current=$(pipe_fingerprint)
  if [[ "$current" == "$NEW_PIPE_MD5|$PIPE_VALVES_MD5|$NEW_PIPE_VERSION" ]]; then
    return 0
  fi
  [[ "$current" == "$OLD_PIPE_MD5|$PIPE_VALVES_MD5|0.2.10-cache.1" ]] || die "unexpected bifrostapi row before upgrade: $current"
  backup_pipe_row
  python3 - "$BIFROST_SOURCE" <<'PY' | docker exec -i "$DB_CONTAINER" psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$DB_NAME" >"$RUN_DIR/bifrostapi-upgrade.sql.log"
import pathlib, sys
content = pathlib.Path(sys.argv[1]).read_text()
tag = '$bifrost_0_2_17$'
if tag in content:
    raise SystemExit('unexpected SQL dollar-quote delimiter in source')
print('BEGIN;')
print(f"UPDATE function SET content={tag}{content}{tag}, meta=jsonb_set(coalesce(nullif(meta, ''), '{{}}')::jsonb, '{{manifest,version}}', to_jsonb('0.2.17'::text), true)::text, updated_at=(extract(epoch from clock_timestamp()))::bigint WHERE id='bifrostapi';")
print('COMMIT;')
PY
  readback=$(pipe_fingerprint)
  [[ "$readback" == "$NEW_PIPE_MD5|$PIPE_VALVES_MD5|$NEW_PIPE_VERSION" ]] || die 'Bifrost upgrade readback mismatch'
  function_version=$(docker exec "$REDIS_CONTAINER" redis-cli INCR "$REDIS_KEY_PREFIX:cache:functions:bifrostapi:version")
  models_version=$(docker exec "$REDIS_CONTAINER" redis-cli INCR "$REDIS_KEY_PREFIX:cache:models:version")
  docker exec "$REDIS_CONTAINER" redis-cli PUBLISH "$REDIS_KEY_PREFIX:cache:invalidate" \
    "{\"namespace\":\"functions\",\"key\":\"bifrostapi\",\"version\":\"$function_version\"}" >/dev/null
  docker exec "$REDIS_CONTAINER" redis-cli PUBLISH "$REDIS_KEY_PREFIX:cache:invalidate" \
    "{\"namespace\":\"models\",\"key\":null,\"version\":\"$models_version\"}" >/dev/null
  printf '%s\n' "$readback" >"$RUN_DIR/bifrostapi-after.txt"
}

api_probe() {
  local label=$1 admin_id
  admin_id=$(docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -Atc \
    "select id from \"user\" where role='admin' order by created_at limit 1")
  [[ -n "$admin_id" ]] || die 'no admin user available for API smoke'
  docker exec -e ADMIN_USER_ID="$admin_id" -i "$WEBUI_CONTAINER" python - <<'PY' >"$RUN_DIR/$label-api.json"
import json, os, time, urllib.request
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
    checks[name] = {'status': response.status, 'bytes': len(raw), 'count': count, 'elapsed_ms': round((time.perf_counter() - started) * 1000, 2)}
print(json.dumps({'ok': all(item['status'] == 200 for item in checks.values()), 'checks': checks}, indent=2, sort_keys=True))
PY
  python3 - "$RUN_DIR/$label-api.json" <<'PY'
import json, pathlib, sys
payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
if not payload.get('ok'):
    raise SystemExit('API smoke failed')
PY
}

verify_candidate_runtime() {
  FAILED_STAGE=verify_candidate_runtime
  [[ "$(docker inspect "$WEBUI_CONTAINER" --format '{{.Image}}')" == "$CANDIDATE_IMAGE_ID" ]] || die 'candidate WebUI image mismatch after switch'
  [[ "$(docker inspect "$RUNTIME_CONTAINER" --format '{{.Image}}')" == "$RUNTIME_IMAGE_ID" ]] || die 'runtime image mismatch after switch'
  [[ "$(docker inspect "$WEBUI_CONTAINER" --format '{{.RestartCount}}')" == 0 ]] || die 'candidate WebUI restarted during switch'
  [[ "$(docker inspect "$RUNTIME_CONTAINER" --format '{{.RestartCount}}')" == 0 ]] || die 'runtime restarted during switch'
  [[ "$(container_env_value "$WEBUI_CONTAINER" UVICORN_WORKERS)" == 4 ]] || die 'candidate is not configured for four workers'
  [[ "$(db_head)" == "$NEW_DB_HEAD" ]] || die 'database head changed after candidate start'
  [[ "$(pipe_fingerprint)" == "$NEW_PIPE_MD5|$PIPE_VALVES_MD5|$NEW_PIPE_VERSION" ]] || die 'Bifrost row changed after candidate start'
  docker exec -i "$WEBUI_CONTAINER" python - <"$PID_PROBE" >"$RUN_DIR/four-worker-pids.json"
  api_probe candidate
  local logs
  logs="$RUN_DIR/candidate-startup.log"
  docker logs "$WEBUI_CONTAINER" >"$logs" 2>&1
  [[ "$(grep -c 'Started server process' "$logs" || true)" == 4 ]] || die 'candidate did not start exactly four server processes'
  [[ "$(grep -c 'Installing external dependencies of functions and tools' "$logs" || true)" == 1 ]] || die 'dependency install singleton did not run exactly once'
  [[ "$(grep -c 'External dependencies of functions and tools already installed by another worker; skipping' "$logs" || true)" == 3 ]] || die 'dependency singleton skip count is not three'
  [[ "$(grep -c 'Startup singleton tasks already running in another worker; skipping in this worker' "$logs" || true)" == 3 ]] || die 'startup singleton skip count is not three'
  [[ "$(grep -c 'Scheduler worker started' "$logs" || true)" == 1 ]] || die 'scheduler did not start exactly once'
  [[ "$(grep -c 'Initializing tool servers' "$logs" || true)" == 1 ]] || die 'tool server initialization did not run exactly once'
  [[ "$(grep -Ec 'Initialized [0-9]+ terminal server\(s\)' "$logs" || true)" == 1 ]] || die 'terminal server initialization did not run exactly once'
  if grep -Eq 'Application startup failed|Child process.*died|UniqueViolation|Traceback|runtime_finalization.*ReadTimeout' "$logs"; then
    die 'candidate startup log contains a release-blocking error'
  fi
  docker stats --no-stream --format '{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.NetIO}}|{{.BlockIO}}' \
    "$WEBUI_CONTAINER" "$RUNTIME_CONTAINER" >"$RUN_DIR/container-stats.txt"
}

switch_live() {
  [[ "${CONFIRM_SWITCH:-}" == 'switch-pr7-live-5b35e9f1b' ]] || die 'set CONFIRM_SWITCH=switch-pr7-live-5b35e9f1b to authorize the live switch'
  local backup counts_before downtime_start downtime_end
  preflight >/dev/null
  backup=$(require_latest_backup)
  FAILED_STAGE=initialize_switch
  RUN_DIR="$STATE_DIR/switch-$(timestamp)"
  install -d -m 700 "$RUN_DIR"
  printf '%s\n' "$backup" >"$RUN_DIR/backup-dir.txt"
  counts_before=$(db_counts)
  printf '%s\n' "$counts_before" >"$RUN_DIR/counts-before.txt"
  live_anchor >"$RUN_DIR/live-anchor-before.txt"
  prepare_runtime_env
  downtime_start=$(date +%s)
  printf '%s\n' "$(date --iso-8601=seconds)" >"$RUN_DIR/downtime-start.txt"

  FAILED_STAGE=stop_old_webui
  compose_base stop "$WEBUI_SERVICE"
  run_migrations
  apply_pipe_upgrade

  FAILED_STAGE=start_runtime
  compose_candidate up -d --no-deps agentscope-runtime
  wait_healthy "$RUNTIME_CONTAINER" 120
  FAILED_STAGE=start_candidate_webui
  compose_candidate up -d --no-deps "$WEBUI_SERVICE"
  wait_healthy "$WEBUI_CONTAINER" 180
  verify_candidate_runtime
  [[ "$(db_counts)" == "$counts_before" ]] || die 'core production row counts changed during switch'
  printf '%s\n' "$(db_counts)" >"$RUN_DIR/counts-after.txt"
  downtime_end=$(date +%s)
  printf '%s\n' "$(date --iso-8601=seconds)" >"$RUN_DIR/downtime-end.txt"
  printf '%s\n' "$((downtime_end-downtime_start))" >"$RUN_DIR/downtime-seconds.txt"
  touch "$RUN_DIR/switch.ok"
  printf 'switch=pass\nrun_dir=%s\ndowntime_seconds=%s\nwebui=%s\nruntime=%s\n' \
    "$RUN_DIR" "$((downtime_end-downtime_start))" \
    "$(docker inspect "$WEBUI_CONTAINER" --format '{{.Id}}|{{.Image}}|{{.State.Health.Status}}|{{.RestartCount}}')" \
    "$(docker inspect "$RUNTIME_CONTAINER" --format '{{.Id}}|{{.Image}}|{{.State.Health.Status}}|{{.RestartCount}}')"
}

rollback_fast() {
  [[ "${CONFIRM_ROLLBACK_FAST:-}" == 'rollback-pr7-live-to-old-image' ]] || die 'set CONFIRM_ROLLBACK_FAST=rollback-pr7-live-to-old-image to authorize fast rollback'
  FAILED_STAGE=rollback_fast
  load_db_identity
  RUN_DIR="$STATE_DIR/rollback-fast-$(timestamp)"
  install -d -m 700 "$RUN_DIR"
  local head
  head=$(db_head)
  [[ "$head" == "$OLD_DB_HEAD" || "$head" == "$NEW_DB_HEAD" ]] || die "unsupported DB head for fast rollback: $head"
  if [[ -f "$RUNTIME_ENV" ]]; then
    compose_candidate stop "$WEBUI_SERVICE" agentscope-runtime || true
    compose_candidate rm -f agentscope-runtime || true
  else
    docker rm -f "$WEBUI_CONTAINER" "$RUNTIME_CONTAINER" >/dev/null 2>&1 || true
  fi
  compose_base up -d --no-deps --force-recreate "$WEBUI_SERVICE"
  wait_healthy "$WEBUI_CONTAINER" 180
  [[ "$(docker inspect "$WEBUI_CONTAINER" --format '{{.Image}}')" == "$OLD_IMAGE_ID" ]] || die 'old image was not restored'
  [[ "$(docker inspect "$WEBUI_CONTAINER" --format '{{.RestartCount}}')" == 0 ]] || die 'old image restarted during rollback'
  api_probe rollback-fast
  docker logs "$WEBUI_CONTAINER" >"$RUN_DIR/old-image-startup.log" 2>&1
  if grep -Eq 'Application startup failed|Child process.*died|UniqueViolation|Traceback' "$RUN_DIR/old-image-startup.log"; then
    die 'old image startup failed on the current database head'
  fi
  touch "$RUN_DIR/rollback-fast.ok"
  printf 'rollback_fast=pass\nrun_dir=%s\ndb_head=%s\nwebui=%s\n' \
    "$RUN_DIR" "$head" "$(docker inspect "$WEBUI_CONTAINER" --format '{{.Id}}|{{.Image}}|{{.State.Health.Status}}|{{.RestartCount}}')"
}

restore_f3() {
  [[ "${CONFIRM_FULL_RESTORE:-}" == 'restore-f3-and-replace-current-data' ]] || die 'set CONFIRM_FULL_RESTORE=restore-f3-and-replace-current-data to authorize destructive DR restore'
  local backup failed_data started finished
  backup=$(require_latest_backup)
  load_db_identity
  RUN_DIR="$STATE_DIR/restore-f3-$(timestamp)"
  install -d -m 700 "$RUN_DIR"
  FAILED_STAGE=stop_services_for_full_restore
  if [[ -f "$RUNTIME_ENV" ]]; then
    compose_candidate stop "$WEBUI_SERVICE" agentscope-runtime || true
  else
    compose_base stop "$WEBUI_SERVICE" || true
  fi
  FAILED_STAGE=restore_f3_database
  started=$(date +%s)
  docker exec "$DB_CONTAINER" dropdb -U "$DB_USER" --force "$DB_NAME"
  docker exec "$DB_CONTAINER" createdb -U "$DB_USER" "$DB_NAME"
  docker exec -i "$DB_CONTAINER" pg_restore -U "$DB_USER" -d "$DB_NAME" --no-owner --no-acl --exit-on-error \
    <"$backup/production-f3.dump" >"$RUN_DIR/pg-restore.log" 2>&1
  finished=$(date +%s)
  printf '%s\n' "$((finished-started))" >"$RUN_DIR/db-restore-seconds.txt"
  [[ "$(db_head)" == "$OLD_DB_HEAD" ]] || die 'full restore did not return DB to f3'
  FAILED_STAGE=restore_f3_files
  failed_data="$STACK_ROOT/data/openwebui.failed-$(timestamp)"
  mv "$DATA_DIR" "$failed_data"
  cp -a --reflink=auto "$backup/openwebui" "$DATA_DIR"
  printf '%s\n' "$failed_data" >"$RUN_DIR/displaced-data-dir.txt"
  docker exec "$REDIS_CONTAINER" redis-cli FLUSHALL >/dev/null
  FAILED_STAGE=start_old_after_full_restore
  compose_base up -d --no-deps --force-recreate "$WEBUI_SERVICE"
  wait_healthy "$WEBUI_CONTAINER" 180
  [[ "$(docker inspect "$WEBUI_CONTAINER" --format '{{.Image}}')" == "$OLD_IMAGE_ID" ]] || die 'old image was not restored after full DR'
  api_probe restore-f3
  touch "$RUN_DIR/restore-f3.ok"
  printf 'restore_f3=pass\nrun_dir=%s\ndb_head=%s\ndisplaced_data=%s\n' "$RUN_DIR" "$(db_head)" "$failed_data"
}

status() {
  load_db_identity
  printf 'webui=%s\n' "$(docker inspect "$WEBUI_CONTAINER" --format '{{.Id}}|{{.Image}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.RestartCount}}|{{.State.StartedAt}}')"
  docker inspect "$RUNTIME_CONTAINER" --format 'runtime={{.Id}}|{{.Image}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.RestartCount}}|{{.State.StartedAt}}' 2>/dev/null || printf 'runtime=absent\n'
  printf 'db_head=%s\npipe=%s\ncounts=%s\n' "$(db_head)" "$(pipe_fingerprint)" "$(db_counts)"
}

case "${1:-}" in
  preflight) preflight ;;
  backup) backup_live ;;
  switch) switch_live ;;
  rollback-fast) rollback_fast ;;
  restore-f3) restore_f3 ;;
  status) status ;;
  *) die 'usage: pr7-live-release.sh {preflight|backup|switch|rollback-fast|restore-f3|status}' ;;
esac
