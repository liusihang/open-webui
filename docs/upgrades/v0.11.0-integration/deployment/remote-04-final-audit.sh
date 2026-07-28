#!/usr/bin/env bash
set -euo pipefail

IMAGE='open-webui:v011-test-4d3543438b-slim'
EXPECTED_SOURCE='4d3543438b6b147ae60f17a9b57b2355a0a026d0'
EXPECTED_BUILD='4d3543438b'
TEST_DIR='/home/aiserver/staging/openwebui-pr7-eea11194ed-test'
STATE_DIR="${TEST_DIR}/deploy-v011-${EXPECTED_BUILD}"
EVIDENCE="${TEST_DIR}/evidence/v011-4d3543438b-20260728/acceptance.json"
WEBUI_CONTAINER='open-webui-pr7'
DB_CONTAINER='openwebui-pr7-db'
DB_USER='webui_pr7'
SOURCE_DB='webui_pr7'
TARGET_REVISION='a11c0d3f0bd0'

capture_container() {
  local container="$1"
  docker inspect --format '{{.Id}}|{{.Image}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.RestartCount}}' "${container}"
}

project="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "${WEBUI_CONTAINER}")"
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

capture_non_webui_project >"${STATE_DIR}/non-webui.final.tsv"
capture_container open-webui >"${STATE_DIR}/formal-live.final.txt"
cmp "${STATE_DIR}/non-webui.before.tsv" "${STATE_DIR}/non-webui.final.tsv"
cmp "${STATE_DIR}/formal-live.before.txt" "${STATE_DIR}/formal-live.final.txt"

image_id="$(docker image inspect --format '{{.Id}}' "${IMAGE}")"
image_source="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "${IMAGE}")"
image_build="$(docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${IMAGE}" | awk -F= '$1 == "WEBUI_BUILD_VERSION" {print substr($0, index($0, "=") + 1)}')"
[[ "${image_source}" == "${EXPECTED_SOURCE}" ]]
[[ "${image_build}" == "${EXPECTED_BUILD}" ]]
[[ "$(docker inspect --format '{{.Image}}' "${WEBUI_CONTAINER}")" == "${image_id}" ]]
[[ "$(docker inspect --format '{{.State.Health.Status}}' "${WEBUI_CONTAINER}")" == 'healthy' ]]
[[ "$(docker inspect --format '{{.RestartCount}}' "${WEBUI_CONTAINER}")" == '0' ]]

workers="$(docker exec -i "${WEBUI_CONTAINER}" python - <<'PY'
from pathlib import Path

pids = []
for path in Path('/proc').glob('[0-9]*'):
    try:
        command = path.joinpath('cmdline').read_bytes().replace(b'\0', b' ').decode()
    except OSError:
        continue
    if 'multiprocessing.spawn' in command and 'spawn_main' in command:
        pids.append(path.name)
print(','.join(sorted(pids, key=int)))
PY
)"
[[ "$(tr ',' '\n' <<<"${workers}" | sed '/^$/d' | wc -l | tr -d ' ')" == '4' ]]

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

revision="$(db_scalar 'SELECT version_num FROM alembic_version ORDER BY version_num;')"
columns="$(db_scalar "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' AND (table_name, column_name) IN (('chat_message', 'meta'), ('chat', 'current_message_id'), ('chat', 'variables'), ('user', 'variables'), ('automation', 'folder_id'));")"
indexes="$(db_scalar "SELECT count(*) FROM pg_indexes WHERE schemaname = 'public' AND indexname IN ('ix_memory_id_user_id', 'ix_automation_user_folder', 'uq_user_email_lower');")"
duplicates="$(db_scalar 'SELECT count(*) FROM (SELECT lower(email) FROM "user" WHERE email IS NOT NULL GROUP BY lower(email) HAVING count(*) > 1) duplicates;')"
[[ "${revision}" == "${TARGET_REVISION}" ]]
[[ "${columns}" == '5' ]]
[[ "${indexes}" == '3' ]]
[[ "${duplicates}" == '0' ]]

