#!/usr/bin/env bash
set -euo pipefail

IMAGE='open-webui:v011-hotfix-e3a9c97dd059'
EXPECTED_IMAGE_ID='sha256:5a541612b86655ac1423b5e88109c47ff818819d99315cf7e51fa9a764e9ac05'
EXPECTED_SOURCE='e3a9c97dd059aa814ea4d34bf1aca910923cf2e8'
FORMAL_IMAGE_ID='sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b'
TARGET_REVISION='a11c0d3f0bd0'
STACK_DIR='/home/aiserver/staging/openwebui-pr7-eea11194ed-test'
EVIDENCE_DIR="${STACK_DIR}/evidence/v011-functional-e3a9c97dd059-20260729-115100"
WEB_CONTAINER='open-webui-pr7'
FORMAL_CONTAINER='open-webui'
DB_CONTAINER='openwebui-pr7-db'
DB_USER='webui_pr7'
SOURCE_DB='webui_pr7'
RUNNER_SOURCE='/tmp/container-acceptance-v011-runner.py'
API_PROBE_SOURCE='/tmp/container-functional-api-probe.py'
ADMIN_ID='b6826286-1251-4576-b3a0-e109ff085a61'

test -s "${STACK_DIR}/container-acceptance.py"
test -s "${STACK_DIR}/pr7_dual_mode_four_worker_probe.py"
test -s "${RUNNER_SOURCE}"
test -s "${API_PROBE_SOURCE}"
test ! -e "${EVIDENCE_DIR}"
install -d -m 700 "${EVIDENCE_DIR}"

capture_container() {
  local container="$1"
  docker inspect --format '{{.Id}}|{{.Image}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.RestartCount}}' "${container}"
}

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

capture_counts() {
  local output="$1"
  {
    printf 'chat\t%s\n' "$(db_scalar 'SELECT count(*) FROM chat;')"
    printf 'agent_run\t%s\n' "$(db_scalar 'SELECT count(*) FROM agent_run;')"
    printf 'agent_run_event\t%s\n' "$(db_scalar 'SELECT count(*) FROM agent_run_event;')"
  } >"${output}"
}

http_code() {
  local header_file="$1"
  local path="$2"
  curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
    --header "@${header_file}" "${base_url}${path}"
}

capture_container "${FORMAL_CONTAINER}" >"${EVIDENCE_DIR}/formal.before.txt"
capture_container "${WEB_CONTAINER}" >"${EVIDENCE_DIR}/test.before.txt"
[[ "$(docker inspect --format '{{.Image}}' "${FORMAL_CONTAINER}")" == "${FORMAL_IMAGE_ID}" ]]

image_id="$(docker image inspect --format '{{.Id}}' "${IMAGE}")"
image_source="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "${IMAGE}")"
container_image="$(docker inspect --format '{{.Image}}' "${WEB_CONTAINER}")"
health="$(docker inspect --format '{{.State.Health.Status}}' "${WEB_CONTAINER}")"
restarts="$(docker inspect --format '{{.RestartCount}}' "${WEB_CONTAINER}")"
[[ "${image_id}" == "${EXPECTED_IMAGE_ID}" ]]
[[ "${image_source}" == "${EXPECTED_SOURCE}" ]]
[[ "${container_image}" == "${EXPECTED_IMAGE_ID}" ]]
[[ "${health}" == 'healthy' ]]
[[ "${restarts}" == '0' ]]

port="$(docker inspect --format '{{(index (index .NetworkSettings.Ports "8080/tcp") 0).HostPort}}' "${WEB_CONTAINER}")"
base_url="http://127.0.0.1:${port}"
acceptance_started="$(date --iso-8601=seconds)"
curl --fail --silent --show-error "${base_url}/health" >"${EVIDENCE_DIR}/health.before.json"
curl --fail --silent --show-error "${base_url}/health/db" >"${EVIDENCE_DIR}/health-db.before.json"
curl --fail --silent --show-error "${base_url}/api/version" >"${EVIDENCE_DIR}/version.before.json"
python3 - "${EVIDENCE_DIR}/health.before.json" "${EVIDENCE_DIR}/health-db.before.json" "${EVIDENCE_DIR}/version.before.json" <<'PY'
from pathlib import Path
import json
import sys

