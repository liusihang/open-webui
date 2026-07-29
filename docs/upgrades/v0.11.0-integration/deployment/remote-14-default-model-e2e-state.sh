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
expected_default='bifrostapi.Cliproxy/gpt-5.5'
stack_root='/home/aiserver/staging/openwebui-pr7-eea11194ed-test'
reference_dir="${stack_root}/evidence/v011-functional-e3a9c97dd059-20260729-115100"
default_backup="${stack_root}/backups/pre-default-model-e3a9c97dd059-retry3"
evidence_dir="${stack_root}/evidence/v011-default-model-e3a9c97dd059-20260729-121500"
auth_state='/tmp/v011-default-model-auth-state-e3a9c97d.json'
since='2026-07-29T12:10:43+08:00'

cleanup() {
    rm -f "${auth_state}"
}
trap cleanup EXIT

capture_container() {
    local container="$1"
    docker inspect --format '{{.Id}}|{{.Image}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.RestartCount}}' "${container}"
}

db_scalar() {
    local sql="$1"
    docker exec "${db_container}" psql \
        --username "${db_user}" \
        --dbname "${database}" \
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

test -s "${reference_dir}/counts.after.tsv"
test -s "${reference_dir}/test.after.txt"
test -s "${reference_dir}/formal.after.txt"
test -s "${default_backup}/update-result.json"
test -x "${default_backup}/rollback-test-default-model.sh"
test ! -e "${evidence_dir}"
install -d -m 700 "${evidence_dir}"

capture_container "${web_container}" >"${evidence_dir}/test.current.txt"
capture_container "${formal_container}" >"${evidence_dir}/formal.current.txt"
cmp "${reference_dir}/test.after.txt" "${evidence_dir}/test.current.txt"
cmp "${reference_dir}/formal.after.txt" "${evidence_dir}/formal.current.txt"
[[ "$(docker inspect --format '{{.Image}}' "${web_container}")" == "${expected_image_id}" ]]
[[ "$(docker inspect --format '{{.State.Health.Status}}' "${web_container}")" == 'healthy' ]]
[[ "$(docker inspect --format '{{.RestartCount}}' "${web_container}")" == '0' ]]
[[ "$(docker inspect --format '{{.Image}}' "${formal_container}")" == "${expected_formal_image_id}" ]]
[[ "$(docker inspect --format '{{.State.Health.Status}}' "${formal_container}")" == 'healthy' ]]
[[ "$(docker inspect --format '{{.RestartCount}}' "${formal_container}")" == '0' ]]

capture_counts "${evidence_dir}/counts.current.tsv"
cmp "${reference_dir}/counts.after.tsv" "${evidence_dir}/counts.current.tsv"
revision="$(db_scalar 'SELECT version_num FROM alembic_version ORDER BY version_num;')"
[[ "${revision}" == "${expected_revision}" ]]

python3 - "${default_backup}/update-result.json" "${expected_default}" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1]))
assert data['new_default_models'] == sys.argv[2]
assert data['authenticated_public_sample_count'] == 20
assert data['authenticated_public_sample_values'] == [sys.argv[2]]
PY

port="$(docker inspect --format '{{(index (index .NetworkSettings.Ports "8080/tcp") 0).HostPort}}' "${web_container}")"
base_url="http://127.0.0.1:${port}"
curl --fail --silent --show-error "${base_url}/health" >"${evidence_dir}/health.json"
curl --fail --silent --show-error "${base_url}/health/db" >"${evidence_dir}/health-db.json"
curl --fail --silent --show-error "${base_url}/api/version" >"${evidence_dir}/version.json"

docker logs --since "${since}" --timestamps "${web_container}" >"${evidence_dir}/container.log" 2>&1
if grep -Eiq 'Traceback|worker.*(died|exited)|segmentation fault|out of memory|Task exception was never retrieved' "${evidence_dir}/container.log"; then
    grep -Ein 'Traceback|worker.*(died|exited)|segmentation fault|out of memory|Task exception was never retrieved' "${evidence_dir}/container.log" >"${evidence_dir}/fatal-log-signals.txt"
    exit 1
fi
if grep -Eiq ' HTTP/[^ ]+" 5[0-9][0-9] ' "${evidence_dir}/container.log"; then
    grep -Ein ' HTTP/[^ ]+" 5[0-9][0-9] ' "${evidence_dir}/container.log" >"${evidence_dir}/http-5xx-signals.txt"
    exit 1
fi
grep -Ein 'ERROR|Exception' "${evidence_dir}/container.log" >"${evidence_dir}/nonfatal-suspicious-signals.txt" || true

{
    printf 'image_id=%s\n' "${expected_image_id}"
    printf 'test_container=%s\n' "$(capture_container "${web_container}")"
    printf 'formal_container=%s\n' "$(capture_container "${formal_container}")"
    printf 'revision=%s\n' "${revision}"
    printf 'default_model=%s\n' "${expected_default}"
    printf 'authenticated_default_samples=20\n'
    printf 'chat_rows=%s\n' "$(awk -F '\t' '$1 == "chat" {print $2}' "${evidence_dir}/counts.current.tsv")"
    printf 'agent_runs=%s\n' "$(awk -F '\t' '$1 == "agent_run" {print $2}' "${evidence_dir}/counts.current.tsv")"
    printf 'agent_run_events=%s\n' "$(awk -F '\t' '$1 == "agent_run_event" {print $2}' "${evidence_dir}/counts.current.tsv")"
    printf 'nonfatal_suspicious_lines=%s\n' "$(wc -l <"${evidence_dir}/nonfatal-suspicious-signals.txt" | tr -d ' ')"
    printf 'rollback_script=%s\n' "${default_backup}/rollback-test-default-model.sh"
    printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
} >"${evidence_dir}/DEFAULT_MODEL_E2E_ACCEPTANCE_OK"

find "${evidence_dir}" -maxdepth 1 -type f ! -name manifest.sha256 -print0 \
    | sort -z \
    | xargs -0 sha256sum >"${evidence_dir}/manifest.sha256"

cat "${evidence_dir}/DEFAULT_MODEL_E2E_ACCEPTANCE_OK"
if [[ -s "${evidence_dir}/nonfatal-suspicious-signals.txt" ]]; then
    cat "${evidence_dir}/nonfatal-suspicious-signals.txt"
fi
sha256sum "${evidence_dir}/manifest.sha256"
