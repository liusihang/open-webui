#!/usr/bin/env bash
set -euo pipefail

web_container='open-webui-pr7'
formal_container='open-webui'
db_container='openwebui-pr7-db'
db_user='webui_pr7'
database='webui_pr7'
expected_image_id='sha256:5a541612b86655ac1423b5e88109c47ff818819d99315cf7e51fa9a764e9ac05'
expected_formal_image_id='sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b'
expected_revision='a11c0d3f0bd0'
since='2026-07-29T11:47:03+08:00'
evidence_dir='/home/aiserver/staging/openwebui-pr7-eea11194ed-test/evidence/v011-functional-e3a9c97dd059-20260729-115100'

capture_container() {
    local container="$1"
    docker inspect --format '{{.Id}}|{{.Image}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.RestartCount}}' "${container}"
}

test -d "${evidence_dir}"
[[ "$(docker inspect --format '{{.Image}}' "${web_container}")" == "${expected_image_id}" ]]
[[ "$(docker inspect --format '{{.State.Health.Status}}' "${web_container}")" == 'healthy' ]]
[[ "$(docker inspect --format '{{.RestartCount}}' "${web_container}")" == '0' ]]
[[ "$(docker inspect --format '{{.Image}}' "${formal_container}")" == "${expected_formal_image_id}" ]]
[[ "$(docker inspect --format '{{.State.Health.Status}}' "${formal_container}")" == 'healthy' ]]
[[ "$(docker inspect --format '{{.RestartCount}}' "${formal_container}")" == '0' ]]

revision="$(docker exec "${db_container}" psql --username "${db_user}" --dbname "${database}" --tuples-only --no-align --set ON_ERROR_STOP=1 --command 'SELECT version_num FROM alembic_version ORDER BY version_num;')"
[[ "${revision}" == "${expected_revision}" ]]

port="$(docker inspect --format '{{(index (index .NetworkSettings.Ports "8080/tcp") 0).HostPort}}' "${web_container}")"
base_url="http://127.0.0.1:${port}"
curl --fail --silent --show-error "${base_url}/health" >"${evidence_dir}/final-health.json"
curl --fail --silent --show-error "${base_url}/health/db" >"${evidence_dir}/final-health-db.json"
curl --fail --silent --show-error "${base_url}/api/version" >"${evidence_dir}/final-version.json"

docker logs --since "${since}" --timestamps "${web_container}" >"${evidence_dir}/final-container.log" 2>&1
if grep -Eiq 'Traceback|worker.*(died|exited)|segmentation fault|out of memory|Task exception was never retrieved' "${evidence_dir}/final-container.log"; then
    grep -Ein 'Traceback|worker.*(died|exited)|segmentation fault|out of memory|Task exception was never retrieved' "${evidence_dir}/final-container.log" >"${evidence_dir}/final-fatal-log-signals.txt"
    exit 1
fi
grep -Ein 'ERROR|Exception| HTTP/[^ ]+" 5[0-9][0-9] ' "${evidence_dir}/final-container.log" >"${evidence_dir}/final-suspicious-log-signals.txt" || true

{
    printf 'image_id=%s\n' "${expected_image_id}"
    printf 'test_container=%s\n' "$(capture_container "${web_container}")"
    printf 'formal_container=%s\n' "$(capture_container "${formal_container}")"
    printf 'revision=%s\n' "${revision}"
    printf 'suspicious_log_lines=%s\n' "$(wc -l <"${evidence_dir}/final-suspicious-log-signals.txt" | tr -d ' ')"
    printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
} >"${evidence_dir}/FINAL_LOG_AUDIT_OK"

cat "${evidence_dir}/FINAL_LOG_AUDIT_OK"
if [[ -s "${evidence_dir}/final-suspicious-log-signals.txt" ]]; then
    cat "${evidence_dir}/final-suspicious-log-signals.txt"
fi
