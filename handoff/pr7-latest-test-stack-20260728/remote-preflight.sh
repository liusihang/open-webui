#!/usr/bin/env bash
set -Eeuo pipefail

ISOLATED_WEB=open-webui-pr7
ISOLATED_DB=openwebui-pr7-db
ISOLATED_REDIS=openwebui-pr7-redis
ISOLATED_RUNTIME=openwebui-pr7-agentscope-runtime
FORMAL_WEB=open-webui
FORMAL_DB=openwebui-db
CANDIDATE_IMAGE=open-webui:pr7-chat-agent-dual-mode-1d8dba8a7-slim

container_anchor() {
  docker inspect "$1" --format 'container_id={{.Id}} image_ref={{.Config.Image}} image_id={{.Image}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restarts={{.RestartCount}} started={{.State.StartedAt}}'
}

database_revision() {
  local container=$1
  local db_user=
  local db_name=
  while IFS= read -r entry; do
    case "${entry}" in
      POSTGRES_USER=*) db_user=${entry#*=} ;;
      POSTGRES_DB=*) db_name=${entry#*=} ;;
    esac
  done < <(docker inspect "${container}" --format '{{range .Config.Env}}{{println .}}{{end}}')
  docker exec -i "${container}" psql -X -v ON_ERROR_STOP=1 -U "${db_user}" -d "${db_name}" -At <<'SQL'
SELECT version_num FROM alembic_version;
SQL
}

echo TIMESTAMP
date --iso-8601=seconds

echo ISOLATED_WEB
container_anchor "${ISOLATED_WEB}"
docker inspect "${ISOLATED_WEB}" --format 'compose_files={{index .Config.Labels "com.docker.compose.project.config_files"}}'
docker inspect "${ISOLATED_WEB}" --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^(UVICORN_WORKERS|ENABLE_DB_MIGRATIONS|ENABLE_AGENT_MODE|AGENT_RUNTIME_BASE_URL)=' | sort
docker top "${ISOLATED_WEB}" -eo pid,ppid,args
curl -fsS http://127.0.0.1:18085/health
printf '\n'
curl -fsS http://127.0.0.1:18085/health/db
printf '\n'

echo ISOLATED_SUPPORT
container_anchor "${ISOLATED_DB}"
container_anchor "${ISOLATED_REDIS}"
container_anchor "${ISOLATED_RUNTIME}"
printf 'isolated_revision=%s\n' "$(database_revision "${ISOLATED_DB}")"

echo CANDIDATE_IMAGE
docker image inspect "${CANDIDATE_IMAGE}" --format 'image_id={{.Id}} created={{.Created}} revision={{index .Config.Labels "org.opencontainers.image.revision"}}'

echo FORMAL_READ_ONLY
container_anchor "${FORMAL_WEB}"
printf 'formal_revision=%s\n' "$(database_revision "${FORMAL_DB}")"
docker top "${FORMAL_WEB}" -eo pid,ppid,args

echo CAPACITY
df -h /home/aiserver/staging/openwebui-pr7-eea11194ed-test
