#!/usr/bin/env bash
set -Eeuo pipefail

web_container=${LIVE_WEBUI_CONTAINER:-open-webui}
runtime_container=${LIVE_RUNTIME_CONTAINER:-openwebui-agentscope-runtime}
expected_container_id=${EXPECTED_CONTAINER_ID:-ae1b858332b7bbe252359d46e610a7b595fa6bad36b459187955737cb386e255}
expected_image_id=${EXPECTED_IMAGE_ID:-sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b}
expected_patch_sha=${EXPECTED_PATCH_SHA:-2ce356413ce67047739487fc0833c69c912cef0fb456b2f58bc9bd35b543f156}
target=/app/backend/open_webui/retrieval/vector/dbs/pgvector.py
samples=${OBSERVATION_SAMPLES:-13}
interval=${OBSERVATION_INTERVAL_SECONDS:-30}

worker_pids() {
  local process_table master_pid
  process_table=$(docker top "$web_container" -eo pid,ppid,args)
  master_pid=$(awk '$0 ~ /python3 -m uvicorn/ && $0 ~ /--workers 4/ {print $1; exit}' <<< "$process_table")
  awk -v master="$master_pid" '$2 == master && /multiprocessing.spawn/ {print $1}' <<< "$process_table" |
    sort -n |
    tr '\n' ','
}

baseline_workers=$(worker_pids)
[[ $(tr ',' '\n' <<< "$baseline_workers" | awk 'NF {count++} END {print count + 0}') == 4 ]]

for sample in $(seq 1 "$samples"); do
  actual_container_id=$(docker inspect "$web_container" --format '{{.Id}}')
  actual_image_id=$(docker inspect "$web_container" --format '{{.Image}}')
  health=$(docker inspect "$web_container" --format '{{.State.Health.Status}}')
  restart_count=$(docker inspect "$web_container" --format '{{.RestartCount}}')
  patch_sha=$(docker exec "$web_container" sha256sum "$target" | awk '{print $1}')
  current_workers=$(worker_pids)
  runtime_health=$(docker inspect "$runtime_container" --format '{{.State.Health.Status}}')
  runtime_restarts=$(docker inspect "$runtime_container" --format '{{.RestartCount}}')

  [[ "$actual_container_id" == "$expected_container_id" ]]
  [[ "$actual_image_id" == "$expected_image_id" ]]
  [[ "$health" == healthy ]]
  [[ "$restart_count" == 0 ]]
  [[ "$patch_sha" == "$expected_patch_sha" ]]
  [[ "$current_workers" == "$baseline_workers" ]]
  [[ "$runtime_health" == healthy ]]
  [[ "$runtime_restarts" == 0 ]]
  curl --fail --silent --show-error --max-time 5 http://127.0.0.1/health >/dev/null
  curl --fail --silent --show-error --max-time 5 http://127.0.0.1/health/db >/dev/null

  printf 'sample=%s timestamp=%s workers=%s health=%s restarts=%s runtime_health=%s runtime_restarts=%s\n' \
    "$sample" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$current_workers" "$health" "$restart_count" "$runtime_health" "$runtime_restarts"
  if [[ "$sample" != "$samples" ]]; then
    sleep "$interval"
  fi
done

echo "observation_samples=$samples"
echo "observation_interval_seconds=$interval"
echo "stable_workers=$baseline_workers"
