#!/usr/bin/env bash
set -euo pipefail

IMAGE='open-webui:v011-test-4d3543438b-slim'
EXPECTED_SOURCE='4d3543438b6b147ae60f17a9b57b2355a0a026d0'
EXPECTED_BUILD='4d3543438b'
EXPECTED_OLD_IMAGE='sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b'
TEST_DIR='/home/aiserver/staging/openwebui-pr7-eea11194ed-test'
STATE_DIR="${TEST_DIR}/deploy-v011-${EXPECTED_BUILD}"
WEBUI_CONTAINER='open-webui-pr7'
DB_CONTAINER='openwebui-pr7-db'
DB_USER='webui_pr7'
SOURCE_DB='webui_pr7'
REHEARSAL_DB='webui_pr7_v011_rehearsal_4d3543438b'
TEST_NETWORK='openwebui-pr7_default'
SOURCE_REVISION='c0d3b4a5e6f7'
TARGET_REVISION='a11c0d3f0bd0'

mkdir -p "${STATE_DIR}"
chmod 700 "${STATE_DIR}"

image_id="$(docker image inspect --format '{{.Id}}' "${IMAGE}")"
image_source="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "${IMAGE}")"
image_build="$(docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${IMAGE}" | awk -F= '$1 == "WEBUI_BUILD_VERSION" {print substr($0, index($0, "=") + 1)}')"
test_image="$(docker inspect --format '{{.Image}}' "${WEBUI_CONTAINER}")"

[[ "${image_source}" == "${EXPECTED_SOURCE}" ]]
[[ "${image_build}" == "${EXPECTED_BUILD}" ]]
[[ "${test_image}" == "${EXPECTED_OLD_IMAGE}" ]]
docker network inspect "${TEST_NETWORK}" >/dev/null

db_scalar() {
  local database="$1"
  local sql="$2"
  docker exec "${DB_CONTAINER}" psql \
    --username "${DB_USER}" \
    --dbname "${database}" \
    --tuples-only \
    --no-align \
    --set ON_ERROR_STOP=1 \
    --command "${sql}"
}

revision="$(db_scalar "${SOURCE_DB}" 'SELECT version_num FROM alembic_version ORDER BY version_num;')"
[[ "${revision}" == "${SOURCE_REVISION}" ]]

duplicates="$(db_scalar "${SOURCE_DB}" 'SELECT lower(email) FROM "user" WHERE email IS NOT NULL GROUP BY lower(email) HAVING count(*) > 1 LIMIT 1;')"
[[ -z "${duplicates}" ]]

baseline_file="${STATE_DIR}/rehearsal-baseline.tsv"
{
  printf 'chat\t%s\n' "$(db_scalar "${SOURCE_DB}" 'SELECT count(*) FROM chat;')"
  printf 'agent_run\t%s\n' "$(db_scalar "${SOURCE_DB}" 'SELECT count(*) FROM agent_run;')"
  printf 'agent_run_event\t%s\n' "$(db_scalar "${SOURCE_DB}" 'SELECT count(*) FROM agent_run_event;')"
} >"${baseline_file}"

timestamp="$(date +%Y%m%dT%H%M%S%z)"
backup="${STATE_DIR}/webui_pr7-pre-v011-rehearsal-${timestamp}.dump"
docker exec "${DB_CONTAINER}" pg_dump \
  --username "${DB_USER}" \
  --dbname "${SOURCE_DB}" \
  --format custom \
  --no-owner \
  --no-privileges >"${backup}"

test -s "${backup}"
sha256sum "${backup}" >"${backup}.sha256"
docker exec -i "${DB_CONTAINER}" pg_restore --list <"${backup}" >"${backup}.restore-list"
test -s "${backup}.restore-list"
printf '%s\n' "${backup}" >"${STATE_DIR}/rehearsal-backup.path"

docker exec "${DB_CONTAINER}" dropdb \
  --username "${DB_USER}" \
  --if-exists \
  "${REHEARSAL_DB}"
docker exec "${DB_CONTAINER}" createdb \
  --username "${DB_USER}" \
  --owner "${DB_USER}" \
  "${REHEARSAL_DB}"
docker exec -i "${DB_CONTAINER}" pg_restore \
  --username "${DB_USER}" \
  --dbname "${REHEARSAL_DB}" \
  --no-owner \
  --no-privileges <"${backup}"

restored_revision="$(db_scalar "${REHEARSAL_DB}" 'SELECT version_num FROM alembic_version ORDER BY version_num;')"
[[ "${restored_revision}" == "${SOURCE_REVISION}" ]]

source_env="${STATE_DIR}/webui-current.env"
rehearsal_env="${STATE_DIR}/webui-rehearsal.env"
docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${WEBUI_CONTAINER}" >"${source_env}"
chmod 600 "${source_env}"

python3 - "${source_env}" "${rehearsal_env}" "${REHEARSAL_DB}" <<'PY'
from pathlib import Path
import sys
from urllib.parse import urlsplit, urlunsplit

source = Path(sys.argv[1])
target = Path(sys.argv[2])
database = sys.argv[3]
output = []
found = False
for raw_line in source.read_text().splitlines():
    if raw_line.startswith('DATABASE_URL='):
        found = True
        value = raw_line.split('=', 1)[1]
        parsed = urlsplit(value)
        if not parsed.scheme.startswith('postgres'):
            raise SystemExit('DATABASE_URL is not PostgreSQL')
        output.append(
            'DATABASE_URL='
            + urlunsplit((parsed.scheme, parsed.netloc, '/' + database, parsed.query, parsed.fragment))
        )
    elif raw_line.startswith('ENABLE_DB_MIGRATIONS='):
        output.append('ENABLE_DB_MIGRATIONS=false')
    else:
        output.append(raw_line)
