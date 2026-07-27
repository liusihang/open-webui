#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

STACK_DIR=/home/aiserver/staging/openwebui-pr7-eea11194ed-test
WEB_CONTAINER=open-webui-pr7
DB_CONTAINER=openwebui-pr7-db
REDIS_CONTAINER=openwebui-pr7-redis
RUNTIME_CONTAINER=openwebui-pr7-agentscope-runtime
CANDIDATE_IMAGE=open-webui:pr7-chat-agent-dual-mode-1d8dba8a7-slim
EXPECTED_CANDIDATE_IMAGE_ID=sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b
EXPECTED_CANDIDATE_REVISION=1d8dba8a77e6e8adc5952891bac83a2a7c5a4804
EXPECTED_OLD_IMAGE_ID=sha256:fd6145b041f28269a0766e8f0f1ab91653a998745290041c43ef314c2456c8c4
SOURCE_REVISION=f8a9b0c1d2e3
TARGET_REVISION=c0d3b4a5e6f7
BACKUP_MANIFEST=${STACK_DIR}/backups/pr7-latest-test-stack-20260728/before-c0/manifest.env
OVERRIDE_FILE=${STACK_DIR}/compose.latest-candidate.yaml

COMPOSE_FILES=(
  "${STACK_DIR}/compose.yaml"
  "${STACK_DIR}/compose.webui-rebuild-eaff69b0d317.yaml"
  "${STACK_DIR}/compose.webui-eaff69-no-migrations.yaml"
  "${STACK_DIR}/compose.webui-4a4e43e206.yaml"
  "${STACK_DIR}/compose.agent-runtime-742f686182.yaml"
)

compose_args=()
for file in "${COMPOSE_FILES[@]}"; do
  [[ -f "${file}" ]] || { printf 'compose_file_missing=%s\n' "${file}"; exit 1; }
  compose_args+=( -f "${file}" )
done
[[ -f "${OVERRIDE_FILE}" ]] || { echo candidate_override_missing; exit 1; }

container_anchor() {
  docker inspect "$1" --format 'container_id={{.Id}} image_ref={{.Config.Image}} image_id={{.Image}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restarts={{.RestartCount}} started={{.State.StartedAt}}'
}

