#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

MANIFEST_FILE=${MANIFEST_FILE:?MANIFEST_FILE is required}
WORK_ROOT=${WORK_ROOT:-/home/aiserver/staging/pr7-live-prep-20260727}
CANDIDATE_IMAGE=${CANDIDATE_IMAGE:-open-webui:pr7-chat-agent-dual-mode-1d8dba8a7-slim}
EXPECTED_CANDIDATE_IMAGE_ID=${EXPECTED_CANDIDATE_IMAGE_ID:-sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b}
EXPECTED_CANDIDATE_REVISION=${EXPECTED_CANDIDATE_REVISION:-1d8dba8a77e6e8adc5952891bac83a2a7c5a4804}
OLD_IMAGE=${OLD_IMAGE:-open-webui:live-f8106c651-to-v0102-pr7-b3-7-onlyoffice-mergefix-slim-20260707013738}
SOURCE_DB_CONTAINER=${SOURCE_DB_CONTAINER:-openwebui-db}
SOURCE_REVISION=${SOURCE_REVISION:-f3a4b5c6d7e8}
TARGET_REVISION=${TARGET_REVISION:-c0d3b4a5e6f7}
RUN_ID=${RUN_ID:-$(date +%Y%m%d-%H%M%S)}
RUN_DIR=${WORK_ROOT}/rehearsals/${RUN_ID}
STATUS_FILE=${RUN_DIR}/status.env
LOG_FILE=${RUN_DIR}/rehearsal.log
REPORT_FILE=${RUN_DIR}/report.env
DB_CONTAINER=pr7-live-prep-db-${RUN_ID}
NETWORK=pr7-live-prep-${RUN_ID}
VOLUME=pr7-live-prep-db-${RUN_ID}
DB_ENV=${RUN_DIR}/postgres.env
APP_ENV=${RUN_DIR}/candidate.env
CLONE_USER=prep_user
CLONE_DB=prep_db
cleanup_required=true

if [[ -e "${RUN_DIR}" ]]; then
  echo rehearsal_run_directory_exists
  exit 1
fi
mkdir -p "${RUN_DIR}"
touch "${LOG_FILE}"

write_status() {
  local state=$1
  local detail=${2:-}
  {
    printf 'state=%s\n' "${state}"
    printf 'detail=%s\n' "${detail}"
    printf 'run_id=%s\n' "${RUN_ID}"
    printf 'pid=%s\n' "$$"
    printf 'updated_at=%s\n' "$(date --iso-8601=seconds)"
  } > "${STATUS_FILE}"
}

cleanup() {
  local exit_code=$?
  rm -f "${DB_ENV}" "${APP_ENV}"
  if [[ "${cleanup_required}" == true ]]; then
    docker rm -f "${DB_CONTAINER}" >/dev/null 2>&1 || true
    docker network rm "${NETWORK}" >/dev/null 2>&1 || true
    docker volume rm "${VOLUME}" >/dev/null 2>&1 || true
  fi
  if [[ ${exit_code} -ne 0 ]]; then
    write_status failed "exit_${exit_code}"
  fi
}
trap cleanup EXIT

if [[ "${LOG_STDOUT:-true}" == true ]]; then
  exec > >(tee -a "${LOG_FILE}") 2>&1
else
  exec >> "${LOG_FILE}" 2>&1
fi

write_status running preflight

if [[ ! -f "${MANIFEST_FILE}" ]]; then
  echo manifest_missing
  exit 1
fi

dump_file=$(awk -F= '$1 == "dump_file" {sub(/^[^=]*=/, ""); print; exit}' "${MANIFEST_FILE}")
expected_dump_sha256=$(awk -F= '$1 == "dump_sha256" {print $2; exit}' "${MANIFEST_FILE}")
manifest_revision=$(awk -F= '$1 == "source_revision" {print $2; exit}' "${MANIFEST_FILE}")

if [[ -z "${dump_file}" || ! -f "${dump_file}" ]]; then
  echo dump_missing
  exit 1
fi
if [[ "${manifest_revision}" != "${SOURCE_REVISION}" ]]; then
  printf 'manifest_revision=%s expected=%s\n' "${manifest_revision}" "${SOURCE_REVISION}"
  exit 1
fi

actual_dump_sha256=$(sha256sum "${dump_file}" | awk '{print $1}')
if [[ "${actual_dump_sha256}" != "${expected_dump_sha256}" ]]; then
  echo dump_checksum_mismatch
  exit 1
fi

