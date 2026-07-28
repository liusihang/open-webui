#!/usr/bin/env bash
set -Eeuo pipefail

web_container=${LIVE_WEBUI_CONTAINER:-open-webui}
db_container=${DB_CONTAINER:-openwebui-db}
rotation_started=${ROTATION_STARTED:-2026-07-28T04:14:00Z}

echo container_anchor
docker inspect "$web_container" --format '{{.Id}} {{.Image}} {{.State.Health.Status}} {{.RestartCount}} {{.State.StartedAt}}'
docker top "$web_container" -eo pid,ppid,lstart,args

echo startup_log_markers
docker logs --since "$rotation_started" "$web_container" 2>&1 |
  awk '
    /Started server process/ ||
    /Waiting for application startup/ ||
    /Application startup complete/ ||
    /Child process \[[0-9]+\] died/ ||
    /Startup singleton/ ||
    /dependencies of functions and tools/ ||
    /Traceback \(most recent call last\)/ ||
    /ERROR/ {print}
  '

db_user=
db_name=
while IFS= read -r entry; do
  case "$entry" in
    POSTGRES_USER=*) db_user=${entry#*=} ;;
    POSTGRES_DB=*) db_name=${entry#*=} ;;
  esac
done < <(docker inspect "$db_container" --format '{{range .Config.Env}}{{println .}}{{end}}')

echo active_event_functions
docker exec -i "$db_container" psql \
  -X \
  -v ON_ERROR_STOP=1 \
  -U "$db_user" \
  -d "$db_name" \
  -At <<'SQL'
SELECT id || '|' || name
FROM function
WHERE type = 'event' AND is_active IS TRUE
ORDER BY id;
SQL

echo active_database_waits
docker exec -i "$db_container" psql \
  -X \
  -v ON_ERROR_STOP=1 \
  -U "$db_user" \
  -d "$db_name" \
  -At <<'SQL'
SELECT
  pid::text || '|' ||
  state || '|' ||
  coalesce(wait_event_type, '') || '|' ||
  coalesce(wait_event, '') || '|' ||
  coalesce(extract(epoch FROM clock_timestamp() - query_start)::bigint::text, '') || '|' ||
  coalesce(upper(substring(ltrim(query) FROM '^([a-zA-Z]+)')), '')
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
  AND state <> 'idle'
ORDER BY query_start;
SQL
