#!/usr/bin/env bash
set -Eeuo pipefail

export STACK_DIR=/home/aiserver/staging/openwebui-pr7-eea11194ed-test
export COMPOSE_FILE=${STACK_DIR}/compose.yaml
export WEB_CONTAINER=open-webui-pr7
export DB_CONTAINER=openwebui-pr7-db
export BACKUP_ROOT=${STACK_DIR}/backups/pr7-latest-test-stack-20260728
export EXPECTED_REVISION=f8a9b0c1d2e3
export RUN_ID=before-c0

exec "${STACK_DIR}/pr7-latest-test-backup-core.sh"
