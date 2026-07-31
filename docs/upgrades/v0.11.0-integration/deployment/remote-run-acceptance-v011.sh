#!/usr/bin/env bash
set -euo pipefail

STACK_DIR='/home/aiserver/staging/openwebui-pr7-eea11194ed-test'
SOURCE_DIR='/tmp/openwebui-v011-deploy'
WEB_CONTAINER='open-webui-pr7'
EVIDENCE_DIR="${STACK_DIR}/evidence/v011-4d3543438b-20260728"

mkdir -p "${EVIDENCE_DIR}"
docker cp "${STACK_DIR}/container-acceptance.py" "${WEB_CONTAINER}:/tmp/container-acceptance.py"
docker cp "${STACK_DIR}/pr7_dual_mode_four_worker_probe.py" "${WEB_CONTAINER}:/tmp/pr7_dual_mode_four_worker_probe.py"
docker cp "${SOURCE_DIR}/container-acceptance-v011-runner.py" "${WEB_CONTAINER}:/tmp/container-acceptance-v011-runner.py"
docker exec -i -e PYTHONPATH=/app/backend "${WEB_CONTAINER}" python /tmp/container-acceptance-v011-runner.py
docker cp "${WEB_CONTAINER}:/tmp/pr7-latest-stack-acceptance.json" "${EVIDENCE_DIR}/acceptance.json"
chmod 600 "${EVIDENCE_DIR}/acceptance.json"
sha256sum "${EVIDENCE_DIR}/acceptance.json" >"${EVIDENCE_DIR}/acceptance.json.sha256"
cat "${EVIDENCE_DIR}/acceptance.json.sha256"
