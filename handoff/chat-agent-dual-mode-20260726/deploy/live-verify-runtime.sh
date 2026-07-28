#!/usr/bin/env bash
set -Eeuo pipefail

STACK_DIR=${STACK_DIR:-/srv/openwebui-migration}
COMPOSE_FILE=${COMPOSE_FILE:-${STACK_DIR}/compose.yaml}
WEB_CONTAINER=${WEB_CONTAINER:-open-webui}
DB_CONTAINER=${DB_CONTAINER:-openwebui-db}
RUNTIME_CONTAINER=${RUNTIME_CONTAINER:-openwebui-agentscope-runtime}
EXPECTED_IMAGE_ID=${EXPECTED_IMAGE_ID:?EXPECTED_IMAGE_ID is required}
EXPECTED_REVISION=${EXPECTED_REVISION:?EXPECTED_REVISION is required}
EXPECTED_WORKERS=${EXPECTED_WORKERS:-4}
EXPECT_RUNTIME=${EXPECT_RUNTIME:-false}
EXPECTED_RUNTIME_IMAGE_ID=${EXPECTED_RUNTIME_IMAGE_ID:-sha256:f7396ba23e49f934216ba8fc4b38c695b7f639722d852b44234769c66ca7f6e9}

anchor=$(docker inspect "${WEB_CONTAINER}" --format 'container_id={{.Id}} image_ref={{.Config.Image}} image_id={{.Image}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restarts={{.RestartCount}} started={{.State.StartedAt}}')
printf '%s\n' "${anchor}"
if [[ "$(docker inspect "${WEB_CONTAINER}" --format '{{.Image}}')" != "${EXPECTED_IMAGE_ID}" ]]; then
  echo image_verification_failed
  exit 1
fi
if [[ "$(docker inspect "${WEB_CONTAINER}" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}')" != healthy ]]; then
  echo health_verification_failed
  exit 1
fi

db_user=
db_name=
while IFS= read -r entry; do
  case "${entry}" in
    POSTGRES_USER=*) db_user=${entry#*=} ;;
    POSTGRES_DB=*) db_name=${entry#*=} ;;
  esac
done < <(docker inspect "${DB_CONTAINER}" --format '{{range .Config.Env}}{{println .}}{{end}}')
revision=$(docker exec -i "${DB_CONTAINER}" psql -X -v ON_ERROR_STOP=1 -U "${db_user}" -d "${db_name}" -At <<'SQL'
SELECT version_num FROM alembic_version;
SQL
)
if [[ "${revision}" != "${EXPECTED_REVISION}" ]]; then
  printf 'revision=%s expected=%s\n' "${revision}" "${EXPECTED_REVISION}"
  exit 1
fi

process_table=$(docker top "${WEB_CONTAINER}" -eo pid,ppid,args)
master_pid=$(awk -v expected_workers="${EXPECTED_WORKERS}" '$0 ~ /-m uvicorn/ && $0 ~ ("--workers " expected_workers) {print $1; exit}' <<< "${process_table}")
if [[ -z "${master_pid}" ]]; then
  echo uvicorn_master_missing
  exit 1
fi
worker_pids=$(awk -v master_pid="${master_pid}" '$2 == master_pid && $0 !~ /resource_tracker/ {print $1}' <<< "${process_table}")
worker_count=$(awk 'NF {count += 1} END {print count + 0}' <<< "${worker_pids}")
if [[ "${worker_count}" != "${EXPECTED_WORKERS}" ]]; then
  printf 'workers=%s expected=%s\n' "${worker_count}" "${EXPECTED_WORKERS}"
  exit 1
fi

docker exec "${WEB_CONTAINER}" curl -fsS http://127.0.0.1:8080/health >/dev/null
if [[ "${EXPECT_RUNTIME}" == true ]]; then
  if [[ "$(docker inspect "${RUNTIME_CONTAINER}" --format '{{.Image}}')" != "${EXPECTED_RUNTIME_IMAGE_ID}" ]]; then
    echo runtime_image_verification_failed
    exit 1
  fi
  if [[ "$(docker inspect "${RUNTIME_CONTAINER}" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}')" != healthy ]]; then
    echo runtime_health_verification_failed
    exit 1
  fi
  if [[ "$(docker inspect "${RUNTIME_CONTAINER}" --format '{{.RestartCount}}')" != 0 ]]; then
    echo runtime_restart_verification_failed
    exit 1
  fi
  docker exec "${WEB_CONTAINER}" python -c "import json,urllib.request; assert json.load(urllib.request.urlopen('http://agentscope-runtime:8000/health', timeout=10))['status'] == 'ok'"
  docker inspect "${RUNTIME_CONTAINER}" --format 'runtime={{.Id}} {{.Image}} {{.State.Status}} {{.State.Health.Status}} {{.RestartCount}} {{.State.StartedAt}}'
fi
printf 'compose_sha256=%s\n' "$(sha256sum "${COMPOSE_FILE}" | awk '{print $1}')"
printf 'revision=%s\n' "${revision}"
printf 'workers=%s\n' "${worker_count}"
printf 'worker_pids=%s\n' "${worker_pids//$'\n'/ }"
