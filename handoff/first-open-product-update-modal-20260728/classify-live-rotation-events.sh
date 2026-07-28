#!/usr/bin/env bash
set -Eeuo pipefail

docker logs \
  --timestamps \
  --since 2026-07-28T06:33:58Z \
  --until 2026-07-28T06:38:00Z \
  open-webui 2>&1 |
  awk '
    /Started server process/ ||
    /Child process \[[0-9]+\] died/ ||
    /remote origin is not allowed/ ||
    /PaddleOCR-VL/ ||
    /Failed to process/ ||
    /RuntimeError:/ ||
    /ValueError:/ ||
    /ERROR/ {
      print
    }
  ' |
  sed -E 's/(Authorization: Bearer )[A-Za-z0-9._-]+/\1[REDACTED]/g'
