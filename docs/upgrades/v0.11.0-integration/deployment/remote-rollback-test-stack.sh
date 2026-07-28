#!/usr/bin/env bash
set -euo pipefail

OLD_IMAGE='open-webui:pr7-chat-agent-dual-mode-1d8dba8a7-slim'
EXPECTED_OLD_IMAGE_ID='sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b'
EXPECTED_BUILD='4d3543438b'
TEST_DIR='/home/aiserver/staging/openwebui-pr7-eea11194ed-test'
STATE_DIR="${TEST_DIR}/deploy-v011-${EXPECTED_BUILD}"
WEBUI_CONTAINER='open-webui-pr7'
DB_CONTAINER='openwebui-pr7-db'
DB_USER='webui_pr7'
SOURCE_DB='webui_pr7'
SOURCE_REVISION='c0d3b4a5e6f7'
ROLLBACK_OVERRIDE="${TEST_DIR}/compose.webui-v011-rollback.yaml"

test -s "${STATE_DIR}/webui-real.env"
test -s "${ROLLBACK_OVERRIDE}"
test -s "${STATE_DIR}/quiesced-backup.path"
test -s "${STATE_DIR}/quiesced-rows.tsv"
[[ "$(docker image inspect --format '{{.Id}}' "${OLD_IMAGE}")" == "${EXPECTED_OLD_IMAGE_ID}" ]]

backup="$(<"${STATE_DIR}/quiesced-backup.path")"
[[ "${backup}" == "${STATE_DIR}/"* ]]
test -s "${backup}"
test -s "${backup}.sha256"
test -s "${backup}.restore-list"
sha256sum --check "${backup}.sha256"

project="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "${WEBUI_CONTAINER}")"
service="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "${WEBUI_CONTAINER}")"

docker stop --time 30 "${WEBUI_CONTAINER}" >/dev/null 2>&1 || true

db_scalar() {
  local sql="$1"
  docker exec "${DB_CONTAINER}" psql \
    --username "${DB_USER}" \
    --dbname "${SOURCE_DB}" \
    --tuples-only \
    --no-align \
    --set ON_ERROR_STOP=1 \
    --command "${sql}"
}

docker exec "${DB_CONTAINER}" psql \
  --username "${DB_USER}" \
  --dbname postgres \
  --tuples-only \
  --no-align \
  --set ON_ERROR_STOP=1 \
  --command "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${SOURCE_DB}' AND pid <> pg_backend_pid();" \
  >"${STATE_DIR}/rollback-terminated-connections.txt"
docker exec "${DB_CONTAINER}" dropdb \
  --username "${DB_USER}" \
  "${SOURCE_DB}"
docker exec "${DB_CONTAINER}" createdb \
  --username "${DB_USER}" \
  --owner "${DB_USER}" \
  "${SOURCE_DB}"
docker exec -i "${DB_CONTAINER}" pg_restore \
  --username "${DB_USER}" \
  --dbname "${SOURCE_DB}" \
  --no-owner \
  --no-privileges <"${backup}"

revision="$(db_scalar 'SELECT version_num FROM alembic_version ORDER BY version_num;')"
[[ "${revision}" == "${SOURCE_REVISION}" ]]
while IFS=$'\t' read -r table expected; do
  actual="$(db_scalar "SELECT count(*) FROM ${table};")"
  [[ "${actual}" == "${expected}" ]]
done <"${STATE_DIR}/quiesced-rows.tsv"

compose=(
  docker compose
  --project-name "${project}"
  --file "${TEST_DIR}/compose.yaml"
  --file "${TEST_DIR}/compose.webui-rebuild-eaff69b0d317.yaml"
  --file "${TEST_DIR}/compose.webui-eaff69-no-migrations.yaml"
  --file "${TEST_DIR}/compose.webui-4a4e43e206.yaml"
  --file "${TEST_DIR}/compose.agent-runtime-742f686182.yaml"
  --file "${ROLLBACK_OVERRIDE}"
)
"${compose[@]}" up --detach --no-deps --force-recreate "${service}"

deadline=$((SECONDS + 300))
while ((SECONDS < deadline)); do
  status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${WEBUI_CONTAINER}" 2>/dev/null || true)"
  [[ "${status}" == 'healthy' ]] && break
  [[ "${status}" == 'unhealthy' || "${status}" == 'exited' || "${status}" == 'dead' ]] && exit 1
  sleep 5
done

[[ "$(docker inspect --format '{{.State.Health.Status}}' "${WEBUI_CONTAINER}")" == 'healthy' ]]
[[ "$(docker inspect --format '{{.Image}}' "${WEBUI_CONTAINER}")" == "${EXPECTED_OLD_IMAGE_ID}" ]]
printf 'rolled_back_at=%s\nrevision=%s\nimage=%s\n' "$(date --iso-8601=seconds)" "${revision}" "${EXPECTED_OLD_IMAGE_ID}" >"${STATE_DIR}/ROLLBACK_OK"
cat "${STATE_DIR}/ROLLBACK_OK"
