#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

STACK_DIR=${STACK_DIR:-/srv/openwebui-migration}
COMPOSE_FILE=${COMPOSE_FILE:-${STACK_DIR}/compose.yaml}
OVERRIDE_FILE=${OVERRIDE_FILE:-/home/aiserver/staging/pr7-live-prep-20260727/release/compose.live-pr7-dual-mode-1d8dba8a7.yaml}
PREP_ROOT=${PREP_ROOT:-/home/aiserver/staging/pr7-live-prep-20260727}
WEB_CONTAINER=${WEB_CONTAINER:-open-webui}
DB_CONTAINER=${DB_CONTAINER:-openwebui-db}
EXPECTED_COMPOSE_SHA256=${EXPECTED_COMPOSE_SHA256:-7fff73a9037687460bd6c27669e9224203241546928173106c9999d6b3425da1}
EXPECTED_OLD_IMAGE_ID=${EXPECTED_OLD_IMAGE_ID:-sha256:7ec820b71fa94205b273cb8cd00344a130e1921ae8e643ba6192b0e58933bd45}
EXPECTED_CANDIDATE_IMAGE_ID=${EXPECTED_CANDIDATE_IMAGE_ID:-sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b}
TARGET_REVISION=${TARGET_REVISION:-c0d3b4a5e6f7}

if [[ "${CONFIRM_LIVE_WEBUI_RECREATE:-}" != "deploy-pr7-dual-mode-to-aiserver-live" ]]; then
  echo live_deploy_confirmation_missing
  exit 1
fi

compose_sha256=$(sha256sum "${COMPOSE_FILE}" | awk '{print $1}')
if [[ "${compose_sha256}" != "${EXPECTED_COMPOSE_SHA256}" ]]; then
  echo compose_anchor_mismatch
  exit 1
fi
if [[ "$(docker inspect "${WEB_CONTAINER}" --format '{{.Image}}')" != "${EXPECTED_OLD_IMAGE_ID}" ]]; then
  echo old_image_anchor_mismatch
  exit 1
fi
if [[ "$(docker image inspect open-webui:pr7-chat-agent-dual-mode-1d8dba8a7-slim --format '{{.Id}}')" != "${EXPECTED_CANDIDATE_IMAGE_ID}" ]]; then
  echo candidate_image_anchor_mismatch
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
db_revision=$(docker exec -i "${DB_CONTAINER}" psql -X -v ON_ERROR_STOP=1 -U "${db_user}" -d "${db_name}" -At <<'SQL'
SELECT version_num FROM alembic_version;
SQL
)
if [[ "${db_revision}" != "${TARGET_REVISION}" ]]; then
  echo candidate_schema_not_ready
  exit 1
fi

docker compose -f "${COMPOSE_FILE}" -f "${OVERRIDE_FILE}" config --quiet
anchor_dir=${PREP_ROOT}/cutover-anchors/$(date +%Y%m%d-%H%M%S)
mkdir -p "${anchor_dir}"
cp --preserve=mode,timestamps "${COMPOSE_FILE}" "${anchor_dir}/compose.yaml.before"
docker inspect "${WEB_CONTAINER}" --format 'container_id={{.Id}} image_ref={{.Config.Image}} image_id={{.Image}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restarts={{.RestartCount}} started={{.State.StartedAt}}' > "${anchor_dir}/open-webui.before.txt"

docker compose -f "${COMPOSE_FILE}" -f "${OVERRIDE_FILE}" up -d --no-deps --force-recreate open-webui

for attempt in $(seq 1 150); do
  health=$(docker inspect "${WEB_CONTAINER}" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}')
  if [[ "${health}" == healthy ]]; then
    break
  fi
  if [[ "${health}" == unhealthy || ${attempt} -eq 150 ]]; then
    echo candidate_health_failed
    exit 1
  fi
  sleep 2
done

running_image_id=$(docker inspect "${WEB_CONTAINER}" --format '{{.Image}}')
if [[ "${running_image_id}" != "${EXPECTED_CANDIDATE_IMAGE_ID}" ]]; then
  echo running_candidate_image_mismatch
  exit 1
fi

process_table=$(docker top "${WEB_CONTAINER}" -eo pid,ppid,args)
master_pid=$(awk '$0 ~ /-m uvicorn/ && $0 ~ /--workers 4/ {print $1; exit}' <<< "${process_table}")
if [[ -z "${master_pid}" ]]; then
  echo uvicorn_master_missing
  exit 1
fi
worker_pids=$(awk -v master_pid="${master_pid}" '$2 == master_pid && $0 !~ /resource_tracker/ {print $1}' <<< "${process_table}")
worker_count=$(awk 'NF {count += 1} END {print count + 0}' <<< "${worker_pids}")
if [[ "${worker_count}" != 4 ]]; then
  printf 'worker_count=%s expected=4\n' "${worker_count}"
  exit 1
fi

safe_env=$(docker inspect "${WEB_CONTAINER}" --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^(ENABLE_DB_MIGRATIONS|UVICORN_WORKERS)=')
if ! grep -Fxq 'ENABLE_DB_MIGRATIONS=false' <<< "${safe_env}" || ! grep -Fxq 'UVICORN_WORKERS=4' <<< "${safe_env}"; then
  echo candidate_runtime_env_mismatch
  exit 1
fi

docker exec "${WEB_CONTAINER}" curl -fsS http://127.0.0.1:8080/health >/dev/null
docker inspect "${WEB_CONTAINER}" --format 'container_id={{.Id}} image_ref={{.Config.Image}} image_id={{.Image}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restarts={{.RestartCount}} started={{.State.StartedAt}}' > "${anchor_dir}/open-webui.after.txt"
printf 'anchor_dir=%s\n' "${anchor_dir}"
printf 'candidate_image_id=%s\n' "${running_image_id}"
printf 'workers=%s\n' "${worker_count}"
printf 'worker_pids=%s\n' "${worker_pids//$'\n'/ }"