health = json.loads(Path(sys.argv[1]).read_text())
health_db = json.loads(Path(sys.argv[2]).read_text())
version = json.loads(Path(sys.argv[3]).read_text())
assert health == {'status': True}
assert health_db == {'status': True}
assert version.get('version') == '0.11.0'
PY

revision="$(db_scalar 'SELECT version_num FROM alembic_version ORDER BY version_num;')"
columns="$(db_scalar "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' AND (table_name, column_name) IN (('chat_message', 'meta'), ('chat', 'current_message_id'), ('chat', 'variables'), ('user', 'variables'), ('automation', 'folder_id'));")"
indexes="$(db_scalar "SELECT count(*) FROM pg_indexes WHERE schemaname = 'public' AND indexname IN ('ix_memory_id_user_id', 'ix_automation_user_folder', 'uq_user_email_lower');")"
duplicates="$(db_scalar 'SELECT count(*) FROM (SELECT lower(email) FROM "user" WHERE email IS NOT NULL GROUP BY lower(email) HAVING count(*) > 1) duplicates;')"
admin_exists="$(db_scalar "SELECT count(*) FROM \"user\" WHERE id = '${ADMIN_ID}' AND role = 'admin';")"
ordinary_id="$(db_scalar "SELECT id FROM \"user\" WHERE role <> 'admin' AND id <> '${ADMIN_ID}' ORDER BY created_at DESC LIMIT 1;")"
[[ "${revision}" == "${TARGET_REVISION}" ]]
[[ "${columns}" == '5' ]]
[[ "${indexes}" == '3' ]]
[[ "${duplicates}" == '0' ]]
[[ "${admin_exists}" == '1' ]]
test -n "${ordinary_id}"
capture_counts "${EVIDENCE_DIR}/counts.before.tsv"

docker cp "${STACK_DIR}/container-acceptance.py" "${WEB_CONTAINER}:/tmp/container-acceptance.py"
docker cp "${STACK_DIR}/pr7_dual_mode_four_worker_probe.py" "${WEB_CONTAINER}:/tmp/pr7_dual_mode_four_worker_probe.py"
docker cp "${RUNNER_SOURCE}" "${WEB_CONTAINER}:/tmp/container-acceptance-v011-runner.py"
docker cp "${API_PROBE_SOURCE}" "${WEB_CONTAINER}:/tmp/container-functional-api-probe.py"

docker exec -i -e PYTHONPATH=/app/backend "${WEB_CONTAINER}" \
  python /tmp/container-functional-api-probe.py "${ADMIN_ID}" \
  >"${EVIDENCE_DIR}/api-surfaces.json"

docker exec -i "${WEB_CONTAINER}" python - <<'PY' >"${EVIDENCE_DIR}/orjson-runtime.txt"
from starlette.requests import Request
from starlette.responses import JSONResponse
from open_webui.main import app

expected = 'open_webui.utils.json_response'
print(f'app={type(app).__module__}.{type(app).__name__}')
print(f'Request.json={Request.json.__module__}')
print(f'JSONResponse.render={JSONResponse.render.__module__}')
if Request.json.__module__ != expected or JSONResponse.render.__module__ != expected:
    raise SystemExit('orjson HTTP patch is not active')
PY

docker exec -i -e PYTHONPATH=/app/backend "${WEB_CONTAINER}" \
  python /tmp/container-acceptance-v011-runner.py \
  2>&1 | tee "${EVIDENCE_DIR}/runner.log"
docker cp "${WEB_CONTAINER}:/tmp/pr7-latest-stack-acceptance.json" "${EVIDENCE_DIR}/acceptance.json"
chmod 600 "${EVIDENCE_DIR}/acceptance.json"
sha256sum "${EVIDENCE_DIR}/acceptance.json" >"${EVIDENCE_DIR}/acceptance.json.sha256"

python3 - "${EVIDENCE_DIR}/acceptance.json" >"${EVIDENCE_DIR}/acceptance-derived.tsv" <<'PY'
from pathlib import Path
import json
import re
import sys

