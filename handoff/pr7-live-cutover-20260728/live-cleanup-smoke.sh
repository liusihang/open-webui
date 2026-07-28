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
admin_id=$(docker exec -i "${DB_CONTAINER}" psql -X -v ON_ERROR_STOP=1 -U "${db_user}" -d "${db_name}" -Atc "SELECT id FROM \"user\" WHERE role='admin' ORDER BY created_at LIMIT 1;")
if [[ -z "${admin_id}" ]]; then
  echo cleanup_admin_missing
  exit 1
fi

docker exec -i "${DB_CONTAINER}" psql \
  -X \
  -v ON_ERROR_STOP=1 \
  -v admin_id="${admin_id}" \
  -U "${db_user}" \
  -d "${db_name}" <<'SQL'
BEGIN;
CREATE TEMP TABLE target_admin (id text PRIMARY KEY);
INSERT INTO target_admin (id) VALUES (:'admin_id');
CREATE TEMP TABLE target_run (id text PRIMARY KEY);
INSERT INTO target_run (id) VALUES
  ('b735d9a3-62aa-435e-9298-9149f45056cb'),
  ('4c67df8d-64f2-4f2b-80b3-15c4b46afa1e'),
  ('d0b2f460-3936-4bd2-83b9-66a19e62413d'),
  ('af199c1d-fdf0-470c-9430-f784f9c9566f'),
  ('17d6246d-0409-4ac8-8e5a-ae7c981df202'),
  ('eda9c4c3-642e-46d5-bc53-9caf9961f745');

DO $$
DECLARE
  matched integer;
  foreign_owned integer;
BEGIN
  SELECT count(*) INTO matched FROM agent_run WHERE id IN (SELECT id FROM target_run);
  SELECT count(*) INTO foreign_owned
  FROM agent_run
  WHERE id IN (SELECT id FROM target_run) AND user_id <> (SELECT id FROM target_admin);
  IF matched <> 6 THEN
    RAISE EXCEPTION 'expected 6 exact smoke runs, observed %', matched;
  END IF;
  IF foreign_owned <> 0 THEN
    RAISE EXCEPTION 'smoke run ownership mismatch';
  END IF;
END $$;

CREATE TEMP TABLE target_chat (id text PRIMARY KEY);
INSERT INTO target_chat (id)
SELECT DISTINCT chat_id FROM agent_run WHERE id IN (SELECT id FROM target_run);
INSERT INTO target_chat (id) VALUES ('fc2746fc-f43b-481d-ade4-6bf21dc6b4f3')
ON CONFLICT (id) DO NOTHING;

DELETE FROM agent_run_decision_execution WHERE run_id IN (SELECT id FROM target_run);
DELETE FROM agent_run_operation WHERE run_id IN (SELECT id FROM target_run);
DELETE FROM agent_artifact WHERE run_id IN (SELECT id FROM target_run);
DELETE FROM agent_run_event WHERE run_id IN (SELECT id FROM target_run);
DELETE FROM agent_run WHERE id IN (SELECT id FROM target_run);
DELETE FROM conversation_mode_profile_temporary_binding
WHERE user_id = :'admin_id'
  AND (
    temporary_conversation_id IN (SELECT id FROM target_chat)
    OR temporary_conversation_id LIKE 'local:pr7-latest-agent-%'
    OR temporary_conversation_id LIKE 'local:pr7-live-cancel-%'
  );
DELETE FROM chat_message WHERE chat_id IN (SELECT id FROM target_chat);
DELETE FROM chat WHERE id IN (SELECT id FROM target_chat) AND user_id = :'admin_id';
COMMIT;

SELECT
  (SELECT count(*) FROM agent_run WHERE id IN (SELECT id FROM target_run))::text || ':' ||
  (SELECT count(*) FROM conversation_mode_profile_temporary_binding
    WHERE user_id = :'admin_id'
      AND (
        temporary_conversation_id IN (SELECT id FROM target_chat)
        OR temporary_conversation_id LIKE 'local:pr7-latest-agent-%'
        OR temporary_conversation_id LIKE 'local:pr7-live-cancel-%'
      ))::text || ':' ||
  (SELECT count(*) FROM chat WHERE id IN (SELECT id FROM target_chat))::text || ':' ||
  (SELECT count(*) FROM tool WHERE id LIKE 'pr7_interaction_gate_%')::text;
SQL
