#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

STACK_DIR=${STACK_DIR:-/srv/openwebui-migration}
COMPOSE_FILE=${COMPOSE_FILE:-${STACK_DIR}/compose.yaml}
OVERRIDE_FILE=${OVERRIDE_FILE:-/home/aiserver/staging/pr7-live-prep-20260727/release/compose.live-pr7-dual-mode-1d8dba8a7.yaml}
PREP_ROOT=${PREP_ROOT:-/home/aiserver/staging/pr7-live-prep-20260727}
PRIVATE_DIR=${PRIVATE_DIR:-${PREP_ROOT}/private}
RUNTIME_ENV_FILE=${RUNTIME_ENV_FILE:-${PRIVATE_DIR}/runtime.env}
RUNTIME_STATE_DIR=${RUNTIME_STATE_DIR:-${PREP_ROOT}/runtime-state}
PROJECT_NAME=${PROJECT_NAME:-openwebui-migration}
WEB_CONTAINER=${WEB_CONTAINER:-open-webui}
RUNTIME_CONTAINER=${RUNTIME_CONTAINER:-openwebui-agentscope-runtime}
RUNTIME_IMAGE=${RUNTIME_IMAGE:-open-webui-pr7-agentscope-runtime:742f686182-true-final-stream}
EXPECTED_RUNTIME_IMAGE_ID=${EXPECTED_RUNTIME_IMAGE_ID:-sha256:f7396ba23e49f934216ba8fc4b38c695b7f639722d852b44234769c66ca7f6e9}
EXPECTED_OLD_IMAGE_ID=${EXPECTED_OLD_IMAGE_ID:-sha256:7ec820b71fa94205b273cb8cd00344a130e1921ae8e643ba6192b0e58933bd45}
EXPECTED_COMPOSE_SHA256=${EXPECTED_COMPOSE_SHA256:-7fff73a9037687460bd6c27669e9224203241546928173106c9999d6b3425da1}
temp=

cleanup() {
  if [[ -n "${temp}" && -f "${temp}" ]]; then
    unlink "${temp}"
  fi
}
trap cleanup EXIT

if [[ "${CONFIRM_LIVE_RUNTIME_PREPARE:-}" != "prepare-pr7-agent-runtime-on-aiserver-live" ]]; then
  echo live_runtime_prepare_confirmation_missing
  exit 1
fi
if [[ "$(docker inspect "${WEB_CONTAINER}" --format '{{.Image}}')" != "${EXPECTED_OLD_IMAGE_ID}" ]]; then
  echo old_image_anchor_mismatch
  exit 1
fi
if [[ "$(docker inspect "${WEB_CONTAINER}" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}')" != healthy ]]; then
  echo old_live_not_healthy
  exit 1
fi
if [[ "$(docker image inspect "${RUNTIME_IMAGE}" --format '{{.Id}}')" != "${EXPECTED_RUNTIME_IMAGE_ID}" ]]; then
  echo runtime_image_anchor_mismatch
  exit 1
fi
if [[ "$(sha256sum "${COMPOSE_FILE}" | awk '{print $1}')" != "${EXPECTED_COMPOSE_SHA256}" ]]; then
  echo compose_anchor_mismatch
  exit 1
fi

container_env_value() {
  local container=$1
  local key=$2
  docker inspect "${container}" --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | sed -n "s/^${key}=//p" \
    | tail -n 1
}

append_no_proxy() {
  local current=$1
  local item
  for item in 127.0.0.1 localhost open-webui agentscope-runtime db redis bifrost onlyoffice; do
    case ",${current}," in
      *",${item},"*) ;;
      *) current=${current:+${current},}${item} ;;
    esac
  done
  printf '%s\n' "${current}"
}

install -d -m 700 "${PRIVATE_DIR}" "${RUNTIME_STATE_DIR}"
if [[ ! -f "${RUNTIME_ENV_FILE}" ]]; then
  token=$(openssl rand -hex 32)
  http_proxy=$(container_env_value "${WEB_CONTAINER}" HTTP_PROXY)
  https_proxy=$(container_env_value "${WEB_CONTAINER}" HTTPS_PROXY)
  all_proxy=$(container_env_value "${WEB_CONTAINER}" ALL_PROXY)
  no_proxy=$(append_no_proxy "$(container_env_value "${WEB_CONTAINER}" NO_PROXY)")
  temp=${RUNTIME_ENV_FILE}.tmp.$$
  {
    printf 'AGENT_RUNTIME_SERVICE_TOKEN=%s\n' "${token}"
    printf 'OPENWEBUI_SERVICE_TOKEN=%s\n' "${token}"
    printf 'PR7_RUNTIME_ENV_FILE=%s\n' "${RUNTIME_ENV_FILE}"
    printf 'PR7_RUNTIME_STATE_DIR=%s\n' "${RUNTIME_STATE_DIR}"
    printf 'PR7_HTTP_PROXY=%s\n' "${http_proxy}"
    printf 'PR7_HTTPS_PROXY=%s\n' "${https_proxy}"
    printf 'PR7_ALL_PROXY=%s\n' "${all_proxy}"
    printf 'PR7_NO_PROXY=%s\n' "${no_proxy}"
  } > "${temp}"
  chmod 600 "${temp}"
  mv "${temp}" "${RUNTIME_ENV_FILE}"
  temp=
