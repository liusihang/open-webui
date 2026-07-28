#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

RELEASE_DIR=${RELEASE_DIR:-/home/aiserver/staging/pr7-live-prep-20260727/release}
COMPOSE_FILE=${COMPOSE_FILE:-/srv/openwebui-migration/compose.yaml}
OVERRIDE_FILE=${OVERRIDE_FILE:-${RELEASE_DIR}/compose.live-pr7-dual-mode-1d8dba8a7.yaml}
WEB_CONTAINER=${WEB_CONTAINER:-open-webui}
EXPECTED_OLD_IMAGE_ID=${EXPECTED_OLD_IMAGE_ID:-sha256:7ec820b71fa94205b273cb8cd00344a130e1921ae8e643ba6192b0e58933bd45}
SOURCE_REVISION=${SOURCE_REVISION:-f3a4b5c6d7e8}
EXPECTED_WORKERS=${EXPECTED_WORKERS:-4}

tmp_dir=$(mktemp -d /home/aiserver/staging/pr7-live-prep-20260727/guard-test.XXXXXX)
runtime_env=${tmp_dir}/runtime.env
runtime_state=${tmp_dir}/runtime-state
mkdir -p "${runtime_state}"
{
  printf 'AGENT_RUNTIME_SERVICE_TOKEN=guard-not-a-real-token\n'
  printf 'OPENWEBUI_SERVICE_TOKEN=guard-not-a-real-token\n'
  printf 'PR7_RUNTIME_ENV_FILE=%s\n' "${runtime_env}"
  printf 'PR7_RUNTIME_STATE_DIR=%s\n' "${runtime_state}"
  printf 'PR7_HTTP_PROXY=\nPR7_HTTPS_PROXY=\nPR7_ALL_PROXY=\n'
  printf 'PR7_NO_PROXY=127.0.0.1,localhost,open-webui,agentscope-runtime,db,redis,bifrost,onlyoffice\n'
} > "${runtime_env}"
chmod 600 "${runtime_env}"
cleanup() {
  find "${tmp_dir}" -type f -delete
  find "${tmp_dir}" -depth -type d -empty -delete
}
trap cleanup EXIT

before_anchor=$(docker inspect "${WEB_CONTAINER}" --format 'container_id={{.Id}} image_id={{.Image}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restarts={{.RestartCount}} started={{.State.StartedAt}}')

expect_guard() {
  local expected=$1
  local output=$2
  shift 2
  if "$@" > "${output}" 2>&1; then
    echo guard_unexpectedly_succeeded
    exit 1
  fi
  if ! grep -Fxq "${expected}" "${output}"; then
    printf 'guard_output_mismatch expected=%s\n' "${expected}"
    exit 1
  fi
}

expect_guard \
  live_deploy_confirmation_missing \
  "${tmp_dir}/deploy.out" \
  "${RELEASE_DIR}/live-deploy-controlled.sh"

expect_guard \
  live_maintenance_confirmation_missing \
  "${tmp_dir}/maintenance.out" \
  "${RELEASE_DIR}/live-enter-maintenance-controlled.sh"

expect_guard \
  live_runtime_prepare_confirmation_missing \
  "${tmp_dir}/runtime-prepare.out" \
  "${RELEASE_DIR}/live-prepare-runtime-controlled.sh"

expect_guard \
  live_rollback_confirmation_missing \
  "${tmp_dir}/rollback.out" \
  "${RELEASE_DIR}/live-rollback-controlled.sh"

expect_guard \
  live_upgrade_confirmation_missing \
  "${tmp_dir}/migration.out" \
  env MIGRATION_ACTION=upgrade "${RELEASE_DIR}/live-migrate-controlled.sh"

expect_guard \
  'live profile confirmation missing' \
  "${tmp_dir}/profiles.out" \
  python3 "${RELEASE_DIR}/live-apply-admin-profiles.py"

"${RELEASE_DIR}/test-live-admin-mode-profile-template.sh" > "${tmp_dir}/profile-template.out"
"${RELEASE_DIR}/test-live-agent-runtime-release.sh" > "${tmp_dir}/agent-runtime-release.out"

resolved_config=$(docker compose \
  --env-file /srv/openwebui-migration/.env \
  --env-file "${runtime_env}" \
  -f "${COMPOSE_FILE}" \
  -f "${OVERRIDE_FILE}" \
  config --format json)
resolved_image=$(jq -r '.services["open-webui"].image' <<< "${resolved_config}")
resolved_workers=$(jq -r '.services["open-webui"].environment.UVICORN_WORKERS' <<< "${resolved_config}")
resolved_migrations=$(jq -r '.services["open-webui"].environment.ENABLE_DB_MIGRATIONS' <<< "${resolved_config}")
resolved_agent_url=$(jq -r '.services["open-webui"].environment.AGENT_RUNTIME_BASE_URL' <<< "${resolved_config}")
resolved_runtime_image=$(jq -r '.services["agentscope-runtime"].image' <<< "${resolved_config}")
resolved_runtime_workers=$(jq -r '.services["agentscope-runtime"].environment.UVICORN_WORKERS' <<< "${resolved_config}")
if [[ "${resolved_image}" != open-webui:pr7-chat-agent-dual-mode-1d8dba8a7-slim ]]; then
  echo resolved_image_mismatch
  exit 1
fi
if [[ "${resolved_workers}" != 4 || "${resolved_migrations}" != false ]]; then
  echo resolved_environment_mismatch
  exit 1
fi
if [[ "${resolved_agent_url}" != http://agentscope-runtime:8000 ]]; then
  echo resolved_agent_runtime_url_mismatch
  exit 1
fi
if [[ "${resolved_runtime_image}" != open-webui-pr7-agentscope-runtime:742f686182-true-final-stream || "${resolved_runtime_workers}" != 1 ]]; then
  echo resolved_agent_runtime_service_mismatch
  exit 1
fi

EXPECTED_IMAGE_ID="${EXPECTED_OLD_IMAGE_ID}" \
EXPECTED_REVISION="${SOURCE_REVISION}" \
EXPECTED_WORKERS="${EXPECTED_WORKERS}" \
  "${RELEASE_DIR}/live-verify-runtime.sh" > "${tmp_dir}/verify.out"

after_anchor=$(docker inspect "${WEB_CONTAINER}" --format 'container_id={{.Id}} image_id={{.Image}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restarts={{.RestartCount}} started={{.State.StartedAt}}')
if [[ "${after_anchor}" != "${before_anchor}" ]]; then
  echo live_anchor_changed_during_guard_tests
  exit 1
fi

printf 'guard_tests=passed\n'
printf 'compose_override=passed\n'
printf 'profile_template=accepted_latest_stack_defaults\n'
printf 'agent_runtime_release=passed\n'
printf 'live_anchor_unchanged=yes\n'
