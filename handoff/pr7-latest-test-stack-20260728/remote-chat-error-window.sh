#!/usr/bin/env bash
set -Eeuo pipefail

docker logs \
  --timestamps \
  --since 2026-07-28T02:14:15+08:00 \
  --until 2026-07-28T02:14:55+08:00 \
  open-webui-pr7 2>&1 | grep -Ea 'Error processing chat payload|Provider returned HTTP|No response returned|Model not found|unknown provider' || true
