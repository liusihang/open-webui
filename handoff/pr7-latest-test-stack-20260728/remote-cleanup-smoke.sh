#!/usr/bin/env bash
set -Eeuo pipefail

STACK_DIR=/home/aiserver/staging/openwebui-pr7-eea11194ed-test
EVIDENCE_FILE=${STACK_DIR}/evidence/pr7-latest-test-stack-20260728/acceptance.json
DB_CONTAINER=openwebui-pr7-db
ADMIN_USER_ID=b6826286-1251-4576-b3a0-e109ff085a61
DIAGNOSTIC_CHAT_ID=db80344e-8894-4b09-916e-d71cff9d54af

run_id=$(jq -r '.agent.run_id' "${EVIDENCE_FILE}")
temporary_chat_id=$(jq -r '.agent.chat_id' "${EVIDENCE_FILE}")
[[ "${run_id}" =~ ^[0-9a-f-]{36}$ ]] || { echo invalid_smoke_run_id; exit 1; }
[[ "${temporary_chat_id}" == local:pr7-latest-agent-* ]] || { echo invalid_smoke_chat_id; exit 1; }

db_user=
db_name=
while IFS= read -r entry; do
  case "${entry}" in
    POSTGRES_USER=*) db_user=${entry#*=} ;;
    POSTGRES_DB=*) db_name=${entry#*=} ;;
  esac
done < <(docker inspect "${DB_CONTAINER}" --format '{{range .Config.Env}}{{println .}}{{end}}')

docker exec -i "${DB_CONTAINER}" psql \
  -X \
  -v ON_ERROR_STOP=1 \
  -v run_id="${run_id}" \
  -v admin_user_id="${ADMIN_USER_ID}" \
  -v diagnostic_chat_id="${DIAGNOSTIC_CHAT_ID}" \
  -U "${db_user}" \
  -d "${db_name}" <<'SQL'
BEGIN;
DELETE FROM agent_run_decision_execution WHERE run_id = :'run_id';
DELETE FROM agent_run_operation WHERE run_id = :'run_id';
DELETE FROM agent_artifact WHERE run_id = :'run_id';
DELETE FROM agent_run_event WHERE run_id = :'run_id';
DELETE FROM agent_run WHERE id = :'run_id';
DELETE FROM conversation_mode_profile_temporary_binding
WHERE user_id = :'admin_user_id'
  AND temporary_conversation_id LIKE 'local:pr7-latest-%';
DELETE FROM chat_message WHERE chat_id = :'diagnostic_chat_id';
DELETE FROM chat WHERE id = :'diagnostic_chat_id' AND user_id = :'admin_user_id';
COMMIT;
SELECT
  (SELECT count(*) FROM agent_run WHERE id = :'run_id')::text || ':' ||
  (SELECT count(*) FROM conversation_mode_profile_temporary_binding
    WHERE user_id = :'admin_user_id' AND temporary_conversation_id LIKE 'local:pr7-latest-%')::text || ':' ||
  (SELECT count(*) FROM chat WHERE id = :'diagnostic_chat_id')::text;
SQL