chat_rows="$(db_scalar 'SELECT count(*) FROM chat;')"
run_rows="$(db_scalar 'SELECT count(*) FROM agent_run;')"
event_rows="$(db_scalar 'SELECT count(*) FROM agent_run_event;')"

backup="$(<"${STATE_DIR}/quiesced-backup.path")"
[[ "${backup}" == "${STATE_DIR}/"* ]]
sha256sum --check "${backup}.sha256" >"${STATE_DIR}/final-backup-check.txt"

test -s "${EVIDENCE}"
python3 - "${EVIDENCE}" <<'PY'
from pathlib import Path
import json
import sys

payload = json.loads(Path(sys.argv[1]).read_text())
assert payload['ok'] is True
assert len(payload['worker_evidence']['container_worker_pids']) == 4
assert len(payload['worker_evidence']['observations']) == 4
assert payload['chat']['done'] is True
assert payload['chat']['marker_seen'] is True
assert payload['agent']['state'] == 'completed'
assert payload['agent']['marker_seen'] is True
assert payload['agent']['event_counts']['run.completed'] == 1
PY

port="$(docker inspect --format '{{(index (index .NetworkSettings.Ports "8080/tcp") 0).HostPort}}' "${WEBUI_CONTAINER}")"
base_url="http://127.0.0.1:${port}"
curl --fail --silent --show-error "${base_url}/health" >"${STATE_DIR}/final-health.json"
curl --fail --silent --show-error "${base_url}/health/db" >"${STATE_DIR}/final-health-db.json"
curl --fail --silent --show-error "${base_url}/api/version" >"${STATE_DIR}/final-version.json"
python3 - "${STATE_DIR}/final-health.json" "${STATE_DIR}/final-health-db.json" "${STATE_DIR}/final-version.json" <<'PY'
from pathlib import Path
import json
import sys

health = json.loads(Path(sys.argv[1]).read_text())
health_db = json.loads(Path(sys.argv[2]).read_text())
version = json.loads(Path(sys.argv[3]).read_text())
assert health == {'status': True}
assert health_db == {'status': True}
assert version['version'] == '0.11.0'
PY

started="$(docker inspect --format '{{.State.StartedAt}}' "${WEBUI_CONTAINER}")"
docker logs --since "${started}" --timestamps "${WEBUI_CONTAINER}" >"${STATE_DIR}/final-container.log" 2>&1
if grep -Eiq 'Traceback|worker.*(died|exited)|segmentation fault|out of memory' "${STATE_DIR}/final-container.log"; then
  grep -Ein 'Traceback|worker.*(died|exited)|segmentation fault|out of memory' "${STATE_DIR}/final-container.log" >"${STATE_DIR}/final-error-signals.txt"
  exit 1
fi

{
  printf 'image=%s\n' "${IMAGE}"
  printf 'image_id=%s\n' "${image_id}"
  printf 'source=%s\n' "${image_source}"
  printf 'build=%s\n' "${image_build}"
  printf 'container=%s\n' "$(capture_container "${WEBUI_CONTAINER}")"
  printf 'workers=%s\n' "${workers}"
  printf 'revision=%s\n' "${revision}"
  printf 'columns=%s\n' "${columns}"
  printf 'indexes=%s\n' "${indexes}"
  printf 'normalized_email_duplicates=%s\n' "${duplicates}"
  printf 'chat_rows=%s\n' "${chat_rows}"
  printf 'agent_run_rows=%s\n' "${run_rows}"
  printf 'agent_run_event_rows=%s\n' "${event_rows}"
  printf 'acceptance_sha256=%s\n' "$(sha256sum "${EVIDENCE}" | cut -d' ' -f1)"
  printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
} >"${STATE_DIR}/FINAL_AUDIT_OK"

cat "${STATE_DIR}/FINAL_AUDIT_OK"
cat "${STATE_DIR}/final-version.json"
printf '\n'
