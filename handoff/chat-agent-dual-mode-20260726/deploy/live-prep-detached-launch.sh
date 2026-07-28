#!/usr/bin/env bash
set -Eeuo pipefail

JOB_SCRIPT=${JOB_SCRIPT:?JOB_SCRIPT is required}
RUN_ID=${RUN_ID:?RUN_ID is required}

if [[ "${JOB_SCRIPT}" != /tmp/live-prep-*.sh ]]; then
  echo job_script_not_allowed
  exit 1
fi
if [[ ! -r "${JOB_SCRIPT}" ]]; then
  echo job_script_missing
  exit 1
fi

export RUN_ID
export LOG_STDOUT=false

setsid --fork /usr/bin/bash "${JOB_SCRIPT}" </dev/null >/dev/null 2>&1