if not found:
    raise SystemExit('DATABASE_URL is missing')
target.write_text('\n'.join(output) + '\n')
target.chmod(0o600)
PY

run_alembic() {
  docker run --rm \
    --network "${TEST_NETWORK}" \
    --env-file "${rehearsal_env}" \
    --entrypoint alembic \
    --workdir /app/backend/open_webui \
    "${IMAGE}" "$@"
}

assert_rows_match() {
  local database="$1"
  while IFS=$'\t' read -r table expected; do
    actual="$(db_scalar "${database}" "SELECT count(*) FROM ${table};")"
    [[ "${actual}" == "${expected}" ]]
  done <"${baseline_file}"
}

assert_target_schema_present() {
  local database="$1"
  local columns indexes
  columns="$(db_scalar "${database}" "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' AND (table_name, column_name) IN (('chat_message', 'meta'), ('chat', 'current_message_id'), ('chat', 'variables'), ('user', 'variables'), ('automation', 'folder_id'));")"
  indexes="$(db_scalar "${database}" "SELECT count(*) FROM pg_indexes WHERE schemaname = 'public' AND indexname IN ('ix_memory_id_user_id', 'ix_automation_user_folder', 'uq_user_email_lower');")"
  [[ "${columns}" == '5' ]]
  [[ "${indexes}" == '3' ]]
}

assert_target_schema_absent() {
  local database="$1"
  local columns indexes
  columns="$(db_scalar "${database}" "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' AND (table_name, column_name) IN (('chat_message', 'meta'), ('chat', 'current_message_id'), ('chat', 'variables'), ('user', 'variables'), ('automation', 'folder_id'));")"
  indexes="$(db_scalar "${database}" "SELECT count(*) FROM pg_indexes WHERE schemaname = 'public' AND indexname IN ('ix_memory_id_user_id', 'ix_automation_user_folder', 'uq_user_email_lower');")"
  [[ "${columns}" == '0' ]]
  [[ "${indexes}" == '0' ]]
}

assert_target_schema_absent "${REHEARSAL_DB}"
assert_rows_match "${REHEARSAL_DB}"

run_alembic upgrade head | tee "${STATE_DIR}/rehearsal-upgrade-1.log"
[[ "$(db_scalar "${REHEARSAL_DB}" 'SELECT version_num FROM alembic_version ORDER BY version_num;')" == "${TARGET_REVISION}" ]]
assert_target_schema_present "${REHEARSAL_DB}"
assert_rows_match "${REHEARSAL_DB}"

# The two branches share a pre-v0.11 ancestor. Alembic cannot remove the
# official branch back to that ancestor without also selecting descendants on
# the custom branch. Rehearse the actual rollback mechanism instead: replace
# only the disposable database with the verified pre-upgrade snapshot.
{
  printf 'backup=%s\n' "${backup}"
  printf 'backup_sha256=%s\n' "$(cut -d' ' -f1 "${backup}.sha256")"
  printf 'restore_started_at=%s\n' "$(date --iso-8601=seconds)"
} >"${STATE_DIR}/rehearsal-restore-rollback.log"
docker exec "${DB_CONTAINER}" dropdb \
  --username "${DB_USER}" \
  "${REHEARSAL_DB}"
docker exec "${DB_CONTAINER}" createdb \
  --username "${DB_USER}" \
  --owner "${DB_USER}" \
  "${REHEARSAL_DB}"
docker exec -i "${DB_CONTAINER}" pg_restore \
  --username "${DB_USER}" \
  --dbname "${REHEARSAL_DB}" \
  --no-owner \
  --no-privileges <"${backup}"
printf 'restore_completed_at=%s\n' "$(date --iso-8601=seconds)" >>"${STATE_DIR}/rehearsal-restore-rollback.log"
[[ "$(db_scalar "${REHEARSAL_DB}" 'SELECT version_num FROM alembic_version ORDER BY version_num;')" == "${SOURCE_REVISION}" ]]
assert_target_schema_absent "${REHEARSAL_DB}"
assert_rows_match "${REHEARSAL_DB}"

run_alembic upgrade head | tee "${STATE_DIR}/rehearsal-upgrade-2.log"
[[ "$(db_scalar "${REHEARSAL_DB}" 'SELECT version_num FROM alembic_version ORDER BY version_num;')" == "${TARGET_REVISION}" ]]
assert_target_schema_present "${REHEARSAL_DB}"
assert_rows_match "${REHEARSAL_DB}"

{
  printf 'image=%s\n' "${IMAGE}"
  printf 'image_id=%s\n' "${image_id}"
  printf 'source=%s\n' "${EXPECTED_SOURCE}"
  printf 'backup=%s\n' "${backup}"
  printf 'backup_sha256=%s\n' "$(cut -d' ' -f1 "${backup}.sha256")"
  printf 'database=%s\n' "${REHEARSAL_DB}"
  printf 'revision=%s\n' "${TARGET_REVISION}"
  printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
} >"${STATE_DIR}/REHEARSAL_OK"

cat "${STATE_DIR}/REHEARSAL_OK"
cat "${baseline_file}"
