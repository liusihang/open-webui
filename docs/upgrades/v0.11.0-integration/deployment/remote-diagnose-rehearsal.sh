#!/usr/bin/env bash
set -euo pipefail

DB_CONTAINER='openwebui-pr7-db'
DB_USER='webui_pr7'
DATABASE='webui_pr7_v011_rehearsal_4d3543438b'
STATE_DIR='/home/aiserver/staging/openwebui-pr7-eea11194ed-test/deploy-v011-4d3543438b'

docker exec "${DB_CONTAINER}" psql \
  --username "${DB_USER}" \
  --dbname "${DATABASE}" \
  --tuples-only \
  --no-align \
  --set ON_ERROR_STOP=1 \
  --command 'SELECT version_num FROM alembic_version ORDER BY version_num;'

docker exec "${DB_CONTAINER}" psql \
  --username "${DB_USER}" \
  --dbname "${DATABASE}" \
  --tuples-only \
  --no-align \
  --set ON_ERROR_STOP=1 \
  --command "SELECT table_name || '.' || column_name FROM information_schema.columns WHERE table_schema = 'public' AND (table_name, column_name) IN (('chat_message', 'meta'), ('chat', 'current_message_id'), ('chat', 'variables'), ('user', 'variables'), ('automation', 'folder_id')) ORDER BY table_name, column_name;"

docker exec "${DB_CONTAINER}" psql \
  --username "${DB_USER}" \
  --dbname "${DATABASE}" \
  --tuples-only \
  --no-align \
  --set ON_ERROR_STOP=1 \
  --command "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND indexname IN ('ix_memory_id_user_id', 'ix_automation_user_folder', 'uq_user_email_lower') ORDER BY indexname;"

tail -n 80 "${STATE_DIR}/rehearsal-downgrade.log"
