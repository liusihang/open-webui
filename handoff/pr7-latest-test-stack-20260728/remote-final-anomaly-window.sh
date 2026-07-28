#!/usr/bin/env bash
set -Eeuo pipefail

until=$(date --iso-8601=seconds)
docker logs \
  --timestamps \
  --since 2026-07-28T02:20:00+08:00 \
  --until "${until}" \
  open-webui-pr7 2>&1 | grep -Ea 'runtime_finalization|ReadTimeout|Child process .* died|Waiting for child process|Finished server process|Application shutdown complete' || true
