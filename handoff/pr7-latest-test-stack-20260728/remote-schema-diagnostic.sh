#!/usr/bin/env bash
set -Eeuo pipefail

DB_CONTAINER=openwebui-pr7-db

db_user=
db_name=
while IFS= read -r entry; do
  case "${entry}" in
    POSTGRES_USER=*) db_user=${entry#*=} ;;
    POSTGRES_DB=*) db_name=${entry#*=} ;;
  esac
done < <(docker inspect "${DB_CONTAINER}" --format '{{range .Config.Env}}{{println .}}{{end}}')

docker exec -i "${DB_CONTAINER}" psql -X -v ON_ERROR_STOP=1 -U "${db_user}" -d "${db_name}" -At <<'SQL'
SELECT 'revision=' || version_num FROM alembic_version;
SELECT 'current_schema=' || current_schema();
SHOW search_path;
SELECT 'table=' || table_schema || '.' || table_name
FROM information_schema.tables
WHERE table_name IN (
  'chat',
  'conversation_mode_profile_revision',
  'conversation_mode_profile_head',
  'conversation_mode_profile_temporary_binding'
)
ORDER BY table_schema, table_name;
SELECT 'column=' || table_schema || '.' || table_name || '.' || column_name
FROM information_schema.columns
WHERE column_name = 'mode_profile_revision_id'
ORDER BY table_schema, table_name;
SELECT 'regclass_chat=' || COALESCE(to_regclass('chat')::text, 'null');
SELECT 'regclass_public_chat=' || COALESCE(to_regclass('public.chat')::text, 'null');
SQL

docker inspect open-webui-pr7 --format 'web_status={{.State.Status}} image_id={{.Image}}'
docker inspect openwebui-pr7-agentscope-runtime --format 'runtime_status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'
docker inspect open-webui --format 'formal_container={{.Id}} image_id={{.Image}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restarts={{.RestartCount}}'
