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
TEST_NETWORK='openwebui-pr7_default'
SOURCE_REVISION='c0d3b4a5e6f7'
TARGET_REVISION='a11c0d3f0bd0'
OVERRIDE="${TEST_DIR}/compose.webui-v011-${EXPECTED_BUILD}.yaml"

test -s "${STATE_DIR}/REHEARSAL_OK"
test -s "${OVERRIDE}"

image_source="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "${IMAGE}")"
image_id="$(docker image inspect --format '{{.Id}}' "${IMAGE}")"
old_image="$(docker inspect --format '{{.Image}}' "${WEBUI_CONTAINER}")"
project="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "${WEBUI_CONTAINER}")"
service="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "${WEBUI_CONTAINER}")"

[[ "${image_source}" == "${EXPECTED_SOURCE}" ]]
[[ "${old_image}" == "${EXPECTED_OLD_IMAGE}" ]]
[[ "${service}" == 'open-webui-pr7' ]]

capture_container() {
  local container="$1"
  docker inspect --format '{{.Id}}|{{.Image}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.RestartCount}}' "${container}"
}

capture_non_webui_project() {
  docker ps -a \
    --filter "label=com.docker.compose.project=${project}" \
    --format '{{.Names}}' \
    | sort \
    | while read -r container; do
        if [[ "${container}" != "${WEBUI_CONTAINER}" ]]; then
          printf '%s\t%s\n' "${container}" "$(capture_container "${container}")"
        fi
      done
}

capture_non_webui_project >"${STATE_DIR}/non-webui.before.tsv"
capture_container open-webui >"${STATE_DIR}/formal-live.before.txt"
capture_container "${WEBUI_CONTAINER}" >"${STATE_DIR}/test-webui.before.txt"

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

[[ "$(db_scalar 'SELECT version_num FROM alembic_version ORDER BY version_num;')" == "${SOURCE_REVISION}" ]]

source_env="${STATE_DIR}/webui-real.env"
docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${WEBUI_CONTAINER}" >"${source_env}"
chmod 600 "${source_env}"

docker stop --time 30 "${WEBUI_CONTAINER}"
[[ "$(docker inspect --format '{{.State.Status}}' "${WEBUI_CONTAINER}")" == 'exited' ]]
[[ "$(docker inspect --format '{{.State.Health.Status}}' "${DB_CONTAINER}")" == 'healthy' ]]

quiesced_rows="${STATE_DIR}/quiesced-rows.tsv"
{
  printf 'chat\t%s\n' "$(db_scalar 'SELECT count(*) FROM chat;')"
  printf 'agent_run\t%s\n' "$(db_scalar 'SELECT count(*) FROM agent_run;')"
  printf 'agent_run_event\t%s\n' "$(db_scalar 'SELECT count(*) FROM agent_run_event;')"
} >"${quiesced_rows}"

timestamp="$(date +%Y%m%dT%H%M%S%z)"
backup="${STATE_DIR}/webui_pr7-pre-v011-quiesced-${timestamp}.dump"
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
printf '%s\n' "${backup}" >"${STATE_DIR}/quiesced-backup.path"

docker run --rm \
  --network "${TEST_NETWORK}" \
  --env-file "${source_env}" \
  --entrypoint alembic \
  --workdir /app/backend/open_webui \
  "${IMAGE}" upgrade head | tee "${STATE_DIR}/real-upgrade.log"

[[ "$(db_scalar 'SELECT version_num FROM alembic_version ORDER BY version_num;')" == "${TARGET_REVISION}" ]]
columns="$(db_scalar "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' AND (table_name, column_name) IN (('chat_message', 'meta'), ('chat', 'current_message_id'), ('chat', 'variables'), ('user', 'variables'), ('automation', 'folder_id'));")"
indexes="$(db_scalar "SELECT count(*) FROM pg_indexes WHERE schemaname = 'public' AND indexname IN ('ix_memory_id_user_id', 'ix_automation_user_folder', 'uq_user_email_lower');")"
[[ "${columns}" == '5' ]]
[[ "${indexes}" == '3' ]]
while IFS=$'\t' read -r table expected; do
  actual="$(db_scalar "SELECT count(*) FROM ${table};")"
  [[ "${actual}" == "${expected}" ]]
done <"${quiesced_rows}"

compose=(
  docker compose
  --project-name "${project}"
  --file "${TEST_DIR}/compose.yaml"
  --file "${TEST_DIR}/compose.webui-rebuild-eaff69b0d317.yaml"
  --file "${TEST_DIR}/compose.webui-eaff69-no-migrations.yaml"
  --file "${TEST_DIR}/compose.webui-4a4e43e206.yaml"
  --file "${TEST_DIR}/compose.agent-runtime-742f686182.yaml"
  --file "${OVERRIDE}"
)

"${compose[@]}" up --detach --no-deps --force-recreate "${service}"

deadline=$((SECONDS + 300))
while ((SECONDS < deadline)); do
  status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${WEBUI_CONTAINER}" 2>/dev/null || true)"
  if [[ "${status}" == 'healthy' ]]; then
    break
  fi
  if [[ "${status}" == 'unhealthy' || "${status}" == 'exited' || "${status}" == 'dead' ]]; then
    docker logs --tail 200 "${WEBUI_CONTAINER}" >"${STATE_DIR}/failed-container.log" 2>&1 || true
    exit 1
  fi
  sleep 5
done

[[ "$(docker inspect --format '{{.State.Health.Status}}' "${WEBUI_CONTAINER}")" == 'healthy' ]]
[[ "$(docker inspect --format '{{.Image}}' "${WEBUI_CONTAINER}")" == "${image_id}" ]]
[[ "$(docker inspect --format '{{.RestartCount}}' "${WEBUI_CONTAINER}")" == '0' ]]

capture_non_webui_project >"${STATE_DIR}/non-webui.after.tsv"
capture_container open-webui >"${STATE_DIR}/formal-live.after.txt"
cmp "${STATE_DIR}/non-webui.before.tsv" "${STATE_DIR}/non-webui.after.tsv"
cmp "${STATE_DIR}/formal-live.before.txt" "${STATE_DIR}/formal-live.after.txt"

{
  printf 'image=%s\n' "${IMAGE}"
  printf 'image_id=%s\n' "${image_id}"
  printf 'source=%s\n' "${EXPECTED_SOURCE}"
  printf 'backup=%s\n' "${backup}"
  printf 'backup_sha256=%s\n' "$(cut -d' ' -f1 "${backup}.sha256")"
  printf 'revision=%s\n' "${TARGET_REVISION}"
  printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
} >"${STATE_DIR}/CUTOVER_OK"

cat "${STATE_DIR}/CUTOVER_OK"
cat "${quiesced_rows}"
