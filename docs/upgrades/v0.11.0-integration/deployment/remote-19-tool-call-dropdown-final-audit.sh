#!/usr/bin/env bash
set -euo pipefail

web_container="open-webui-pr7"
formal_container="open-webui"
db_container="openwebui-pr7-db"
expected_web_image="sha256:c5fecd259933068ac435e37c4698ed84a027e67fab58817b41b813052dd6aaca"
expected_formal_image="sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b"
expected_source="9643bd7ad189ddc1e65fd6996d8b5c047e6e06e8"
evidence_dir="/home/aiserver/staging/openwebui-pr7-eea11194ed-test/evidence/v011-tool-call-dropdown-9643bd7ad189-20260729-142000"

[[ "$(docker inspect --format '{{.Image}}' "${web_container}")" == "${expected_web_image}" ]]
[[ "$(docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "${web_container}")" == "${expected_source}" ]]
[[ "$(docker inspect --format '{{.State.Health.Status}}' "${web_container}")" == "healthy" ]]
[[ "$(docker inspect --format '{{.RestartCount}}' "${web_container}")" == "0" ]]
[[ "$(docker inspect --format '{{.State.OOMKilled}}' "${web_container}")" == "false" ]]
[[ "$(docker inspect --format '{{.Image}}' "${formal_container}")" == "${expected_formal_image}" ]]
[[ "$(docker inspect --format '{{.State.Health.Status}}' "${formal_container}")" == "healthy" ]]
[[ "$(docker inspect --format '{{.RestartCount}}' "${formal_container}")" == "0" ]]

port="$(docker inspect --format '{{(index (index .NetworkSettings.Ports "8080/tcp") 0).HostPort}}' "${web_container}")"
health="$(curl --fail --silent --show-error "http://127.0.0.1:${port}/health")"
health_db="$(curl --fail --silent --show-error "http://127.0.0.1:${port}/health/db")"
version="$(curl --fail --silent --show-error "http://127.0.0.1:${port}/api/version")"
frontend_version="$(curl --fail --silent --show-error "http://127.0.0.1:${port}/_app/version.json")"
python3 - "${health}" "${health_db}" "${version}" "${frontend_version}" "${expected_source}" <<'PY'
import json
import sys

assert json.loads(sys.argv[1]) == {"status": True}
assert json.loads(sys.argv[2]) == {"status": True}
assert json.loads(sys.argv[3]).get("version") == "0.11.0"
assert json.loads(sys.argv[4]).get("version") == sys.argv[5]
PY

revision="$(docker exec "${db_container}" psql --username webui_pr7 --dbname webui_pr7 --tuples-only --no-align --set ON_ERROR_STOP=1 --command 'SELECT version_num FROM alembic_version ORDER BY version_num;')"
[[ "${revision}" == "a11c0d3f0bd0" ]]

fatal_pattern='Traceback|worker.*(died|exited)|segmentation fault|out of memory|Task exception was never retrieved| HTTP/[^ ]+" 5[0-9][0-9] '
if docker logs --since "2026-07-29T14:14:00+08:00" "${web_container}" 2>&1 | grep -Eiq "${fatal_pattern}"; then
    docker logs --since "2026-07-29T14:14:00+08:00" "${web_container}" >"${evidence_dir}/final-suspicious.log" 2>&1
    exit 1
fi

{
    printf 'test_image=%s\n' "${expected_web_image}"
    printf 'test_source=%s\n' "${expected_source}"
    printf 'test_health=healthy\n'
    printf 'test_restart_count=0\n'
    printf 'formal_image=%s\n' "${expected_formal_image}"
    printf 'formal_health=healthy\n'
    printf 'formal_restart_count=0\n'
    printf 'revision=%s\n' "${revision}"
    printf 'recent_fatal_or_5xx=0\n'
    printf 'audited_at=%s\n' "$(date --iso-8601=seconds)"
} >"${evidence_dir}/FINAL_AUDIT_OK"

sha256sum "${evidence_dir}/DEPLOY_OK" "${evidence_dir}/FINAL_AUDIT_OK" >"${evidence_dir}/manifest.sha256"
cat "${evidence_dir}/FINAL_AUDIT_OK"
cat "${evidence_dir}/manifest.sha256"
