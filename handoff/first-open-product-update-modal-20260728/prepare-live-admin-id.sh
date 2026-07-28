#!/usr/bin/env bash
set -Eeuo pipefail

db_container=openwebui-db
private_dir=/home/aiserver/staging/pr7-announcement-hotpatch-6ba5c1398/live/private
db_user=
db_name=

while IFS= read -r entry; do
  case "$entry" in
    POSTGRES_USER=*) db_user=${entry#*=} ;;
    POSTGRES_DB=*) db_name=${entry#*=} ;;
  esac
done < <(docker inspect "$db_container" --format '{{range .Config.Env}}{{println .}}{{end}}')

[[ -n "$db_user" ]]
[[ -n "$db_name" ]]
mkdir -p "$private_dir"
chmod 700 "$private_dir"

docker exec -i "$db_container" psql \
  -X \
  -v ON_ERROR_STOP=1 \
  -U "$db_user" \
  -d "$db_name" \
  -At >"$private_dir/admin.id" <<'SQL'
SELECT id FROM "user" WHERE role = 'admin' ORDER BY created_at LIMIT 1;
SQL

[[ -s "$private_dir/admin.id" ]]
[[ "$(wc -l <"$private_dir/admin.id" | tr -d ' ')" == 1 ]]
chmod 600 "$private_dir/admin.id"
admin_id_hash=$(sha256sum "$private_dir/admin.id" | awk '{print substr($1, 1, 12)}')
printf 'live_admin_id_prepared=true admin_id_hash=%s\n' "$admin_id_hash"
