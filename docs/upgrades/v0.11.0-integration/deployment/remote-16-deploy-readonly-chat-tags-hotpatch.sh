#!/usr/bin/env bash
set -euo pipefail

target_container='open-webui-pr7'
formal_container='open-webui'
db_container='openwebui-pr7-db'
db_user='webui_pr7'
database='webui_pr7'
expected_current_image_id='sha256:c7970f571cdd6b6556403bee0b403f822e73ce8160d8c64b3953a8f97246598a'
expected_formal_image_id='sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b'
base_image='open-webui:v011-hotfix-c764e6ec2c01'
hotpatch_image='open-webui:v011-hotfix-0bb586c8699d'
source_commit='0bb586c8699d34c211a5a3686ab61bfe10f2ac90'
target_revision='a11c0d3f0bd0'
artifact='/tmp/openwebui-v011-hotfix-0bb586c8699d-context.tar.gz'
expected_artifact_sha256='b9db770dd69cd63e80f46fffb0b795b5cd8047ffaf3c66c1b2a1dfa1df1c7e79'
deployment_root='/home/aiserver/staging/openwebui-pr7-eea11194ed-test'
context_dir="${deployment_root}/hotpatch-v011-0bb586c8699d"
overlay="${deployment_root}/compose.webui-v011-hotfix-0bb586c8699d.yaml"
backup_dir="${deployment_root}/backups/pre-hotpatch-0bb586c8699d"
evidence_dir="${deployment_root}/evidence/v011-persisted-chat-0bb586c8699d-20260729-132800"

capture_container() {
    local container="$1"
    docker inspect --format '{{.Id}}|{{.Image}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.RestartCount}}|{{.State.OOMKilled}}' "${container}"
}

current_image_id="$(docker inspect --format '{{.Image}}' "${target_container}")"
formal_image_id="$(docker inspect --format '{{.Image}}' "${formal_container}")"
[[ "${current_image_id}" == "${expected_current_image_id}" ]]
[[ "${formal_image_id}" == "${expected_formal_image_id}" ]]
[[ "$(docker inspect --format '{{.State.Health.Status}}' "${target_container}")" == 'healthy' ]]
[[ "$(docker inspect --format '{{.RestartCount}}' "${target_container}")" == '0' ]]
if docker image inspect "${hotpatch_image}" >/dev/null 2>&1; then
    printf 'refusing to overwrite existing image tag: %s\n' "${hotpatch_image}" >&2
    exit 1
fi

actual_artifact_sha256="$(sha256sum "${artifact}" | awk '{print $1}')"
[[ "${actual_artifact_sha256}" == "${expected_artifact_sha256}" ]]
for path in "${context_dir}" "${overlay}" "${backup_dir}" "${evidence_dir}"; do
    if [[ -e "${path}" ]]; then
        printf 'refusing to overwrite existing deployment artifact: %s\n' "${path}" >&2
        exit 1
    fi
done

install -d -m 700 "${context_dir}" "${backup_dir}" "${evidence_dir}"
date --iso-8601=seconds >"${evidence_dir}/started-at.txt"
tar -xzf "${artifact}" -C "${context_dir}"

docker build \
    --network none \
    --build-arg "BASE_IMAGE=${base_image}" \
    --build-arg "HOTPATCH_SOURCE_COMMIT=${source_commit}" \
    --file "${context_dir}/Dockerfile.hotpatch-0bb586c8699d" \
    --tag "${hotpatch_image}" \
    "${context_dir}"

image_id="$(docker image inspect --format '{{.Id}}' "${hotpatch_image}")"
image_source="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "${hotpatch_image}")"
image_base="$(docker image inspect --format '{{index .Config.Labels "io.openwebui.hotpatch.base-image"}}' "${hotpatch_image}")"
image_scope="$(docker image inspect --format '{{index .Config.Labels "io.openwebui.hotpatch.scope"}}' "${hotpatch_image}")"
[[ "${image_source}" == "${source_commit}" ]]
[[ "${image_base}" == "${base_image}" ]]
[[ "${image_scope}" == 'v0.11-read-only-chat-tags' ]]

project="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "${target_container}")"
project_workdir="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "${target_container}")"
compose_csv="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' "${target_container}")"
IFS=',' read -r -a compose_files <<<"${compose_csv}"

capture_container "${target_container}" >"${backup_dir}/test.before.txt"
capture_container "${formal_container}" >"${backup_dir}/formal.before.txt"
docker ps -a \
    --filter "label=com.docker.compose.project=${project}" \
    --format '{{.Names}}' \
    | sort \
    | while read -r container; do
        if [[ "${container}" != "${target_container}" ]]; then
            printf '%s\t%s\n' "${container}" "$(capture_container "${container}")"
        fi
      done >"${backup_dir}/non-webui.before.tsv"

for compose_file in "${compose_files[@]}"; do
    cp -a "${compose_file}" "${backup_dir}/"
done
{
    printf 'target_container=%s\n' "${target_container}"
    printf 'rollback_image_id=%s\n' "${expected_current_image_id}"
    printf 'formal_image_id=%s\n' "${expected_formal_image_id}"
    printf 'hotpatch_image_ref=%s\n' "${hotpatch_image}"
    printf 'source_commit=%s\n' "${source_commit}"
    printf 'artifact_sha256=%s\n' "${actual_artifact_sha256}"
    sha256sum "${compose_files[@]}"
} >"${backup_dir}/manifest.txt"

cat >"${overlay}" <<'YAML'
services:
  open-webui-pr7:
    image: open-webui:v011-hotfix-0bb586c8699d
    environment:
      REDIS_HEALTH_CHECK_INTERVAL: "30"
      REDIS_SOCKET_KEEPALIVE: "true"
