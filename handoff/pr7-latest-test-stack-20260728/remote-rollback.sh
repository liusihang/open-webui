#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

if [[ "${CONFIRM_ISOLATED_ROLLBACK:-}" != "rollback-pr7-latest-test-stack-to-f8" ]]; then
  echo isolated_rollback_confirmation_missing
  exit 1
fi

STACK_DIR=/home/aiserver/staging/openwebui-pr7-eea11194ed-test
WEB_CONTAINER=open-webui-pr7
DB_CONTAINER=openwebui-pr7-db
CANDIDATE_IMAGE=open-webui:pr7-chat-agent-dual-mode-1d8dba8a7-slim
SOURCE_REVISION=f8a9b0c1d2e3
TARGET_REVISION=c0d3b4a5e6f7

COMPOSE_FILES=(
  "${STACK_DIR}/compose.yaml"
  "${STACK_DIR}/compose.webui-rebuild-eaff69b0d317.yaml"
  "${STACK_DIR}/compose.webui-eaff69-no-migrations.yaml"
  "${STACK_DIR}/compose.webui-4a4e43e206.yaml"
  "${STACK_DIR}/compose.agent-runtime-742f686182.yaml"
)
compose_args=()
for file in "${COMPOSE_FILES[@]}"; do compose_args+=( -f "${file}" ); done

db_user=
db_name=
while IFS= read -r entry; do
  case "${entry}" in
    POSTGRES_USER=*) db_user=${entry#*=} ;;
    POSTGRES_DB=*) db_name=${entry#*=} ;;
  esac
done < <(docker inspect "${DB_CONTAINER}" --format '{{range .Config.Env}}{{println .}}{{end}}')

database_revision() {
  docker exec -i "${DB_CONTAINER}" psql -X -v ON_ERROR_STOP=1 -U "${db_user}" -d "${db_name}" -At <<'SQL'
SELECT version_num FROM alembic_version;
SQL
}

web_network=$(docker inspect "${WEB_CONTAINER}" --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{println}}{{end}}' | head -n 1)
env_file=$(mktemp "${STACK_DIR}/rollback-env.XXXXXX")
trap 'rm -f "${env_file}"' EXIT
docker inspect "${WEB_CONTAINER}" --format '{{range .Config.Env}}{{println .}}{{end}}' | awk '!/^ENABLE_DB_MIGRATIONS=/' > "${env_file}"
printf 'ENABLE_DB_MIGRATIONS=false\n' >> "${env_file}"

docker stop --time 30 "${WEB_CONTAINER}" >/dev/null || true
if [[ "$(database_revision)" == "${TARGET_REVISION}" ]]; then
  docker run --rm \
    --network "${web_network}" \
    --env-file "${env_file}" \
    --entrypoint alembic \
    --workdir /app/backend/open_webui \
    "${CANDIDATE_IMAGE}" downgrade "${SOURCE_REVISION}"
fi
[[ "$(database_revision)" == "${SOURCE_REVISION}" ]] || { echo rollback_revision_failed; exit 1; }

docker compose "${compose_args[@]}" up -d --no-deps --force-recreate open-webui-pr7
for attempt in $(seq 1 120); do
  health=$(docker inspect "${WEB_CONTAINER}" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}')
  [[ "${health}" == healthy ]] && break
  [[ ${attempt} -eq 120 ]] && { echo rollback_health_failed; exit 1; }
  sleep 2
done

printf 'rollback_image_id=%s\n' "$(docker inspect "${WEB_CONTAINER}" --format '{{.Image}}')"
printf 'rollback_revision=%s\n' "$(database_revision)"
docker top "${WEB_CONTAINER}" -eo pid,ppid,args
