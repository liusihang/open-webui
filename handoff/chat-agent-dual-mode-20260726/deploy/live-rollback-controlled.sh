#!/usr/bin/env bash
set -Eeuo pipefail

STACK_DIR=${STACK_DIR:-/srv/openwebui-migration}
COMPOSE_FILE=${COMPOSE_FILE:-${STACK_DIR}/compose.yaml}
RELEASE_DIR=${RELEASE_DIR:-/home/aiserver/staging/pr7-live-prep-20260727/release}
MIGRATION_SCRIPT=${MIGRATION_SCRIPT:-${RELEASE_DIR}/live-migrate-controlled.sh}
WEB_CONTAINER=${WEB_CONTAINER:-open-webui}
DB_CONTAINER=${DB_CONTAINER:-openwebui-db}
EXPECTED_COMPOSE_SHA256=${EXPECTED_COMPOSE_SHA256:-7fff73a9037687460bd6c27669e9224203241546928173106c9999d6b3425da1}
EXPECTED_OLD_IMAGE_ID=${EXPECTED_OLD_IMAGE_ID:-sha256:7ec820b71fa94205b273cb8cd00344a130e1921ae8e643ba6192b0e58933bd45}
SOURCE_REVISION=${SOURCE_REVISION:-f3a4b5c6d7e8}
TARGET_REVISION=${TARGET_REVISION:-c0d3b4a5e6f7}

if [[ "${CONFIRM_LIVE_ROLLBACK:-}" != "rollback-pr7-dual-mode-on-aiserver-live" ]]; then
  echo live_rollback_confirmation_missing
  exit 1
fi
if [[ "${CONFIRM_ROLLBACK_DATA_LOSS:-}" != "drop-new-agent-and-mode-profile-schema" ]]; then
  echo rollback_data_ack_missing
  exit 1
fi
if [[ "$(sha256sum "${COMPOSE_FILE}" | awk '{print $1}')" != "${EXPECTED_COMPOSE_SHA256}" ]]; then
  echo compose_anchor_mismatch
  exit 1
fi

db_user=
db_name=
while IFS= read -r entry; do
  case "${entry}" in
    POSTGRES_USER=*) db_user=${entry#*=} ;;
    POSTGRES_DB=*) db_name=${entry#*=} ;;
  esac
done < <(docker inspect "${DB_CONTAINER}" --format '{{range .Config.Env}}{{println .}}{{end}}')

database_revision() {
  docker exec -i "${DB_CONTAINER}" psql -X -v ON_ERROR_STOP=1 -U "${db_user}" -d "${db_name}" -At <<'SQL'
SELECT version_num FROM alembic_version;
SQL
}

current_revision=$(database_revision)
if [[ "${current_revision}" != "${TARGET_REVISION}" && "${current_revision}" != "${SOURCE_REVISION}" ]]; then
  echo unsupported_rollback_revision
  exit 1
fi

docker stop "${WEB_CONTAINER}" >/dev/null

if [[ "${current_revision}" == "${TARGET_REVISION}" ]]; then
  MIGRATION_ACTION=downgrade \
  CONFIRM_LIVE_DATABASE_MIGRATION=downgrade-c0-to-f3-on-aiserver-live \
  CONFIRM_ROLLBACK_DATA_LOSS=drop-new-agent-and-mode-profile-schema \
    "${MIGRATION_SCRIPT}"
fi

docker compose -f "${COMPOSE_FILE}" up -d --no-deps --force-recreate open-webui

for attempt in $(seq 1 150); do
  health=$(docker inspect "${WEB_CONTAINER}" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}')
  if [[ "${health}" == healthy ]]; then
    break
  fi
  if [[ "${health}" == unhealthy || ${attempt} -eq 150 ]]; then
    echo rollback_health_failed
    exit 1
  fi
  sleep 2
done

if [[ "$(docker inspect "${WEB_CONTAINER}" --format '{{.Image}}')" != "${EXPECTED_OLD_IMAGE_ID}" ]]; then
  echo rollback_image_mismatch
  exit 1
fi
if [[ "$(database_revision)" != "${SOURCE_REVISION}" ]]; then
  echo rollback_database_revision_mismatch
  exit 1
fi

safe_env=$(docker inspect "${WEB_CONTAINER}" --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^(ENABLE_DB_MIGRATIONS|UVICORN_WORKERS)=')
if ! grep -Fxq 'ENABLE_DB_MIGRATIONS=true' <<< "${safe_env}" || ! grep -Fxq 'UVICORN_WORKERS=4' <<< "${safe_env}"; then
  echo rollback_runtime_env_mismatch
  exit 1
fi
process_table=$(docker top "${WEB_CONTAINER}" -eo pid,ppid,args)
master_pid=$(awk '$0 ~ /-m uvicorn/ && $0 ~ /--workers 4/ {print $1; exit}' <<< "${process_table}")
worker_pids=$(awk -v master_pid="${master_pid}" '$2 == master_pid && $0 !~ /resource_tracker/ {print $1}' <<< "${process_table}")
worker_count=$(awk 'NF {count += 1} END {print count + 0}' <<< "${worker_pids}")
if [[ -z "${master_pid}" || "${worker_count}" != 4 ]]; then
  echo rollback_worker_verification_failed
  exit 1
fi

printf 'rollback_image_id=%s\n' "${EXPECTED_OLD_IMAGE_ID}"
printf 'rollback_revision=%s\n' "${SOURCE_REVISION}"
printf 'rollback_workers=%s\n' "${worker_count}"
printf 'rollback_worker_pids=%s\n' "${worker_pids//$'\n'/ }"
