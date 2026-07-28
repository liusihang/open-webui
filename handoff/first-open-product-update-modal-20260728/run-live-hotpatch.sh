#!/usr/bin/env bash
set -Eeuo pipefail

export TARGET_CONTAINER=open-webui
export EXPECTED_CONTAINER_ID=ae1b858332b7bbe252359d46e610a7b595fa6bad36b459187955737cb386e255
export EXPECTED_IMAGE_ID=sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b
export RUN_DIR=/home/aiserver/staging/pr7-announcement-hotpatch-6ba5c1398/live
export HEALTH_URL=http://127.0.0.1/health
export BASE_URL=http://127.0.0.1
export HOTPATCH_RUN_ID=announcement-6ba5c1398-live
export PRESERVED_FILE=/app/backend/open_webui/retrieval/vector/dbs/pgvector.py
export PRESERVED_SHA=2ce356413ce67047739487fc0833c69c912cef0fb456b2f58bc9bd35b543f156

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
    echo 'usage: run-live-hotpatch.sh prepare|install|rollback' >&2
    exit 2
    ;;
esac
