#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

STACK_DIR=/home/aiserver/staging/openwebui-pr7-eea11194ed-test
WEB_CONTAINER=open-webui-pr7
EVIDENCE_DIR=${STACK_DIR}/evidence/pr7-latest-test-stack-20260728

mkdir -p "${EVIDENCE_DIR}"
docker cp "${STACK_DIR}/container-acceptance.py" "${WEB_CONTAINER}:/tmp/container-acceptance.py"
docker cp "${STACK_DIR}/pr7_dual_mode_four_worker_probe.py" "${WEB_CONTAINER}:/tmp/pr7_dual_mode_four_worker_probe.py"
docker exec -i -e PYTHONPATH=/app/backend "${WEB_CONTAINER}" python /tmp/container-acceptance.py
docker cp "${WEB_CONTAINER}:/tmp/pr7-latest-stack-acceptance.json" "${EVIDENCE_DIR}/acceptance.json"
chmod 600 "${EVIDENCE_DIR}/acceptance.json"
