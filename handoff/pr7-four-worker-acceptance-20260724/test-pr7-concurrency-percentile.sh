#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROBE=${SCRIPT_DIR}/pr7_four_worker_concurrency_probe.py

grep -Fq 'math.ceil(len(elapsed) * 0.95) - 1' "${PROBE}" || {
  echo concurrency_p95_not_nearest_rank
  exit 1
}

printf 'concurrency_p95=nearest_rank\n'
