#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROBE=${SCRIPT_DIR}/pr7_four_worker_concurrency_probe.py

grep -Fq "WEB_CONTAINER = os.environ.get(" "${PROBE}" || {
  echo concurrency_web_container_not_configurable
  exit 1
}
grep -Fq "ProxyHandler({})" "${PROBE}" || {
  echo concurrency_probe_not_direct
  exit 1
}

printf 'concurrency_probe_target=runtime_configurable_direct\n'
