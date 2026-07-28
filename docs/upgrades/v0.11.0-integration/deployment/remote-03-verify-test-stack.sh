#!/usr/bin/env bash
set -euo pipefail

IMAGE='open-webui:v011-test-4d3543438b-slim'
EXPECTED_SOURCE='4d3543438b6b147ae60f17a9b57b2355a0a026d0'
EXPECTED_BUILD='4d3543438b'
TEST_DIR='/home/aiserver/staging/openwebui-pr7-eea11194ed-test'
STATE_DIR="${TEST_DIR}/deploy-v011-${EXPECTED_BUILD}"
WEBUI_CONTAINER='open-webui-pr7'
DB_CONTAINER='openwebui-pr7-db'
DB_USER='webui_pr7'
SOURCE_DB='webui_pr7'
TARGET_REVISION='a11c0d3f0bd0'

test -s "${STATE_DIR}/CUTOVER_OK"

image_id="$(docker image inspect --format '{{.Id}}' "${IMAGE}")"
image_source="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "${IMAGE}")"
container_image="$(docker inspect --format '{{.Image}}' "${WEBUI_CONTAINER}")"
health="$(docker inspect --format '{{.State.Health.Status}}' "${WEBUI_CONTAINER}")"
restarts="$(docker inspect --format '{{.RestartCount}}' "${WEBUI_CONTAINER}")"

[[ "${image_source}" == "${EXPECTED_SOURCE}" ]]
[[ "${container_image}" == "${image_id}" ]]
[[ "${health}" == 'healthy' ]]
[[ "${restarts}" == '0' ]]

port="$(docker inspect --format '{{(index (index .NetworkSettings.Ports "8080/tcp") 0).HostPort}}' "${WEBUI_CONTAINER}")"
base_url="http://127.0.0.1:${port}"
curl --fail --silent --show-error "${base_url}/health" >"${STATE_DIR}/health.json"
curl --fail --silent --show-error "${base_url}/health/db" >"${STATE_DIR}/health-db.json"
curl --fail --silent --show-error "${base_url}/api/version" >"${STATE_DIR}/version.json"

python3 - "${STATE_DIR}/health.json" "${STATE_DIR}/health-db.json" "${STATE_DIR}/version.json" <<'PY'
from pathlib import Path
import json
import sys

health = json.loads(Path(sys.argv[1]).read_text())
health_db = json.loads(Path(sys.argv[2]).read_text())
version = json.loads(Path(sys.argv[3]).read_text())
if health != {'status': True}:
    raise SystemExit(f'/health was not true: {health!r}')
if health_db != {'status': True}:
    raise SystemExit(f'/health/db was not true: {health_db!r}')
if version.get('version') != '0.11.0':
    raise SystemExit(f"unexpected version: {version.get('version')!r}")
Path(sys.argv[3] + '.value').write_text(version['version'] + '\n')
print(json.dumps(version, sort_keys=True))
PY

revision="$(docker exec "${DB_CONTAINER}" psql --username "${DB_USER}" --dbname "${SOURCE_DB}" --tuples-only --no-align --set ON_ERROR_STOP=1 --command 'SELECT version_num FROM alembic_version ORDER BY version_num;')"
[[ "${revision}" == "${TARGET_REVISION}" ]]

docker exec -i "${WEBUI_CONTAINER}" python - <<'PY' | tee "${STATE_DIR}/orjson-runtime.txt"
from starlette.requests import Request
from starlette.responses import JSONResponse
from open_webui.main import app

request_module = Request.json.__module__
response_module = JSONResponse.render.__module__
expected = 'open_webui.utils.json_response'
print(f'app={type(app).__module__}.{type(app).__name__}')
print(f'Request.json={request_module}')
print(f'JSONResponse.render={response_module}')
if request_module != expected or response_module != expected:
    raise SystemExit('orjson HTTP patch is not active')
PY

run_record="$(docker exec "${DB_CONTAINER}" psql --username "${DB_USER}" --dbname "${SOURCE_DB}" --tuples-only --no-align --field-separator '|' --set ON_ERROR_STOP=1 --command 'SELECT ar.id, ar.user_id FROM agent_run ar JOIN "user" u ON u.id = ar.user_id WHERE u.role = '\''admin'\'' ORDER BY ar.created_at DESC LIMIT 1;')"
run_id="${run_record%%|*}"
owner_id="${run_record#*|}"
ordinary_id="$(docker exec "${DB_CONTAINER}" psql --username "${DB_USER}" --dbname "${SOURCE_DB}" --tuples-only --no-align --set ON_ERROR_STOP=1 --command "SELECT id FROM \"user\" WHERE role <> 'admin' AND id <> '${owner_id}' ORDER BY created_at DESC LIMIT 1;")"
test -n "${run_id}"
test -n "${ordinary_id}"

