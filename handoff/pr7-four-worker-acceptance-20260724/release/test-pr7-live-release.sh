#!/usr/bin/env bash
set -Eeuo pipefail

DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SCRIPT=$DIR/pr7-live-release.sh
COMPOSE=$DIR/compose.pr7-live.yaml
PID_PROBE=$DIR/four-worker-pid-probe.py

bash -n "$SCRIPT"
python3 -m py_compile "$PID_PROBE"

script_text=$(<"$SCRIPT")
compose_text=$(<"$COMPOSE")

for required in \
  'EXPECTED_LIVE_CONTAINER_ID=78faa81d' \
  'EXPECTED_BASE_COMPOSE_SHA256=' \
  'EXPECTED_BASE_ENV_SHA256=' \
  'CANDIDATE_IMAGE_ID=sha256:2d3a9138' \
  'RUNTIME_IMAGE_ID=sha256:f7396ba' \
  'OLD_DB_HEAD=f3a4b5c6d7e8' \
  'NEW_DB_HEAD=f8a9b0c1d2e3' \
  'CONFIRM_SWITCH' \
  'CONFIRM_ROLLBACK_FAST' \
  'CONFIRM_FULL_RESTORE' \
  'pg_dump' \
  'pg_restore' \
  'alembic -c alembic.ini upgrade head' \
  'apply_pipe_upgrade' \
  'cache:functions:bifrostapi:version' \
  'four-worker-pids.json' \
  'Started server process' \
  'Startup singleton tasks already running in another worker' \
  'rollback_fast' \
  'restore_f3'; do
  grep -Fq "$required" <<<"$script_text"
done

for required in \
  'agentscope-runtime:' \
  'open-webui:agentmode-v0102-5b35e9f1b-slim-release' \
  'open-webui-pr7-agentscope-runtime:742f686182-true-final-stream' \
  "UVICORN_WORKERS: '4'" \
  "ENABLE_DB_MIGRATIONS: 'false'" \
  'AGENT_RUNTIME_BASE_URL: http://agentscope-runtime:8000' \
  'AGENT_RUNTIME_STATE_PATH: /var/lib/agentscope-runtime/runtime-state.sqlite3'; do
  grep -Fq "$required" <<<"$compose_text"
done

if grep -Eq 'docker (system|builder|image|volume|network) prune|docker compose .* down|git reset --hard' <<<"$script_text"; then
  echo 'release script contains broad destructive cleanup' >&2
  exit 1
fi

if grep -Eq 'Authorization: Bearer|WEBUI_SECRET_KEY=[^"$]' <<<"$script_text"; then
  echo 'release script appears to persist or print a secret' >&2
  exit 1
fi

printf 'PR7 live release package static contract passed\n'