candidate_image_id=$(docker image inspect "${CANDIDATE_IMAGE}" --format '{{.Id}}')
candidate_revision=$(docker image inspect "${CANDIDATE_IMAGE}" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
if [[ "${candidate_image_id}" != "${EXPECTED_CANDIDATE_IMAGE_ID}" || "${candidate_revision}" != "${EXPECTED_CANDIDATE_REVISION}" ]]; then
  printf 'candidate_identity_mismatch id=%s revision=%s\n' "${candidate_image_id}" "${candidate_revision}"
  exit 1
fi

db_image=$(docker inspect "${SOURCE_DB_CONTAINER}" --format '{{.Config.Image}}')
db_password=$(openssl rand -hex 32)
webui_secret=$(openssl rand -hex 32)
{
  printf 'POSTGRES_USER=%s\n' "${CLONE_USER}"
  printf 'POSTGRES_PASSWORD=%s\n' "${db_password}"
  printf 'POSTGRES_DB=%s\n' "${CLONE_DB}"
} > "${DB_ENV}"
{
  printf 'DATABASE_URL=postgresql://%s:%s@%s:5432/%s\n' "${CLONE_USER}" "${db_password}" "${DB_CONTAINER}" "${CLONE_DB}"
  printf 'WEBUI_SECRET_KEY=%s\n' "${webui_secret}"
  printf 'ENABLE_DB_MIGRATIONS=false\n'
} > "${APP_ENV}"

docker network create --internal "${NETWORK}" >/dev/null
docker volume create "${VOLUME}" >/dev/null
docker run -d \
  --name "${DB_CONTAINER}" \
  --network "${NETWORK}" \
  --env-file "${DB_ENV}" \
  --mount "type=volume,source=${VOLUME},destination=/var/lib/postgresql/data" \
  "${db_image}" >/dev/null

write_status running wait_database
for attempt in $(seq 1 120); do
  if docker exec "${DB_CONTAINER}" psql -X -v ON_ERROR_STOP=1 -U "${CLONE_USER}" -d "${CLONE_DB}" -At <<'SQL' >/dev/null 2>&1
SELECT 1;
SQL
  then
    break
  fi
  if [[ ${attempt} -eq 120 ]]; then
    echo clone_database_not_ready
    exit 1
  fi
  sleep 2
done

write_status running restore
restore_started=$(date +%s)
docker exec -i "${DB_CONTAINER}" pg_restore \
  --exit-on-error \
  --no-owner \
  --no-acl \
  -U "${CLONE_USER}" \
  -d "${CLONE_DB}" < "${dump_file}"
restore_finished=$(date +%s)
restore_duration=$((restore_finished - restore_started))

query() {
  docker exec -i "${DB_CONTAINER}" psql -X -v ON_ERROR_STOP=1 -U "${CLONE_USER}" -d "${CLONE_DB}" -At
}

before_revision=$(query <<'SQL'
SELECT version_num FROM alembic_version;
SQL
)
if [[ "${before_revision}" != "${SOURCE_REVISION}" ]]; then
  printf 'restored_revision=%s expected=%s\n' "${before_revision}" "${SOURCE_REVISION}"
  exit 1
fi

before_chat_signature=$(query <<'SQL'
SELECT count(*) || ':' || COALESCE(sum(hashtextextended(id::text, 0)::numeric), 0) FROM chat;
SQL
)
docker exec "${DB_CONTAINER}" pg_dump -U "${CLONE_USER}" -d "${CLONE_DB}" --schema-only --no-owner --no-acl > "${RUN_DIR}/schema-before.sql"

write_status running upgrade
upgrade_started=$(date +%s)
docker run --rm \
  --network "${NETWORK}" \
  --env-file "${APP_ENV}" \
  --entrypoint alembic \
  --workdir /app/backend/open_webui \
  "${CANDIDATE_IMAGE}" upgrade "${TARGET_REVISION}"
upgrade_finished=$(date +%s)
upgrade_duration=$((upgrade_finished - upgrade_started))

after_upgrade_revision=$(query <<'SQL'
SELECT version_num FROM alembic_version;
SQL
)
if [[ "${after_upgrade_revision}" != "${TARGET_REVISION}" ]]; then
  printf 'upgraded_revision=%s expected=%s\n' "${after_upgrade_revision}" "${TARGET_REVISION}"
  exit 1
fi

upgrade_invariants=$(query <<'SQL'
SELECT
  (SELECT count(*) FROM conversation_mode_profile_head) || ':' ||
  (SELECT count(*) FROM conversation_mode_profile_revision) || ':' ||
  (SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'chat' AND column_name = 'mode_profile_revision_id') || ':' ||
  (SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name IN ('agent_run','agent_run_event','agent_artifact','agent_run_operation','agent_run_decision_execution','conversation_mode_profile_head','conversation_mode_profile_revision','conversation_mode_profile_temporary_binding')) || ':' ||
  (SELECT count(*) FROM pg_constraint WHERE conname = 'uq_conv_mode_profile_temp_user_conversation') || ':' ||
  (SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'agent_run' AND column_name IN ('pending_user_input_id','pending_user_input_expires_at')) || ':' ||
  (SELECT count(*) FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'agent_run' AND indexname = 'ix_agent_run_user_input_deadline');
SQL
)
if [[ "${upgrade_invariants}" != "2:2:1:8:1:2:1" ]]; then
  printf 'upgrade_invariants=%s expected=2:2:1:8:1:2:1\n' "${upgrade_invariants}"
  exit 1
fi

after_upgrade_chat_signature=$(query <<'SQL'
SELECT count(*) || ':' || COALESCE(sum(hashtextextended(id::text, 0)::numeric), 0) FROM chat;
SQL
)
if [[ "${after_upgrade_chat_signature}" != "${before_chat_signature}" ]]; then
  echo chat_signature_changed_during_upgrade
  exit 1
fi

write_status running downgrade
downgrade_started=$(date +%s)
docker run --rm \
  --network "${NETWORK}" \
  --env-file "${APP_ENV}" \
  --entrypoint alembic \
  --workdir /app/backend/open_webui \
  "${CANDIDATE_IMAGE}" downgrade "${SOURCE_REVISION}"
downgrade_finished=$(date +%s)
downgrade_duration=$((downgrade_finished - downgrade_started))

after_downgrade_revision=$(query <<'SQL'
SELECT version_num FROM alembic_version;
SQL
)
if [[ "${after_downgrade_revision}" != "${SOURCE_REVISION}" ]]; then
  printf 'downgraded_revision=%s expected=%s\n' "${after_downgrade_revision}" "${SOURCE_REVISION}"
  exit 1
fi

downgrade_invariants=$(query <<'SQL'
SELECT
  (SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'chat' AND column_name = 'mode_profile_revision_id') || ':' ||
  (SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name IN ('agent_run','agent_run_event','agent_artifact','agent_run_operation','agent_run_decision_execution','conversation_mode_profile_head','conversation_mode_profile_revision','conversation_mode_profile_temporary_binding'));
SQL
)
if [[ "${downgrade_invariants}" != "0:0" ]]; then
  printf 'downgrade_invariants=%s expected=0:0\n' "${downgrade_invariants}"
  exit 1
fi

after_downgrade_chat_signature=$(query <<'SQL'
SELECT count(*) || ':' || COALESCE(sum(hashtextextended(id::text, 0)::numeric), 0) FROM chat;
SQL
)
if [[ "${after_downgrade_chat_signature}" != "${before_chat_signature}" ]]; then
  echo chat_signature_changed_after_downgrade
  exit 1
fi

docker exec "${DB_CONTAINER}" pg_dump -U "${CLONE_USER}" -d "${CLONE_DB}" --schema-only --no-owner --no-acl > "${RUN_DIR}/schema-after.sql"
if ! cmp -s "${RUN_DIR}/schema-before.sql" "${RUN_DIR}/schema-after.sql"; then
  diff -u "${RUN_DIR}/schema-before.sql" "${RUN_DIR}/schema-after.sql" > "${RUN_DIR}/schema-diff.txt" || true
  echo schema_roundtrip_mismatch
  exit 1
fi

old_image_heads=$(docker run --rm \
  --network "${NETWORK}" \
  --env-file "${APP_ENV}" \
  --entrypoint alembic \
  --workdir /app/backend/open_webui \
  "${OLD_IMAGE}" heads)
old_image_current=$(docker run --rm \
  --network "${NETWORK}" \
  --env-file "${APP_ENV}" \
  --entrypoint alembic \
  --workdir /app/backend/open_webui \
  "${OLD_IMAGE}" current)
docker run --rm \
  --network "${NETWORK}" \
  --env-file "${APP_ENV}" \
  --entrypoint alembic \
  --workdir /app/backend/open_webui \
  "${OLD_IMAGE}" upgrade head

schema_sha256=$(sha256sum "${RUN_DIR}/schema-before.sql" | awk '{print $1}')
{
  printf 'run_id=%s\n' "${RUN_ID}"
  printf 'dump_file=%s\n' "${dump_file}"
  printf 'dump_sha256=%s\n' "${actual_dump_sha256}"
  printf 'restored_revision=%s\n' "${before_revision}"
  printf 'upgraded_revision=%s\n' "${after_upgrade_revision}"
  printf 'downgraded_revision=%s\n' "${after_downgrade_revision}"
  printf 'upgrade_invariants=%s\n' "${upgrade_invariants}"
  printf 'downgrade_invariants=%s\n' "${downgrade_invariants}"
  printf 'chat_signature=%s\n' "${before_chat_signature}"
  printf 'schema_sha256=%s\n' "${schema_sha256}"
  printf 'schema_roundtrip=identical\n'
  printf 'restore_duration_seconds=%s\n' "${restore_duration}"
  printf 'upgrade_duration_seconds=%s\n' "${upgrade_duration}"
  printf 'downgrade_duration_seconds=%s\n' "${downgrade_duration}"
  printf 'old_image_heads=%s\n' "${old_image_heads//$'\n'/ }"
  printf 'old_image_current=%s\n' "${old_image_current//$'\n'/ }"
} > "${REPORT_FILE}"

write_status running cleanup
docker rm -f "${DB_CONTAINER}" >/dev/null
docker network rm "${NETWORK}" >/dev/null
docker volume rm "${VOLUME}" >/dev/null
cleanup_required=false

write_status complete verified
printf 'report=%s\n' "${REPORT_FILE}"
printf 'restore_duration_seconds=%s\n' "${restore_duration}"
printf 'upgrade_duration_seconds=%s\n' "${upgrade_duration}"
printf 'downgrade_duration_seconds=%s\n' "${downgrade_duration}"
