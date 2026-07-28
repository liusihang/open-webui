#!/usr/bin/env bash
set -Eeuo pipefail

container=${LIVE_WEBUI_CONTAINER:-open-webui}
expected_container_id=${EXPECTED_CONTAINER_ID:-ae1b858332b7bbe252359d46e610a7b595fa6bad36b459187955737cb386e255}
expected_image_id=${EXPECTED_IMAGE_ID:-sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b}
expected_patch_sha=${EXPECTED_PATCH_SHA:?EXPECTED_PATCH_SHA is required}
target=/app/backend/open_webui/retrieval/vector/dbs/pgvector.py
health_url=${HEALTH_URL:-http://127.0.0.1/health}

workers() {
  docker exec "$container" ps -eo pid=,ppid=,args= |
    awk -v master="$master_pid" '$2 == master && /multiprocessing.spawn/ {print $1}' |
    sort -n
}

worker_count() {
  workers | awk 'END {print NR + 0}'
}

ready_count() {
  docker logs --since "$rotation_started" "$container" 2>&1 |
    awk '/Application startup complete/ {count++} END {print count + 0}'
}

assert_anchor() {
  local actual_container_id actual_image_id health restart_count installed_sha
  actual_container_id=$(docker inspect "$container" --format '{{.Id}}')
  actual_image_id=$(docker inspect "$container" --format '{{.Image}}')
  health=$(docker inspect "$container" --format '{{.State.Health.Status}}')
  restart_count=$(docker inspect "$container" --format '{{.RestartCount}}')
  installed_sha=$(docker exec "$container" sha256sum "$target" | awk '{print $1}')
  [[ "$actual_container_id" == "$expected_container_id" ]] || return 1
  [[ "$actual_image_id" == "$expected_image_id" ]] || return 1
  [[ "$health" == healthy ]] || return 1
  [[ "$restart_count" == 0 ]] || return 1
  [[ "$installed_sha" == "$expected_patch_sha" ]] || return 1
  [[ $(worker_count) == 4 ]] || return 1
  curl --fail --silent --show-error --max-time 5 "$health_url" >/dev/null || return 1
}

monitor_file=$(mktemp)
monitor_stop=${monitor_file}.stop
availability_monitor() {
  local total=0 failures=0 code
  set +e
  while [[ ! -e "$monitor_stop" ]]; do
    code=$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 3 "$health_url")
    total=$((total + 1))
    [[ "$code" == 200 ]] || failures=$((failures + 1))
    sleep 0.25
  done
  printf 'availability_total=%s\navailability_failures=%s\n' "$total" "$failures" > "$monitor_file"
}

stop_monitor() {
  touch "$monitor_stop"
  if [[ -n ${monitor_pid:-} ]]; then
    wait "$monitor_pid" || true
  fi
}
trap stop_monitor EXIT

actual_container_id=$(docker inspect "$container" --format '{{.Id}}')
actual_image_id=$(docker inspect "$container" --format '{{.Image}}')
[[ "$actual_container_id" == "$expected_container_id" ]] || {
  echo container_id_mismatch
  exit 1
}
[[ "$actual_image_id" == "$expected_image_id" ]] || {
  echo image_id_mismatch
  exit 1
}

master_pid=$(docker exec "$container" ps -eo pid=,args= |
  awk '/python3 -m uvicorn/ && /--workers 4/ {print $1; exit}')
[[ -n "$master_pid" ]] || {
  echo uvicorn_master_missing
  exit 1
}

rotation_started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
mapfile -t original_workers < <(workers)
[[ ${#original_workers[@]} == 4 ]] || {
  echo original_worker_count_not_four
  exit 1
}

assert_anchor
availability_monitor &
monitor_pid=$!

echo "rotation_started=$rotation_started"
echo "master_pid=$master_pid"
echo "original_workers=${original_workers[*]}"

expected_ready=0
for old_pid in "${original_workers[@]}"; do
  current_workers=" $(workers | tr '\n' ' ')"
  [[ "$current_workers" == *" $old_pid "* ]] || {
    echo "expected_old_worker_missing=$old_pid"
    exit 1
  }

  echo "replacing_worker=$old_pid"
  docker exec "$container" kill -TERM "$old_pid"
  expected_ready=$((expected_ready + 1))

  replacement_ready=false
  for _ in $(seq 1 240); do
    current_workers=" $(workers | tr '\n' ' ')"
    if [[ $(worker_count) == 4 && "$current_workers" != *" $old_pid "* && $(ready_count) -ge $expected_ready ]]; then
      if assert_anchor; then
        replacement_ready=true
        break
      fi
    fi
    sleep 2
  done
  [[ "$replacement_ready" == true ]] || {
    echo "replacement_not_ready_for=$old_pid"
    exit 1
  }
  echo "replacement_ready_for=$old_pid workers=$(workers | tr '\n' ',')"
done

sleep 5
assert_anchor
stop_monitor
trap - EXIT

final_workers=$(workers | tr '\n' ',')
child_death_count=$(docker logs --since "$rotation_started" "$container" 2>&1 |
  awk '/Child process \[[0-9]+\] died/ {count++} END {print count + 0}')
traceback_count=$(docker logs --since "$rotation_started" "$container" 2>&1 |
  awk '/Traceback \(most recent call last\)/ {count++} END {print count + 0}')

echo "final_workers=$final_workers"
echo "ready_count=$(ready_count)"
echo "planned_child_replacements=$child_death_count"
echo "traceback_count=$traceback_count"
cat "$monitor_file"
docker inspect "$container" --format 'container_id={{.Id}} image_id={{.Image}} health={{.State.Health.Status}} restart_count={{.RestartCount}} started_at={{.State.StartedAt}}'
