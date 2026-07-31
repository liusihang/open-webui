#!/usr/bin/env bash
set -euo pipefail

web_container='open-webui-pr7'
formal_container='open-webui'
expected_image_id='sha256:5a541612b86655ac1423b5e88109c47ff818819d99315cf7e51fa9a764e9ac05'
expected_formal_image_id='sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b'
new_default='bifrostapi.Cliproxy/gpt-5.5'
helper_source='/tmp/update-test-default-model.py'
deployment_root='/home/aiserver/staging/openwebui-pr7-eea11194ed-test'
backup_dir="${deployment_root}/backups/pre-default-model-e3a9c97dd059-retry3"

capture_container() {
    local container="$1"
    docker inspect --format '{{.Id}}|{{.Image}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.RestartCount}}' "${container}"
}

[[ "$(docker inspect --format '{{.Image}}' "${web_container}")" == "${expected_image_id}" ]]
[[ "$(docker inspect --format '{{.Image}}' "${formal_container}")" == "${expected_formal_image_id}" ]]
[[ "$(docker inspect --format '{{.State.Health.Status}}' "${web_container}")" == 'healthy' ]]
[[ "$(docker inspect --format '{{.RestartCount}}' "${web_container}")" == '0' ]]
test -s "${helper_source}"
test ! -e "${backup_dir}"
install -d -m 700 "${backup_dir}"

capture_container "${web_container}" >"${backup_dir}/test.before.txt"
capture_container "${formal_container}" >"${backup_dir}/formal.before.txt"
install -m 600 "${helper_source}" "${backup_dir}/update-test-default-model.py"
docker cp "${helper_source}" "${web_container}:/tmp/update-test-default-model.py"
docker exec -e PYTHONPATH=/app/backend "${web_container}" python /tmp/update-test-default-model.py \
    --expected "${new_default}" \
    --new "${new_default}" \
    >"${backup_dir}/update-result.json"

python3 -c 'import json,sys; data=json.load(open(sys.argv[1])); assert data["authenticated_public_sample_count"] == 20; assert data["authenticated_public_sample_values"] == [sys.argv[2]]' \
    "${backup_dir}/update-result.json" "${new_default}"

rollback_script="${backup_dir}/rollback-test-default-model.sh"
cat >"${rollback_script}" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
web_container='open-webui-pr7'
backup_dir='/home/aiserver/staging/openwebui-pr7-eea11194ed-test/backups/pre-default-model-e3a9c97dd059-retry3'
docker cp "${backup_dir}/update-test-default-model.py" "${web_container}:/tmp/update-test-default-model.py"
docker exec -e PYTHONPATH=/app/backend "${web_container}" python /tmp/update-test-default-model.py \
    --expected bifrostapi.Cliproxy/gpt-5.5 \
    --new __empty__
SH
chmod 700 "${rollback_script}"

capture_container "${web_container}" >"${backup_dir}/test.after.txt"
capture_container "${formal_container}" >"${backup_dir}/formal.after.txt"
cmp "${backup_dir}/test.before.txt" "${backup_dir}/test.after.txt"
cmp "${backup_dir}/formal.before.txt" "${backup_dir}/formal.after.txt"

{
    printf 'default_model=%s\n' "${new_default}"
    printf 'test_container=%s\n' "$(capture_container "${web_container}")"
    printf 'formal_container=%s\n' "$(capture_container "${formal_container}")"
    printf 'sample_count=20\n'
    printf 'rollback_script=%s\n' "${rollback_script}"
    printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
} >"${backup_dir}/DEFAULT_MODEL_OK"

cat "${backup_dir}/DEFAULT_MODEL_OK"
cat "${backup_dir}/update-result.json"
