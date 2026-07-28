#!/usr/bin/env bash
set -Eeuo pipefail

export TARGET_CONTAINER=open-webui-pr7
export EXPECTED_CONTAINER_ID=715d9301220d94b8e4bb1d58a01b67c17358fca7d7bb1ad2465885b2b22af714
export EXPECTED_IMAGE_ID=sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b
export RUN_DIR=/home/aiserver/staging/pr7-announcement-hotpatch-6ba5c1398/isolated
export HEALTH_URL=http://127.0.0.1:18085/health
export BASE_URL=http://127.0.0.1:18085
export HOTPATCH_RUN_ID=announcement-6ba5c1398-isolated

case "${1:-}" in
  prepare)
    exec bash /tmp/pr7-announcement-prepare.sh
    ;;
  install)
    exec bash /tmp/pr7-announcement-install.sh
    ;;
  rollback)
    exec bash /tmp/pr7-announcement-rollback.sh
    ;;
  *)
    echo 'usage: run-isolated-hotpatch.sh prepare|install|rollback' >&2
    exit 2
    ;;
esac