YAML

compose_args=(--project-name "${project}" --project-directory "${project_workdir}")
for compose_file in "${compose_files[@]}"; do
    compose_args+=(-f "${compose_file}")
done
compose_args+=(-f "${overlay}")

resolved_config="$(mktemp)"
cleanup() {
    rm -f -- "${resolved_config}"
}
trap cleanup EXIT
docker compose "${compose_args[@]}" config --format json >"${resolved_config}"
python3 - "${resolved_config}" "${hotpatch_image}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding='utf-8') as handle:
    config = json.load(handle)
service = config['services']['open-webui-pr7']
environment = service.get('environment', {})
assert service.get('image') == sys.argv[2]
assert str(environment.get('REDIS_HEALTH_CHECK_INTERVAL')) == '30'
assert str(environment.get('REDIS_SOCKET_KEEPALIVE')).lower() == 'true'
print(f'resolved_image={service["image"]}')
print('redis_keepalive=true')
PY

rollback_script="${backup_dir}/rollback-open-webui-pr7.sh"
{
    printf '#!/usr/bin/env bash\nset -euo pipefail\n'
    printf 'docker compose --project-name %q --project-directory %q' "${project}" "${project_workdir}"
    for compose_file in "${compose_files[@]}"; do
        printf ' -f %q' "${compose_file}"
    done
    printf ' up -d --no-deps --force-recreate open-webui-pr7\n'
} >"${rollback_script}"
chmod 700 "${rollback_script}"

docker compose "${compose_args[@]}" up -d --no-deps --force-recreate open-webui-pr7

deadline=$((SECONDS + 300))
while ((SECONDS < deadline)); do
    runtime_status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${target_container}" 2>/dev/null || true)"
    if [[ "${runtime_status}" == 'healthy' ]]; then
        break
    fi
    if [[ "${runtime_status}" == 'unhealthy' || "${runtime_status}" == 'exited' || "${runtime_status}" == 'dead' ]]; then
        docker logs --tail 200 "${target_container}" >"${backup_dir}/failed-container.log" 2>&1 || true
        "${rollback_script}"
        exit 1
    fi
    sleep 5
done

if [[ "$(docker inspect --format '{{.State.Health.Status}}' "${target_container}")" != 'healthy' ]]; then
    docker logs --tail 200 "${target_container}" >"${backup_dir}/failed-container.log" 2>&1 || true
    "${rollback_script}"
    exit 1
fi
[[ "$(docker inspect --format '{{.Image}}' "${target_container}")" == "${image_id}" ]]
[[ "$(docker inspect --format '{{.RestartCount}}' "${target_container}")" == '0' ]]

port="$(docker inspect --format '{{(index (index .NetworkSettings.Ports "8080/tcp") 0).HostPort}}' "${target_container}")"
base_url="http://127.0.0.1:${port}"
curl --fail --silent --show-error "${base_url}/health" >"${evidence_dir}/health.json"
curl --fail --silent --show-error "${base_url}/health/db" >"${evidence_dir}/health-db.json"
curl --fail --silent --show-error "${base_url}/api/version" >"${evidence_dir}/version.json"
python3 - "${evidence_dir}/health.json" "${evidence_dir}/health-db.json" "${evidence_dir}/version.json" <<'PY'
from pathlib import Path
import json
import sys

assert json.loads(Path(sys.argv[1]).read_text()) == {'status': True}
assert json.loads(Path(sys.argv[2]).read_text()) == {'status': True}
assert json.loads(Path(sys.argv[3]).read_text()).get('version') == '0.11.0'
PY

revision="$(docker exec "${db_container}" psql --username "${db_user}" --dbname "${database}" --tuples-only --no-align --set ON_ERROR_STOP=1 --command 'SELECT version_num FROM alembic_version ORDER BY version_num;')"
[[ "${revision}" == "${target_revision}" ]]

docker exec "${target_container}" test -s /app/build/index.html
docker exec "${target_container}" grep -F -q "${source_commit}" /app/build/_app/version.json
docker exec "${target_container}" grep -R -F -q 'getConversationModeDraftCapabilitySnapshotForMode' /app/build/_app/immutable
docker exec "${target_container}" grep -R -F -q '{ existingChat: true }' /app/build/_app/immutable
docker exec "${target_container}" grep -R -F -q 'loadedChat.user_id === $user?.id' /app/build/_app/immutable

docker ps -a \
    --filter "label=com.docker.compose.project=${project}" \
    --format '{{.Names}}' \
    | sort \
    | while read -r container; do
        if [[ "${container}" != "${target_container}" ]]; then
            printf '%s\t%s\n' "${container}" "$(capture_container "${container}")"
        fi
      done >"${backup_dir}/non-webui.after.tsv"
capture_container "${formal_container}" >"${backup_dir}/formal.after.txt"
cmp "${backup_dir}/non-webui.before.tsv" "${backup_dir}/non-webui.after.tsv"
cmp "${backup_dir}/formal.before.txt" "${backup_dir}/formal.after.txt"

{
    printf 'image=%s\n' "${hotpatch_image}"
    printf 'image_id=%s\n' "${image_id}"
    printf 'source=%s\n' "${image_source}"
    printf 'container=%s\n' "$(capture_container "${target_container}")"
    printf 'formal=%s\n' "$(capture_container "${formal_container}")"
    printf 'revision=%s\n' "${revision}"
    printf 'rollback_script=%s\n' "${rollback_script}"
    printf 'evidence_dir=%s\n' "${evidence_dir}"
    printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
} >"${backup_dir}/DEPLOY_OK"

cp "${backup_dir}/DEPLOY_OK" "${evidence_dir}/DEPLOY_OK"
cat "${backup_dir}/DEPLOY_OK"
