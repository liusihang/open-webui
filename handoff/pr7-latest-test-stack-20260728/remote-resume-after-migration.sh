#!/usr/bin/env bash
set -Eeuo pipefail

STACK_DIR=/home/aiserver/staging/openwebui-pr7-eea11194ed-test
WEB_CONTAINER=open-webui-pr7
DB_CONTAINER=openwebui-pr7-db
REDIS_CONTAINER=openwebui-pr7-redis
RUNTIME_CONTAINER=openwebui-pr7-agentscope-runtime
CANDIDATE_IMAGE=open-webui:pr7-chat-agent-dual-mode-1d8dba8a7-slim
EXPECTED_CANDIDATE_IMAGE_ID=sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b
EXPECTED_CANDIDATE_REVISION=1d8dba8a77e6e8adc5952891bac83a2a7c5a4804
EXPECTED_OLD_IMAGE_ID=sha256:fd6145b041f28269a0766e8f0f1ab91653a998745290041c43ef314c2456c8c4
TARGET_REVISION=c0d3b4a5e6f7
OVERRIDE_FILE=${STACK_DIR}/compose.latest-candidate.yaml
EVIDENCE_DIR=${STACK_DIR}/evidence/pr7-latest-test-stack-20260728

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

database_revision=$(docker exec -i "${DB_CONTAINER}" psql -X -v ON_ERROR_STOP=1 -U "${db_user}" -d "${db_name}" -At <<'SQL'
SELECT version_num FROM alembic_version;
SQL
)
schema_signature=$(docker exec -i "${DB_CONTAINER}" psql -X -v ON_ERROR_STOP=1 -U "${db_user}" -d "${db_name}" -At <<'SQL'
SELECT
  (SELECT count(*) FROM conversation_mode_profile_revision)::text || ':' ||
  (SELECT count(*) FROM conversation_mode_profile_head)::text || ':' ||
  (to_regclass('public.conversation_mode_profile_temporary_binding') IS NOT NULL)::int::text || ':' ||
  (SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='chat' AND column_name='mode_profile_revision_id')::text;
SQL
)

[[ "${database_revision}" == "${TARGET_REVISION}" ]] || { printf 'resume_revision=%s expected=%s\n' "${database_revision}" "${TARGET_REVISION}"; exit 1; }
[[ "${schema_signature}" == "2:2:1:1" ]] || { printf 'resume_schema_signature=%s expected=2:2:1:1\n' "${schema_signature}"; exit 1; }
[[ "$(docker inspect "${WEB_CONTAINER}" --format '{{.State.Status}}')" == exited ]] || { echo resume_requires_stopped_old_webui; exit 1; }
[[ "$(docker inspect "${WEB_CONTAINER}" --format '{{.Image}}')" == "${EXPECTED_OLD_IMAGE_ID}" ]] || { echo resume_old_image_mismatch; exit 1; }
[[ "$(docker image inspect "${CANDIDATE_IMAGE}" --format '{{.Id}}')" == "${EXPECTED_CANDIDATE_IMAGE_ID}" ]] || { echo resume_candidate_image_mismatch; exit 1; }
[[ "$(docker image inspect "${CANDIDATE_IMAGE}" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')" == "${EXPECTED_CANDIDATE_REVISION}" ]] || { echo resume_candidate_revision_mismatch; exit 1; }

docker compose "${compose_args[@]}" -f "${OVERRIDE_FILE}" config --quiet
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

container_anchor "${WEB_CONTAINER}" > "${EVIDENCE_DIR}/web.after.txt"
container_anchor "${DB_CONTAINER}" > "${EVIDENCE_DIR}/db.after.txt"
container_anchor "${REDIS_CONTAINER}" > "${EVIDENCE_DIR}/redis.after.txt"
container_anchor "${RUNTIME_CONTAINER}" > "${EVIDENCE_DIR}/runtime.after.txt"
container_anchor open-webui > "${EVIDENCE_DIR}/formal.after.txt"
cmp -s "${EVIDENCE_DIR}/db.before.txt" "${EVIDENCE_DIR}/db.after.txt" || { echo isolated_db_container_changed; exit 1; }
cmp -s "${EVIDENCE_DIR}/redis.before.txt" "${EVIDENCE_DIR}/redis.after.txt" || { echo isolated_redis_container_changed; exit 1; }
cmp -s "${EVIDENCE_DIR}/runtime.before.txt" "${EVIDENCE_DIR}/runtime.after.txt" || { echo isolated_runtime_container_changed; exit 1; }
cmp -s "${EVIDENCE_DIR}/formal.before.txt" "${EVIDENCE_DIR}/formal.after.txt" || { echo formal_container_anchor_changed; exit 1; }

printf 'candidate_image_id=%s\n' "${EXPECTED_CANDIDATE_IMAGE_ID}"
printf 'database_revision=%s\n' "${database_revision}"
printf 'schema_signature=%s\n' "${schema_signature}"
printf 'workers=%s\n' "${worker_count}"
printf 'worker_pids=%s\n' "${worker_pids//$'\n'/ }"
printf 'evidence_dir=%s\n' "${EVIDENCE_DIR}"
