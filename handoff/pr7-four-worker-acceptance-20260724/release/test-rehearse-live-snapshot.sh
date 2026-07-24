#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/rehearse-live-snapshot.sh"

bash -n "$SCRIPT"
source_text=$(<"$SCRIPT")

grep -Fq 'REHEARSAL_PREFIX=pr7-live-rehearsal' <<<"$source_text"
grep -Fq 'EXPECTED_LIVE_CONTAINER_ID=' <<<"$source_text"
grep -Fq 'EXPECTED_LIVE_IMAGE_ID=' <<<"$source_text"
grep -Fq 'assert_live_anchor' <<<"$source_text"
grep -Fq 'pg_dump' <<<"$source_text"
grep -Fq 'cp -a --reflink=auto' <<<"$source_text"
grep -Fq 'ENABLE_DB_MIGRATIONS=false' <<<"$source_text"
grep -Fq 'alembic -c alembic.ini upgrade head' <<<"$source_text"
grep -Fq 'UVICORN_WORKERS=4' <<<"$source_text"
grep -Fq 'AGENT_RUNTIME_BASE_URL=http://agentscope-runtime:8000' <<<"$source_text"
grep -Fq 'REHEARSAL_DB_URL=postgresql://${REHEARSAL_DB_USER}@${PG_CONTAINER}:5432/${REHEARSAL_DB_NAME}' <<<"$source_text"
grep -Fq 'REDIS_URL=redis://${REDIS_CONTAINER}:6379/0' <<<"$source_text"
grep -Fq -- "--health-cmd 'python -c" <<<"$source_text"
grep -Fq 'restore-f3' <<<"$source_text"
grep -Fq 'old-on-f8' <<<"$source_text"

if grep -Eq 'docker (stop|rm|restart|kill) (open-webui|openwebui-db|openwebui-redis)([[:space:]]|$)' <<<"$source_text"; then
  echo 'rehearsal script can mutate a formal live container' >&2
  exit 1
fi

if grep -Eq 'cd /srv/openwebui-migration.*docker compose|docker compose.*-p openwebui-migration.*(up|down|stop|restart)' <<<"$source_text"; then
  echo 'rehearsal script can run a mutating formal live compose command' >&2
  exit 1
fi

if grep -Eq 'docker (system|builder|image|volume|network) prune|docker compose .* down' <<<"$source_text"; then
  echo 'rehearsal script contains broad destructive Docker cleanup' >&2
  exit 1
fi

printf 'live snapshot rehearsal safety contract passed\n'
