#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR='/tmp/openwebui-v011-deploy'
TEST_DIR='/home/aiserver/staging/openwebui-pr7-eea11194ed-test'
STATE_DIR="${TEST_DIR}/deploy-v011-4d3543438b"
CANDIDATE='compose.webui-v011-4d3543438b.yaml'
ROLLBACK='compose.webui-v011-rollback.yaml'

test -s "${SOURCE_DIR}/${CANDIDATE}"
test -s "${SOURCE_DIR}/${ROLLBACK}"
install -m 0644 "${SOURCE_DIR}/${CANDIDATE}" "${TEST_DIR}/${CANDIDATE}"
install -m 0644 "${SOURCE_DIR}/${ROLLBACK}" "${TEST_DIR}/${ROLLBACK}"

compose=(
  docker compose
  --project-name openwebui-pr7
  --file "${TEST_DIR}/compose.yaml"
  --file "${TEST_DIR}/compose.webui-rebuild-eaff69b0d317.yaml"
  --file "${TEST_DIR}/compose.webui-eaff69-no-migrations.yaml"
  --file "${TEST_DIR}/compose.webui-4a4e43e206.yaml"
  --file "${TEST_DIR}/compose.agent-runtime-742f686182.yaml"
  --file "${TEST_DIR}/${CANDIDATE}"
)
"${compose[@]}" config --services >"${STATE_DIR}/candidate-compose-services.txt"
grep -Fx 'open-webui-pr7' "${STATE_DIR}/candidate-compose-services.txt" >/dev/null

sha256sum "${TEST_DIR}/${CANDIDATE}" "${TEST_DIR}/${ROLLBACK}" >"${STATE_DIR}/compose-overrides.sha256"
cat "${STATE_DIR}/compose-overrides.sha256"
cat "${STATE_DIR}/candidate-compose-services.txt"
