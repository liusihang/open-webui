#!/usr/bin/env bash
set -euo pipefail

web_container='open-webui-pr7'
formal_container='open-webui'
db_container='openwebui-pr7-db'
db_user='webui_pr7'
database='webui_pr7'
expected_image_id='sha256:bdbd84db321857ee8a8cd29326dd000b4c51d77256c2805fe2e817b987ffa63a'
expected_formal_image_id='sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b'
expected_revision='a11c0d3f0bd0'
expected_source='0bb586c8699d34c211a5a3686ab61bfe10f2ac90'
evidence_dir='/home/aiserver/staging/openwebui-pr7-eea11194ed-test/evidence/v011-persisted-chat-0bb586c8699d-20260729-132800'

capture_container() {
    local container="$1"
    docker inspect --format '{{.Id}}|{{.Image}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.RestartCount}}|{{.State.OOMKilled}}' "${container}"
}

test -s "${evidence_dir}/started-at.txt"
test -s "${evidence_dir}/DEPLOY_OK"
[[ "$(docker inspect --format '{{.Image}}' "${web_container}")" == "${expected_image_id}" ]]
[[ "$(docker inspect --format '{{.State.Health.Status}}' "${web_container}")" == 'healthy' ]]
[[ "$(docker inspect --format '{{.RestartCount}}' "${web_container}")" == '0' ]]
[[ "$(docker inspect --format '{{.State.OOMKilled}}' "${web_container}")" == 'false' ]]
[[ "$(docker inspect --format '{{.Image}}' "${formal_container}")" == "${expected_formal_image_id}" ]]
[[ "$(docker inspect --format '{{.State.Health.Status}}' "${formal_container}")" == 'healthy' ]]
[[ "$(docker inspect --format '{{.RestartCount}}' "${formal_container}")" == '0' ]]
[[ "$(docker inspect --format '{{.State.OOMKilled}}' "${formal_container}")" == 'false' ]]

revision="$(docker exec "${db_container}" psql --username "${db_user}" --dbname "${database}" --tuples-only --no-align --set ON_ERROR_STOP=1 --command 'SELECT version_num FROM alembic_version ORDER BY version_num;')"
[[ "${revision}" == "${expected_revision}" ]]

port="$(docker inspect --format '{{(index (index .NetworkSettings.Ports "8080/tcp") 0).HostPort}}' "${web_container}")"
base_url="http://127.0.0.1:${port}"
curl --fail --silent --show-error "${base_url}/health" >"${evidence_dir}/final-health.json"
curl --fail --silent --show-error "${base_url}/health/db" >"${evidence_dir}/final-health-db.json"
curl --fail --silent --show-error "${base_url}/api/version" >"${evidence_dir}/final-version.json"
python3 - "${evidence_dir}/final-health.json" "${evidence_dir}/final-health-db.json" "${evidence_dir}/final-version.json" <<'PY'
from pathlib import Path
import json
import sys

assert json.loads(Path(sys.argv[1]).read_text()) == {'status': True}
assert json.loads(Path(sys.argv[2]).read_text()) == {'status': True}
assert json.loads(Path(sys.argv[3]).read_text()).get('version') == '0.11.0'
PY

docker exec "${web_container}" grep -F -q "${expected_source}" /app/build/_app/version.json
docker exec "${web_container}" grep -R -F -q 'getConversationModeDraftCapabilitySnapshotForMode' /app/build/_app/immutable
docker exec "${web_container}" grep -R -F -q 'loadedChat.user_id === $user?.id' /app/build/_app/immutable

since="$(cat "${evidence_dir}/started-at.txt")"
docker logs --since "${since}" --timestamps "${web_container}" >"${evidence_dir}/final-container.log" 2>&1
fatal_pattern='Traceback|worker.*(died|exited)|segmentation fault|out of memory|Task exception was never retrieved| HTTP/[^ ]+" 5[0-9][0-9] '
if grep -Eiq "${fatal_pattern}" "${evidence_dir}/final-container.log"; then
    grep -Ein "${fatal_pattern}" "${evidence_dir}/final-container.log" >"${evidence_dir}/final-fatal-log-signals.txt"
    exit 1
fi

grep -Ein 'ERROR|Unhandled exception|ExceptionGroup' "${evidence_dir}/final-container.log" >"${evidence_dir}/final-suspicious-log-signals.txt" || true
if [[ -s "${evidence_dir}/final-suspicious-log-signals.txt" ]]; then
    cat "${evidence_dir}/final-suspicious-log-signals.txt"
    exit 1
fi

{
    printf 'image_id=%s\n' "${expected_image_id}"
    printf 'source=%s\n' "${expected_source}"
    printf 'test_container=%s\n' "$(capture_container "${web_container}")"
    printf 'formal_container=%s\n' "$(capture_container "${formal_container}")"
    printf 'revision=%s\n' "${revision}"
    printf 'fatal_log_lines=0\n'
    printf 'suspicious_log_lines=0\n'
    printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
} >"${evidence_dir}/FINAL_PERSISTED_CHAT_AUDIT_OK"

find "${evidence_dir}" -maxdepth 1 -type f ! -name manifest.sha256 -print0 \
    | sort -z \
    | xargs -0 sha256sum >"${evidence_dir}/manifest.sha256"

cat "${evidence_dir}/FINAL_PERSISTED_CHAT_AUDIT_OK"
sha256sum "${evidence_dir}/manifest.sha256"