payload = json.loads(Path(sys.argv[1]).read_text())
assert payload['ok'] is True
assert len(payload['worker_evidence']['container_worker_pids']) == 4
assert len(payload['worker_evidence']['observations']) == 4
assert payload['chat']['done'] is True
assert payload['chat']['marker_seen'] is True
assert payload['chat']['content_delta_count'] >= 2
assert payload['agent']['state'] == 'completed'
assert payload['agent']['marker_seen'] is True
required = {'run.running', 'final.started', 'final.delta', 'run.completed'}
assert required.issubset(payload['agent']['event_counts'])
assert payload['agent']['event_counts']['run.completed'] == 1
run_id = payload['agent']['run_id']
assert re.fullmatch(r'[0-9a-f-]{36}', run_id)
print(f'run_id\t{run_id}')
print(f'expected_event_count\t{sum(payload["agent"]["event_counts"].values())}')
print(f'model_id\t{payload["model_id"]}')
PY

run_id="$(awk -F '\t' '$1 == "run_id" {print $2}' "${EVIDENCE_DIR}/acceptance-derived.tsv")"
expected_event_count="$(awk -F '\t' '$1 == "expected_event_count" {print $2}' "${EVIDENCE_DIR}/acceptance-derived.tsv")"
run_record="$(db_scalar "SELECT state || '|' || user_id FROM agent_run WHERE id = '${run_id}';")"
run_event_count="$(db_scalar "SELECT count(*) FROM agent_run_event WHERE run_id = '${run_id}';")"
[[ "${run_record}" == "completed|${ADMIN_ID}" ]]
[[ "${run_event_count}" == "${expected_event_count}" ]]
capture_counts "${EVIDENCE_DIR}/counts.after.tsv"

pre_chat="$(awk -F '\t' '$1 == "chat" {print $2}' "${EVIDENCE_DIR}/counts.before.tsv")"
pre_runs="$(awk -F '\t' '$1 == "agent_run" {print $2}' "${EVIDENCE_DIR}/counts.before.tsv")"
pre_events="$(awk -F '\t' '$1 == "agent_run_event" {print $2}' "${EVIDENCE_DIR}/counts.before.tsv")"
post_chat="$(awk -F '\t' '$1 == "chat" {print $2}' "${EVIDENCE_DIR}/counts.after.tsv")"
post_runs="$(awk -F '\t' '$1 == "agent_run" {print $2}' "${EVIDENCE_DIR}/counts.after.tsv")"
post_events="$(awk -F '\t' '$1 == "agent_run_event" {print $2}' "${EVIDENCE_DIR}/counts.after.tsv")"
[[ "${post_chat}" == "${pre_chat}" ]]
[[ "${post_runs}" == "$((pre_runs + 1))" ]]
[[ "${post_events}" == "$((pre_events + run_event_count))" ]]

admin_token="$(docker exec -i "${WEB_CONTAINER}" python - "${ADMIN_ID}" <<'PY'
import sys
from open_webui.utils.auth import create_token
print(create_token({'id': sys.argv[1]}))
PY
)"
ordinary_token="$(docker exec -i "${WEB_CONTAINER}" python - "${ordinary_id}" <<'PY'
import sys
from open_webui.utils.auth import create_token
print(create_token({'id': sys.argv[1]}))
PY
)"
admin_headers="${EVIDENCE_DIR}/admin.headers"
ordinary_headers="${EVIDENCE_DIR}/ordinary.headers"
printf 'Authorization: Bearer %s\n' "${admin_token}" >"${admin_headers}"
printf 'Authorization: Bearer %s\n' "${ordinary_token}" >"${ordinary_headers}"
chmod 600 "${admin_headers}" "${ordinary_headers}"