db_user=
db_name=
while IFS= read -r entry; do
  case "${entry}" in
    POSTGRES_USER=*) db_user=${entry#*=} ;;
    POSTGRES_DB=*) db_name=${entry#*=} ;;
  esac
done < <(docker inspect "${DB_CONTAINER}" --format '{{range .Config.Env}}{{println .}}{{end}}')
[[ -n "${db_user}" && -n "${db_name}" ]] || { echo database_identity_missing; exit 1; }

database_revision() {
  docker exec -i "${DB_CONTAINER}" psql -X -v ON_ERROR_STOP=1 -U "${db_user}" -d "${db_name}" -At <<'SQL'
SELECT version_num FROM alembic_version;
SQL
}

candidate_image_id=$(docker image inspect "${CANDIDATE_IMAGE}" --format '{{.Id}}')
candidate_revision=$(docker image inspect "${CANDIDATE_IMAGE}" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
[[ "${candidate_image_id}" == "${EXPECTED_CANDIDATE_IMAGE_ID}" ]] || { echo candidate_image_id_mismatch; exit 1; }
[[ "${candidate_revision}" == "${EXPECTED_CANDIDATE_REVISION}" ]] || { echo candidate_revision_mismatch; exit 1; }
[[ "$(docker inspect "${WEB_CONTAINER}" --format '{{.Image}}')" == "${EXPECTED_OLD_IMAGE_ID}" ]] || { echo baseline_image_mismatch; exit 1; }
[[ "$(database_revision)" == "${SOURCE_REVISION}" ]] || { echo baseline_revision_mismatch; exit 1; }

dump_file=$(awk -F= '$1 == "dump_file" {sub(/^[^=]*=/, ""); print; exit}' "${BACKUP_MANIFEST}")
expected_sha=$(awk -F= '$1 == "dump_sha256" {print $2; exit}' "${BACKUP_MANIFEST}")
source_revision=$(awk -F= '$1 == "source_revision" {print $2; exit}' "${BACKUP_MANIFEST}")
[[ "${source_revision}" == "${SOURCE_REVISION}" ]] || { echo backup_revision_mismatch; exit 1; }
[[ -f "${dump_file}" ]] || { echo backup_dump_missing; exit 1; }
[[ "$(sha256sum "${dump_file}" | awk '{print $1}')" == "${expected_sha}" ]] || { echo backup_checksum_mismatch; exit 1; }
docker exec -i "${DB_CONTAINER}" pg_restore --list < "${dump_file}" >/dev/null

docker compose "${compose_args[@]}" -f "${OVERRIDE_FILE}" config --quiet
resolved_image=$(docker compose "${compose_args[@]}" -f "${OVERRIDE_FILE}" config --format json | jq -r '.services["open-webui-pr7"].image')
resolved_workers=$(docker compose "${compose_args[@]}" -f "${OVERRIDE_FILE}" config --format json | jq -r '.services["open-webui-pr7"].environment.UVICORN_WORKERS')
resolved_migrations=$(docker compose "${compose_args[@]}" -f "${OVERRIDE_FILE}" config --format json | jq -r '.services["open-webui-pr7"].environment.ENABLE_DB_MIGRATIONS')
[[ "${resolved_image}" == "${CANDIDATE_IMAGE}" && "${resolved_workers}" == 4 && "${resolved_migrations}" == false ]] || { echo resolved_candidate_config_mismatch; exit 1; }

web_networks=$(docker inspect "${WEB_CONTAINER}" --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{println}}{{end}}')
db_networks=$(docker inspect "${DB_CONTAINER}" --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{println}}{{end}}')
shared_network=
while IFS= read -r network; do
  if [[ -n "${network}" ]] && grep -Fxq "${network}" <<< "${db_networks}"; then
    shared_network=${network}
    break
  fi
done <<< "${web_networks}"
[[ -n "${shared_network}" ]] || { echo shared_database_network_missing; exit 1; }

evidence_dir=${STACK_DIR}/evidence/pr7-latest-test-stack-20260728
mkdir -p "${evidence_dir}"
container_anchor "${WEB_CONTAINER}" > "${evidence_dir}/web.before.txt"
container_anchor "${DB_CONTAINER}" > "${evidence_dir}/db.before.txt"
container_anchor "${REDIS_CONTAINER}" > "${evidence_dir}/redis.before.txt"
container_anchor "${RUNTIME_CONTAINER}" > "${evidence_dir}/runtime.before.txt"
container_anchor open-webui > "${evidence_dir}/formal.before.txt"

env_file=$(mktemp "${STACK_DIR}/candidate-migration-env.XXXXXX")
trap 'rm -f "${env_file}"' EXIT
docker inspect "${WEB_CONTAINER}" --format '{{range .Config.Env}}{{println .}}{{end}}' | awk '!/^ENABLE_DB_MIGRATIONS=/' > "${env_file}"
printf 'ENABLE_DB_MIGRATIONS=false\n' >> "${env_file}"

docker stop --time 30 "${WEB_CONTAINER}" >/dev/null

docker run --rm \
  --network "${shared_network}" \
  --env-file "${env_file}" \
  --entrypoint alembic \
  --workdir /app/backend/open_webui \
  "${CANDIDATE_IMAGE}" upgrade "${TARGET_REVISION}"

[[ "$(database_revision)" == "${TARGET_REVISION}" ]] || { echo migration_target_not_reached; exit 1; }
schema_signature=$(docker exec -i "${DB_CONTAINER}" psql -X -v ON_ERROR_STOP=1 -U "${db_user}" -d "${db_name}" -At <<'SQL'
SELECT
  (SELECT count(*) FROM conversation_mode_profile_revision)::text || ':' ||
  (SELECT count(*) FROM conversation_mode_profile_head)::text || ':' ||
  (to_regclass('public.conversation_mode_profile_temporary_binding') IS NOT NULL)::int::text || ':' ||
  (SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='chat' AND column_name='mode_profile_revision_id')::text;
SQL
)
[[ "${schema_signature}" == "2:2:1:1" ]] || { printf 'schema_signature=%s expected=2:2:1:1\n' "${schema_signature}"; exit 1; }

docker compose "${compose_args[@]}" -f "${OVERRIDE_FILE}" up -d --no-deps --force-recreate open-webui-pr7

for attempt in $(seq 1 120); do
  status=$(docker inspect "${WEB_CONTAINER}" --format '{{.State.Status}}')
  health=$(docker inspect "${WEB_CONTAINER}" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}')
  if [[ "${status}" == running && "${health}" == healthy ]]; then
    break
  fi
  if [[ "${status}" == exited || "${status}" == dead || ${attempt} -eq 120 ]]; then
    printf 'candidate_not_healthy status=%s health=%s attempt=%s\n' "${status}" "${health}" "${attempt}"
    exit 1
  fi
  sleep 2
done

[[ "$(docker inspect "${WEB_CONTAINER}" --format '{{.Image}}')" == "${EXPECTED_CANDIDATE_IMAGE_ID}" ]] || { echo running_image_mismatch; exit 1; }
safe_env=$(docker inspect "${WEB_CONTAINER}" --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^(ENABLE_DB_MIGRATIONS|UVICORN_WORKERS)=')
grep -Fxq 'ENABLE_DB_MIGRATIONS=false' <<< "${safe_env}" || { echo migration_env_mismatch; exit 1; }
grep -Fxq 'UVICORN_WORKERS=4' <<< "${safe_env}" || { echo worker_env_mismatch; exit 1; }

process_table=$(docker top "${WEB_CONTAINER}" -eo pid,ppid,args)
master_pid=$(awk '$0 ~ /-m uvicorn/ && $0 ~ /--workers 4/ {print $1; exit}' <<< "${process_table}")
[[ -n "${master_pid}" ]] || { echo uvicorn_master_missing; exit 1; }
worker_pids=$(awk -v master_pid="${master_pid}" '$2 == master_pid && $0 !~ /resource_tracker/ {print $1}' <<< "${process_table}")
worker_count=$(awk 'NF {count += 1} END {print count + 0}' <<< "${worker_pids}")
[[ "${worker_count}" == 4 ]] || { printf 'worker_count=%s expected=4\n' "${worker_count}"; exit 1; }

curl -fsS http://127.0.0.1:18085/health >/dev/null
curl -fsS http://127.0.0.1:18085/health/db >/dev/null

container_anchor "${WEB_CONTAINER}" > "${evidence_dir}/web.after.txt"
container_anchor "${DB_CONTAINER}" > "${evidence_dir}/db.after.txt"
container_anchor "${REDIS_CONTAINER}" > "${evidence_dir}/redis.after.txt"
container_anchor "${RUNTIME_CONTAINER}" > "${evidence_dir}/runtime.after.txt"
container_anchor open-webui > "${evidence_dir}/formal.after.txt"
cmp -s "${evidence_dir}/db.before.txt" "${evidence_dir}/db.after.txt" || { echo isolated_db_container_changed; exit 1; }
cmp -s "${evidence_dir}/redis.before.txt" "${evidence_dir}/redis.after.txt" || { echo isolated_redis_container_changed; exit 1; }
cmp -s "${evidence_dir}/runtime.before.txt" "${evidence_dir}/runtime.after.txt" || { echo isolated_runtime_container_changed; exit 1; }
cmp -s "${evidence_dir}/formal.before.txt" "${evidence_dir}/formal.after.txt" || { echo formal_container_anchor_changed; exit 1; }

printf 'candidate_image_id=%s\n' "${candidate_image_id}"
printf 'database_revision=%s\n' "$(database_revision)"
printf 'schema_signature=%s\n' "${schema_signature}"
printf 'workers=%s\n' "${worker_count}"
printf 'worker_pids=%s\n' "${worker_pids//$'\n'/ }"
printf 'evidence_dir=%s\n' "${evidence_dir}"
