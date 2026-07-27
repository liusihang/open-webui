#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

PREP_ROOT=${PREP_ROOT:-/home/aiserver/staging/pr7-live-prep-20260727}
WEB_CONTAINER=${WEB_CONTAINER:-open-webui}
EXPECTED_OLD_IMAGE_ID=${EXPECTED_OLD_IMAGE_ID:-sha256:7ec820b71fa94205b273cb8cd00344a130e1921ae8e643ba6192b0e58933bd45}

if [[ "${CONFIRM_LIVE_MAINTENANCE:-}" != "stop-only-open-webui-on-aiserver-live" ]]; then
  echo live_maintenance_confirmation_missing
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

anchor_dir=${PREP_ROOT}/cutover-anchors/$(date +%Y%m%d-%H%M%S)
mkdir -p "${anchor_dir}"
docker inspect "${WEB_CONTAINER}" --format 'container_id={{.Id}} image_ref={{.Config.Image}} image_id={{.Image}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restarts={{.RestartCount}} started={{.State.StartedAt}}' > "${anchor_dir}/open-webui.pre-maintenance.txt"

docker stop --time 30 "${WEB_CONTAINER}" >/dev/null
if [[ "$(docker inspect "${WEB_CONTAINER}" --format '{{.State.Status}}')" != exited ]]; then
  echo live_webui_stop_failed
  exit 1
fi

printf 'maintenance_anchor_dir=%s\n' "${anchor_dir}"
printf 'open_webui_status=exited\n'
