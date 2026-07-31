#!/usr/bin/env bash
set -euo pipefail

TEST_DIR='/home/aiserver/staging/openwebui-pr7-eea11194ed-test'
STATE_DIR="${TEST_DIR}/deploy-v011-4d3543438b"
WEBUI_CONTAINER='open-webui-pr7'
RUNTIME_CONTAINER='openwebui-pr7-agentscope-runtime'

docker inspect --format 'webui={{.Id}}|{{.Image}}|{{.State.Status}}|{{.State.Health.Status}}|{{.RestartCount}}' "${WEBUI_CONTAINER}"
docker top "${WEBUI_CONTAINER}" -eo pid,ppid,args
docker logs --since 10m --timestamps "${WEBUI_CONTAINER}" >"${STATE_DIR}/acceptance-failure-webui.log" 2>&1
docker logs --since 10m --timestamps "${RUNTIME_CONTAINER}" >"${STATE_DIR}/acceptance-failure-runtime.log" 2>&1

printf 'webui_error_signals\n'
grep -Ein 'Traceback|ERROR|Exception|worker.*(died|exited)|segmentation fault|oom' "${STATE_DIR}/acceptance-failure-webui.log" | tail -n 120 || true
printf 'runtime_error_signals\n'
grep -Ein 'Traceback|ERROR|Exception|worker.*(died|exited)|segmentation fault|oom' "${STATE_DIR}/acceptance-failure-runtime.log" | tail -n 120 || true