admin_detail="$(http_code "${admin_headers}" "/api/agent/runs/${run_id}")"
admin_events="$(http_code "${admin_headers}" "/api/agent/runs/${run_id}/events/list?after_seq=0")"
ordinary_detail="$(http_code "${ordinary_headers}" "/api/agent/runs/${run_id}")"
ordinary_events="$(http_code "${ordinary_headers}" "/api/agent/runs/${run_id}/events/list?after_seq=0")"
[[ "${admin_detail}" == '200' ]]
[[ "${admin_events}" == '200' ]]
[[ "${ordinary_detail}" == '404' ]]
[[ "${ordinary_events}" == '404' ]]
{
  printf 'admin_detail=%s\n' "${admin_detail}"
  printf 'admin_events=%s\n' "${admin_events}"
  printf 'ordinary_detail=%s\n' "${ordinary_detail}"
  printf 'ordinary_events=%s\n' "${ordinary_events}"
} >"${EVIDENCE_DIR}/agent-authorization.txt"
rm -f "${admin_headers}" "${ordinary_headers}"
unset admin_token ordinary_token

curl --fail --silent --show-error "${base_url}/health" >"${EVIDENCE_DIR}/health.after.json"
curl --fail --silent --show-error "${base_url}/health/db" >"${EVIDENCE_DIR}/health-db.after.json"
curl --fail --silent --show-error "${base_url}/api/version" >"${EVIDENCE_DIR}/version.after.json"
cmp "${EVIDENCE_DIR}/health.before.json" "${EVIDENCE_DIR}/health.after.json"
cmp "${EVIDENCE_DIR}/health-db.before.json" "${EVIDENCE_DIR}/health-db.after.json"
cmp "${EVIDENCE_DIR}/version.before.json" "${EVIDENCE_DIR}/version.after.json"

docker logs --since "${acceptance_started}" --timestamps "${WEB_CONTAINER}" >"${EVIDENCE_DIR}/container.log" 2>&1
if grep -Eiq 'Traceback|worker.*(died|exited)|segmentation fault|out of memory|Task exception was never retrieved' "${EVIDENCE_DIR}/container.log"; then
  grep -Ein 'Traceback|worker.*(died|exited)|segmentation fault|out of memory|Task exception was never retrieved' "${EVIDENCE_DIR}/container.log" >"${EVIDENCE_DIR}/fatal-log-signals.txt"
  exit 1
fi

capture_container "${FORMAL_CONTAINER}" >"${EVIDENCE_DIR}/formal.after.txt"
capture_container "${WEB_CONTAINER}" >"${EVIDENCE_DIR}/test.after.txt"
cmp "${EVIDENCE_DIR}/formal.before.txt" "${EVIDENCE_DIR}/formal.after.txt"
cmp "${EVIDENCE_DIR}/test.before.txt" "${EVIDENCE_DIR}/test.after.txt"
[[ "$(docker inspect --format '{{.Image}}' "${FORMAL_CONTAINER}")" == "${FORMAL_IMAGE_ID}" ]]
[[ "$(docker inspect --format '{{.State.Health.Status}}' "${WEB_CONTAINER}")" == 'healthy' ]]
[[ "$(docker inspect --format '{{.RestartCount}}' "${WEB_CONTAINER}")" == '0' ]]

{
  printf 'image=%s\n' "${IMAGE}"
  printf 'image_id=%s\n' "${image_id}"
  printf 'source=%s\n' "${image_source}"
  printf 'test_container=%s\n' "$(capture_container "${WEB_CONTAINER}")"
  printf 'formal_container=%s\n' "$(capture_container "${FORMAL_CONTAINER}")"
  printf 'revision=%s\n' "${revision}"
  printf 'target_columns=%s\n' "${columns}"
  printf 'target_indexes=%s\n' "${indexes}"
  printf 'normalized_email_duplicates=%s\n' "${duplicates}"
  printf 'agent_run_id=%s\n' "${run_id}"
  printf 'agent_run_events=%s\n' "${run_event_count}"
  printf 'acceptance_sha256=%s\n' "$(cut -d' ' -f1 "${EVIDENCE_DIR}/acceptance.json.sha256")"
  printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
} >"${EVIDENCE_DIR}/FUNCTIONAL_ACCEPTANCE_OK"

cat "${EVIDENCE_DIR}/FUNCTIONAL_ACCEPTANCE_OK"
cat "${EVIDENCE_DIR}/api-surfaces.json"
cat "${EVIDENCE_DIR}/agent-authorization.txt"
