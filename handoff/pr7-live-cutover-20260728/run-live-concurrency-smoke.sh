#!/usr/bin/env bash
set -Eeuo pipefail

db_container=${DB_CONTAINER:-openwebui-db}
web_container=${LIVE_WEBUI_CONTAINER:-open-webui}
probe=${CONCURRENCY_PROBE:-/tmp/pr7-four-worker-concurrency-probe.py}

db_user=
db_name=
while IFS= read -r entry; do
  case "$entry" in
    POSTGRES_USER=*) db_user=${entry#*=} ;;
    POSTGRES_DB=*) db_name=${entry#*=} ;;
  esac
done < <(docker inspect "$db_container" --format '{{range .Config.Env}}{{println .}}{{end}}')

admin_id=$(docker exec -i "$db_container" psql \
  -X \
  -v ON_ERROR_STOP=1 \
  -U "$db_user" \
  -d "$db_name" \
  -At <<'SQL'
SELECT id FROM "user" WHERE role = 'admin' ORDER BY created_at LIMIT 1;
SQL
)
[[ -n "$admin_id" ]] || {
  echo admin_user_missing
  exit 1
}
[[ -f "$probe" ]] || {
  echo concurrency_probe_missing
  exit 1
}

BASE_URL=http://127.0.0.1 \
ADMIN_USER_ID="$admin_id" \
MODEL_ID=bifrostapi.Cliproxy/gpt-5.5 \
WEB_CONTAINER="$web_container" \
python3 "$probe"
