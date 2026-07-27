#!/usr/bin/env bash
set -Eeuo pipefail

DB_CONTAINER=${DB_CONTAINER:?DB_CONTAINER is required}
DB_USER=${DB_USER:-prep_user}
DB_NAME=${DB_NAME:-prep_db}

docker exec -i "${DB_CONTAINER}" psql -X -v ON_ERROR_STOP=1 -U "${DB_USER}" -d "${DB_NAME}" -At -F $'\t' <<'SQL'
SELECT
  p.pid,
  p.phase,
  p.blocks_done,
  p.blocks_total,
  p.tuples_done,
  p.tuples_total,
  c.relname AS index_name,
  t.relname AS table_name
FROM pg_stat_progress_create_index AS p
LEFT JOIN pg_class AS c ON c.oid = p.index_relid
LEFT JOIN pg_class AS t ON t.oid = p.relid;
SQL