admin_token="$(docker exec -i "${WEBUI_CONTAINER}" python - "${owner_id}" <<'PY'
import sys
from open_webui.utils.auth import create_token
print(create_token({'id': sys.argv[1]}))
PY
)"
ordinary_token="$(docker exec -i "${WEBUI_CONTAINER}" python - "${ordinary_id}" <<'PY'
import sys
from open_webui.utils.auth import create_token
print(create_token({'id': sys.argv[1]}))
PY
)"

admin_headers="${STATE_DIR}/admin.headers"
ordinary_headers="${STATE_DIR}/ordinary.headers"
printf 'Authorization: Bearer %s\n' "${admin_token}" >"${admin_headers}"
printf 'Authorization: Bearer %s\n' "${ordinary_token}" >"${ordinary_headers}"
chmod 600 "${admin_headers}" "${ordinary_headers}"

http_code() {
  local headers="$1"
  local path="$2"
  curl --silent --show-error --output /dev/null --write-out '%{http_code}' --header "@${headers}" "${base_url}${path}"
}

ordinary_detail="$(http_code "${ordinary_headers}" "/api/agent/runs/${run_id}")"
ordinary_list="$(http_code "${ordinary_headers}" "/api/agent/runs/${run_id}/events/list?after_seq=0")"
ordinary_stream="$(curl --silent --show-error --max-time 5 --output /dev/null --write-out '%{http_code}' --header "@${ordinary_headers}" "${base_url}/api/agent/runs/${run_id}/events?after_seq=0" || true)"
admin_detail="$(http_code "${admin_headers}" "/api/agent/runs/${run_id}")"
admin_list="$(http_code "${admin_headers}" "/api/agent/runs/${run_id}/events/list?after_seq=0")"
admin_stream="$(curl --silent --show-error --max-time 5 --output /dev/null --write-out '%{http_code}' --header "@${admin_headers}" "${base_url}/api/agent/runs/${run_id}/events?after_seq=0" || true)"

[[ "${ordinary_detail}" == '404' ]]
[[ "${ordinary_list}" == '404' ]]
[[ "${ordinary_stream}" == '404' ]]
[[ "${admin_detail}" == '200' ]]
[[ "${admin_list}" == '200' ]]
[[ "${admin_stream}" == '200' ]]

rm -f "${admin_headers}" "${ordinary_headers}"
unset admin_token ordinary_token

{
  printf 'ordinary_detail=%s\n' "${ordinary_detail}"
  printf 'ordinary_list=%s\n' "${ordinary_list}"
  printf 'ordinary_stream=%s\n' "${ordinary_stream}"
  printf 'admin_detail=%s\n' "${admin_detail}"
  printf 'admin_list=%s\n' "${admin_list}"
  printf 'admin_stream=%s\n' "${admin_stream}"
} >"${STATE_DIR}/agent-run-authorization.txt"

docker logs --since "$(docker inspect --format '{{.State.StartedAt}}' "${WEBUI_CONTAINER}")" "${WEBUI_CONTAINER}" >"${STATE_DIR}/container-since-start.log" 2>&1
if grep -Eiq 'Traceback|worker.*(died|exited)|segmentation fault' "${STATE_DIR}/container-since-start.log"; then
  grep -Ein 'Traceback|worker.*(died|exited)|segmentation fault' "${STATE_DIR}/container-since-start.log" >"${STATE_DIR}/container-error-signals.txt"
  exit 1
fi

{
  printf 'image=%s\n' "${IMAGE}"
  printf 'image_id=%s\n' "${image_id}"
  printf 'source=%s\n' "${EXPECTED_SOURCE}"
  printf 'health=%s\n' "${health}"
  printf 'restarts=%s\n' "${restarts}"
  printf 'revision=%s\n' "${revision}"
  printf 'version=%s\n' "$(<"${STATE_DIR}/version.json.value")"
  printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
} >"${STATE_DIR}/VERIFY_OK"

cat "${STATE_DIR}/VERIFY_OK"
cat "${STATE_DIR}/agent-run-authorization.txt"
cat "${STATE_DIR}/orjson-runtime.txt"
