#!/usr/bin/env bash
set -Eeuo pipefail

web_container=${LIVE_WEBUI_CONTAINER:-open-webui}
runtime_container=${LIVE_RUNTIME_CONTAINER:-openwebui-agentscope-runtime}
isolated_runtime_container=${ISOLATED_RUNTIME_CONTAINER:-openwebui-pr7-agentscope-runtime}
db_container=${DB_CONTAINER:-openwebui-db}
since=${HOTPATCH_LOG_SINCE:-2026-07-28T04:59:00Z}
expected_container_id=${EXPECTED_CONTAINER_ID:-ae1b858332b7bbe252359d46e610a7b595fa6bad36b459187955737cb386e255}
expected_image_id=${EXPECTED_IMAGE_ID:-sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b}
expected_patch_sha=${EXPECTED_PATCH_SHA:-2ce356413ce67047739487fc0833c69c912cef0fb456b2f58bc9bd35b543f156}
target=/app/backend/open_webui/retrieval/vector/dbs/pgvector.py

actual_container_id=$(docker inspect "$web_container" --format '{{.Id}}')
actual_image_id=$(docker inspect "$web_container" --format '{{.Image}}')
health=$(docker inspect "$web_container" --format '{{.State.Health.Status}}')
restart_count=$(docker inspect "$web_container" --format '{{.RestartCount}}')
patch_sha=$(docker exec "$web_container" sha256sum "$target" | awk '{print $1}')

[[ "$actual_container_id" == "$expected_container_id" ]]
[[ "$actual_image_id" == "$expected_image_id" ]]
[[ "$health" == healthy ]]
[[ "$restart_count" == 0 ]]
[[ "$patch_sha" == "$expected_patch_sha" ]]
curl --fail --silent --show-error --max-time 5 http://127.0.0.1/health >/dev/null
curl --fail --silent --show-error --max-time 5 http://127.0.0.1/health/db >/dev/null

echo service_anchors
for container in "$web_container" "$runtime_container" "$isolated_runtime_container" "$db_container"; do
  docker inspect "$container" --format '{{.Name}}|{{.Id}}|{{.Image}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}|{{.RestartCount}}|{{.State.StartedAt}}'
done

echo web_workers
process_table=$(docker top "$web_container" -eo pid,ppid,lstart,args)
printf '%s\n' "$process_table"
master_pid=$(awk '$0 ~ /python3 -m uvicorn/ && $0 ~ /--workers 4/ {print $1; exit}' <<< "$process_table")
worker_count=$(awk -v master="$master_pid" '$2 == master && /multiprocessing.spawn/ {count++} END {print count + 0}' <<< "$process_table")
[[ -n "$master_pid" && "$worker_count" == 4 ]]

echo immutable_config_hashes
config_files_label=$(docker inspect "$web_container" --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}')
[[ -n "$config_files_label" ]]
IFS=',' read -r -a config_files <<< "$config_files_label"
for config_file in "${config_files[@]}"; do
  [[ -f "$config_file" ]]
  sha256sum "$config_file"
done
sha256sum /srv/openwebui-migration/.env
echo "installed_patch_sha256=$patch_sha"

echo web_log_counts
docker logs --since "$since" "$web_container" 2>&1 |
  awk '
    /Child process \[[0-9]+\] died/ {child_death++}
    /Started server process/ {server_start++}
    /Traceback \(most recent call last\)/ {traceback++}
    /runtime_finalization/ && /ReadTimeout/ {runtime_finalization_timeout++}
    /Error during (get|search|query|hybrid_search|has_collection):/ {pgvector_read_error++}
    /Invalidated database connection during (get|search|query|hybrid_search|has_collection); retrying once/ {pgvector_retry_warning++}
    END {
      print "planned_child_deaths=" child_death + 0
      print "server_process_starts=" server_start + 0
      print "tracebacks=" traceback + 0
      print "runtime_finalization_read_timeouts=" runtime_finalization_timeout + 0
      print "pgvector_read_errors=" pgvector_read_error + 0
      print "pgvector_retry_warnings=" pgvector_retry_warning + 0
    }
  '

echo runtime_log_counts
docker logs --since "$since" "$runtime_container" 2>&1 |
  awk '
    /Traceback \(most recent call last\)/ {traceback++}
    /ReadTimeout/ {read_timeout++}
    END {
      print "tracebacks=" traceback + 0
      print "read_timeouts=" read_timeout + 0
    }
  '

db_user=
db_name=
while IFS= read -r entry; do
  case "$entry" in
    POSTGRES_USER=*) db_user=${entry#*=} ;;
    POSTGRES_DB=*) db_name=${entry#*=} ;;
  esac
done < <(docker inspect "$db_container" --format '{{range .Config.Env}}{{println .}}{{end}}')

echo database_connections
docker exec -i "$db_container" psql \
  -X \
  -v ON_ERROR_STOP=1 \
  -U "$db_user" \
  -d "$db_name" \
  -At <<'SQL'
SELECT
  count(*)::text || '|' ||
  count(*) FILTER (WHERE state = 'active')::text || '|' ||
  count(*) FILTER (WHERE state = 'idle')::text || '|' ||
  count(*) FILTER (WHERE wait_event IS NOT NULL)::text
FROM pg_stat_activity
WHERE datname = current_database();
SQL

echo resource_snapshot
docker stats --no-stream "$web_container" "$runtime_container" "$db_container"
