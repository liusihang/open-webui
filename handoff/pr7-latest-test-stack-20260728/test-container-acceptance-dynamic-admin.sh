#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

if ! grep -Fq "ADMIN_USER_ID = os.environ.get(" "${SCRIPT_DIR}/container-acceptance.py"; then
  echo acceptance_admin_id_not_runtime_configurable
  exit 1
fi

printf 'container_acceptance_admin=runtime_configurable\n'
