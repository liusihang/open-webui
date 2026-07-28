#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
COMPOSE_FILE=${COMPOSE_FILE:-${SCRIPT_DIR}/compose.live-pr7-dual-mode-1d8dba8a7.yaml}
PREPARE_SCRIPT=${PREPARE_SCRIPT:-${SCRIPT_DIR}/live-prepare-runtime-controlled.sh}
DEPLOY_SCRIPT=${DEPLOY_SCRIPT:-${SCRIPT_DIR}/live-deploy-controlled.sh}
ROLLBACK_SCRIPT=${ROLLBACK_SCRIPT:-${SCRIPT_DIR}/live-rollback-controlled.sh}

compose_text=$(<"${COMPOSE_FILE}")
prepare_text=$(<"${PREPARE_SCRIPT}")
deploy_text=$(<"${DEPLOY_SCRIPT}")
rollback_text=$(<"${ROLLBACK_SCRIPT}")

for required in \
  'agentscope-runtime:' \
  'open-webui-pr7-agentscope-runtime:742f686182-true-final-stream' \
  'container_name: openwebui-agentscope-runtime' \
  'AGENT_RUNTIME_STATE_PATH: /var/lib/agentscope-runtime/runtime-state.sqlite3' \
  'AGENT_RUNTIME_BASE_URL: http://agentscope-runtime:8000' \
  'AGENT_RUNTIME_SERVICE_TOKEN:' \
  'OPENWEBUI_SERVICE_TOKEN:' \
  'condition: service_healthy'; do
  if ! grep -Fq "${required}" <<< "${compose_text}"; then
    printf 'missing_runtime_compose_contract=%s\n' "${required}"
    exit 1
  fi
done

if [[ ! -x "${PREPARE_SCRIPT}" ]]; then
  echo runtime_prepare_script_missing_or_not_executable
  exit 1
fi
if ! grep -Fq 'RUNTIME_STATE_DIR=${RUNTIME_STATE_DIR:-${PREP_ROOT}/runtime-state}' <<< "${prepare_text}"; then
  echo runtime_state_default_not_owner_writable
  exit 1
fi

for required in \
  'RUNTIME_ENV_FILE' \
  'openwebui-agentscope-runtime' \
  'EXPECTED_RUNTIME_IMAGE_ID' \
  'runtime_not_healthy' \
  'AGENT_RUNTIME_BASE_URL=http://agentscope-runtime:8000'; do
  if ! grep -Fq "${required}" <<< "${deploy_text}"; then
    printf 'missing_runtime_deploy_contract=%s\n' "${required}"
    exit 1
  fi
done

for required in \
  'openwebui-agentscope-runtime' \
  'stop' \
  'rm'; do
  if ! grep -Fq "${required}" <<< "${rollback_text}"; then
    printf 'missing_runtime_rollback_contract=%s\n' "${required}"
    exit 1
  fi
done

printf 'live_agent_runtime_release=contract_passed\n'
