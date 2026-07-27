#!/usr/bin/env bash
set -Eeuo pipefail

DB_CONTAINER=${DB_CONTAINER:-openwebui-db}

db_user=
db_name=
while IFS= read -r entry; do
  case "${entry}" in
    POSTGRES_USER=*) db_user=${entry#*=} ;;
    POSTGRES_DB=*) db_name=${entry#*=} ;;
  esac
done < <(docker inspect "${DB_CONTAINER}" --format '{{range .Config.Env}}{{println .}}{{end}}')
if [[ -z "${db_user}" || -z "${db_name}" ]]; then
  echo database_identity_missing
  exit 1
fi

docker exec -i "${DB_CONTAINER}" psql -X -v ON_ERROR_STOP=1 -U "${db_user}" -d "${db_name}" -At -F $'\t' <<'SQL'
WITH terminal_resources AS (
  SELECT
    'terminal'::text AS resource_type,
    element ->> 'id' AS id,
    regexp_replace(COALESCE(element ->> 'name', ''), E'[\t\n\r]+', ' ', 'g') AS name,
    'terminal'::text AS subtype,
    COALESCE((element ->> 'enabled')::boolean, true) AS active
  FROM config
  CROSS JOIN LATERAL json_array_elements(
    CASE
      WHEN json_typeof(value) = 'array' THEN value
      ELSE '[]'::json
    END
  ) AS element
  WHERE key = 'terminal_server.connections'
), tool_resources AS (
  SELECT
    'tool'::text,
    id,
    regexp_replace(COALESCE(name, ''), E'[\t\n\r]+', ' ', 'g'),
    'tool'::text,
    true
  FROM tool
), skill_resources AS (
  SELECT
    'skill'::text,
    id,
    regexp_replace(COALESCE(name, ''), E'[\t\n\r]+', ' ', 'g'),
    'skill'::text,
    is_active
  FROM skill
), function_resources AS (
  SELECT
    'function'::text,
    id,
    regexp_replace(COALESCE(name, ''), E'[\t\n\r]+', ' ', 'g'),
    type,
    is_active
  FROM function
)
SELECT resource_type, id, name, subtype, active
FROM (
  SELECT * FROM terminal_resources
  UNION ALL
  SELECT * FROM tool_resources
  UNION ALL
  SELECT * FROM skill_resources
  UNION ALL
  SELECT * FROM function_resources
) AS resources
WHERE id IS NOT NULL AND id <> ''
ORDER BY resource_type, id;
SQL