fi

if [[ "$(stat --format '%a' "${RUNTIME_ENV_FILE}")" != 600 ]]; then
  echo runtime_env_permissions_mismatch
  exit 1
fi
for key in AGENT_RUNTIME_SERVICE_TOKEN OPENWEBUI_SERVICE_TOKEN PR7_RUNTIME_ENV_FILE PR7_RUNTIME_STATE_DIR PR7_NO_PROXY; do
  if ! grep -q "^${key}=." "${RUNTIME_ENV_FILE}"; then
    printf 'runtime_env_key_missing=%s\n' "${key}"
    exit 1
  fi
done

compose_runtime() {
  docker compose \
    --project-name "${PROJECT_NAME}" \
    --env-file "${STACK_DIR}/.env" \
    --env-file "${RUNTIME_ENV_FILE}" \
    -f "${COMPOSE_FILE}" \
    -f "${OVERRIDE_FILE}" \
    "$@"
}

compose_runtime config --quiet
compose_runtime up -d --no-deps agentscope-runtime

for attempt in $(seq 1 120); do
  health=$(docker inspect "${RUNTIME_CONTAINER}" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}')
  if [[ "${health}" == healthy ]]; then
    break
  fi
  if [[ "${health}" == unhealthy || ${attempt} -eq 120 ]]; then
    echo runtime_not_healthy
    exit 1
  fi
  sleep 2
done

if [[ "$(docker inspect "${RUNTIME_CONTAINER}" --format '{{.Image}}')" != "${EXPECTED_RUNTIME_IMAGE_ID}" ]]; then
  echo running_runtime_image_mismatch
  exit 1
fi
if [[ "$(docker inspect "${RUNTIME_CONTAINER}" --format '{{.RestartCount}}')" != 0 ]]; then
  echo runtime_restart_count_nonzero
  exit 1
fi
file_token=$(awk -F= '$1 == "AGENT_RUNTIME_SERVICE_TOKEN" {print $2; exit}' "${RUNTIME_ENV_FILE}")
runtime_token=$(container_env_value "${RUNTIME_CONTAINER}" AGENT_RUNTIME_SERVICE_TOKEN)
callback_token=$(container_env_value "${RUNTIME_CONTAINER}" OPENWEBUI_SERVICE_TOKEN)
if [[ -z "${file_token}" || "${runtime_token}" != "${file_token}" || "${callback_token}" != "${file_token}" ]]; then
  echo runtime_token_wiring_mismatch
  exit 1
fi
if [[ "$(container_env_value "${RUNTIME_CONTAINER}" AGENT_RUNTIME_STATE_PATH)" != /var/lib/agentscope-runtime/runtime-state.sqlite3 ]]; then
  echo runtime_state_path_mismatch
  exit 1
fi

web_networks=$(docker inspect "${WEB_CONTAINER}" --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{println}}{{end}}')
runtime_networks=$(docker inspect "${RUNTIME_CONTAINER}" --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{println}}{{end}}')
if ! grep -Fqx "${PROJECT_NAME}_default" <<< "${web_networks}" || ! grep -Fqx "${PROJECT_NAME}_default" <<< "${runtime_networks}"; then
  echo runtime_network_mismatch
  exit 1
fi

docker exec "${RUNTIME_CONTAINER}" python -c "import json,urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=10))['status'] == 'ok'"
printf 'runtime_env_file=%s\n' "${RUNTIME_ENV_FILE}"
printf 'runtime_state_dir=%s\n' "${RUNTIME_STATE_DIR}"
docker inspect "${RUNTIME_CONTAINER}" --format 'runtime={{.Id}} {{.Image}} {{.State.Status}} {{.State.Health.Status}} {{.RestartCount}} {{.State.StartedAt}}'
