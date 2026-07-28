#!/usr/bin/env bash
set -euo pipefail

CANDIDATE_IMAGE='open-webui:v011-test-4d3543438b-slim'
OLD_IMAGE='open-webui:pr7-chat-agent-dual-mode-1d8dba8a7-slim'
EXPECTED_OLD_IMAGE_ID='sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b'
EXPECTED_BUILD='4d3543438b'
TEST_DIR='/home/aiserver/staging/openwebui-pr7-eea11194ed-test'
STATE_DIR="${TEST_DIR}/deploy-v011-${EXPECTED_BUILD}"
WEBUI_CONTAINER='open-webui-pr7'
DB_CONTAINER='openwebui-pr7-db'
DB_USER='webui_pr7'
SOURCE_DB='webui_pr7'
TEST_NETWORK='shared_network'
SOURCE_REVISION='c0d3b4a5e6f7'
ROLLBACK_OVERRIDE="${TEST_DIR}/compose.webui-v011-rollback.yaml"

test -s "${STATE_DIR}/webui-real.env"
test -s "${ROLLBACK_OVERRIDE}"
[[ "$(docker image inspect --format '{{.Id}}' "${OLD_IMAGE}")" == "${EXPECTED_OLD_IMAGE_ID}" ]]

project="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "${WEBUI_CONTAINER}")"
service="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "${WEBUI_CONTAINER}")"

docker stop --time 30 "${WEBUI_CONTAINER}" >/dev/null 2>&1 || true

docker run --rm \
  --network "${TEST_NETWORK}" \
  --env-file "${STATE_DIR}/webui-real.env" \
  --entrypoint alembic \
  --workdir /app/backend/open_webui \
  "${CANDIDATE_IMAGE}" downgrade "${SOURCE_REVISION}" | tee "${STATE_DIR}/rollback-downgrade.log"

revision="$(docker exec "${DB_CONTAINER}" psql --username "${DB_USER}" --dbname "${SOURCE_DB}" --tuples-only --no-align --set ON_ERROR_STOP=1 --command 'SELECT version_num FROM alembic_version ORDER BY version_num;')"
[[ "${revision}" == "${SOURCE_REVISION}" ]]

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
