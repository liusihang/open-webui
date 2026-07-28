#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

STACK_DIR=${STACK_DIR:-/srv/openwebui-migration}
COMPOSE_FILE=${COMPOSE_FILE:-${STACK_DIR}/compose.yaml}
WEB_CONTAINER=${WEB_CONTAINER:-open-webui}
DB_CONTAINER=${DB_CONTAINER:-openwebui-db}
BACKUP_ROOT=${BACKUP_ROOT:-/home/aiserver/staging/pr7-live-prep-20260727/backups}
EXPECTED_REVISION=${EXPECTED_REVISION:-f3a4b5c6d7e8}
RUN_ID=${RUN_ID:-$(date +%Y%m%d-%H%M%S)}
RUN_DIR=${BACKUP_ROOT}/${RUN_ID}
STATUS_FILE=${RUN_DIR}/status.env
LOG_FILE=${RUN_DIR}/backup.log
DUMP_FILE=${RUN_DIR}/openwebui-live-${RUN_ID}.dump
PARTIAL_FILE=${DUMP_FILE}.partial
LIST_FILE=${RUN_DIR}/openwebui-live-${RUN_ID}.restore-list.txt
MANIFEST_FILE=${RUN_DIR}/manifest.env

if [[ -e "${RUN_DIR}" ]]; then
  echo backup_run_directory_exists
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

on_exit() {
  local exit_code=$?
  if [[ ${exit_code} -ne 0 ]]; then
    rm -f "${PARTIAL_FILE}"
    write_status failed "exit_${exit_code}"
  fi
}
trap on_exit EXIT

if [[ "${LOG_STDOUT:-true}" == true ]]; then
  exec > >(tee -a "${LOG_FILE}") 2>&1
else
  exec >> "${LOG_FILE}" 2>&1
fi

write_status running preflight

db_user=
db_name=
while IFS= read -r entry; do
  case "${entry}" in
    POSTGRES_USER=*) db_user=${entry#*=} ;;
    POSTGRES_DB=*) db_name=${entry#*=} ;;
  esac
done < <(docker inspect "${DB_CONTAINER}" --format '{{range .Config.Env}}{{println .}}{{end}}')

if [[ -z "${db_user}" || -z "${db_name}" ]]; then
  echo database_identity_missing
  exit 1
fi

live_anchor=$(docker inspect "${WEB_CONTAINER}" --format 'container_id={{.Id}} image_ref={{.Config.Image}} image_id={{.Image}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restarts={{.RestartCount}} started={{.State.StartedAt}}')
printf '%s\n' "${live_anchor}"

current_revision=$(docker exec -i "${DB_CONTAINER}" psql -X -v ON_ERROR_STOP=1 -U "${db_user}" -d "${db_name}" -At <<'SQL'
SELECT version_num FROM alembic_version;
SQL
)
if [[ "${current_revision}" != "${EXPECTED_REVISION}" ]]; then
  printf 'unexpected_revision=%s expected=%s\n' "${current_revision}" "${EXPECTED_REVISION}"
  exit 1
fi

compose_sha256=$(sha256sum "${COMPOSE_FILE}" | awk '{print $1}')
cp --preserve=mode,timestamps "${COMPOSE_FILE}" "${RUN_DIR}/compose.yaml.before"

write_status running pg_dump
started_epoch=$(date +%s)
docker exec "${DB_CONTAINER}" pg_dump \
  -U "${db_user}" \
  -d "${db_name}" \
  --format=custom \
  --compress=6 \
  --no-owner \
  --no-acl > "${PARTIAL_FILE}"
mv "${PARTIAL_FILE}" "${DUMP_FILE}"

write_status running restore_list
docker exec -i "${DB_CONTAINER}" pg_restore --list < "${DUMP_FILE}" > "${LIST_FILE}"

write_status running checksum
dump_sha256=$(sha256sum "${DUMP_FILE}" | awk '{print $1}')
dump_size=$(stat --format '%s' "${DUMP_FILE}")
finished_epoch=$(date +%s)
duration_seconds=$((finished_epoch - started_epoch))

after_anchor=$(docker inspect "${WEB_CONTAINER}" --format 'container_id={{.Id}} image_ref={{.Config.Image}} image_id={{.Image}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restarts={{.RestartCount}} started={{.State.StartedAt}}')

if [[ "${after_anchor}" != "${live_anchor}" ]]; then
  echo live_anchor_changed_during_backup
  exit 1
fi

after_revision=$(docker exec -i "${DB_CONTAINER}" psql -X -v ON_ERROR_STOP=1 -U "${db_user}" -d "${db_name}" -At <<'SQL'
SELECT version_num FROM alembic_version;
SQL
)
if [[ "${after_revision}" != "${EXPECTED_REVISION}" ]]; then
  printf 'revision_changed_during_backup=%s\n' "${after_revision}"
  exit 1
fi

{
  printf 'run_id=%s\n' "${RUN_ID}"
  printf 'dump_file=%s\n' "${DUMP_FILE}"
  printf 'dump_size=%s\n' "${dump_size}"
  printf 'dump_sha256=%s\n' "${dump_sha256}"
  printf 'restore_list=%s\n' "${LIST_FILE}"
  printf 'source_revision=%s\n' "${current_revision}"
  printf 'compose_file=%s\n' "${COMPOSE_FILE}"
  printf 'compose_sha256=%s\n' "${compose_sha256}"
  printf 'duration_seconds=%s\n' "${duration_seconds}"
  printf '%s\n' "${live_anchor}"
} > "${MANIFEST_FILE}"

write_status complete verified
printf 'manifest=%s\n' "${MANIFEST_FILE}"
printf 'dump_size=%s\n' "${dump_size}"
printf 'dump_sha256=%s\n' "${dump_sha256}"
printf 'duration_seconds=%s\n' "${duration_seconds}"
