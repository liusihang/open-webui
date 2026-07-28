#!/usr/bin/env bash
set -Eeuo pipefail

container=${LIVE_WEBUI_CONTAINER:-open-webui}
expected_container_id=${EXPECTED_CONTAINER_ID:-ae1b858332b7bbe252359d46e610a7b595fa6bad36b459187955737cb386e255}
expected_image_id=${EXPECTED_IMAGE_ID:-sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b}
expected_patch_sha=${EXPECTED_PATCH_SHA:?EXPECTED_PATCH_SHA is required}
expected_ready_probe_sha=${EXPECTED_READY_PROBE_SHA:?EXPECTED_READY_PROBE_SHA is required}
target=/app/backend/open_webui/retrieval/vector/dbs/pgvector.py
ready_probe=/tmp/pr7-live-worker-ready-probe.py
health_url=${HEALTH_URL:-http://127.0.0.1/health}
verify_only=${VERIFY_ONLY:-false}
workers_to_replace_csv=${WORKERS_TO_REPLACE_CSV:-}

process_rows() {
  docker exec -i "$container" python - <<'PY'
from pathlib import Path

for process_dir in Path('/proc').iterdir():
    if not process_dir.name.isdigit():
        continue
    try:
        status = (process_dir / 'status').read_text()
        ppid = next(
            line.split(':', 1)[1].strip()
            for line in status.splitlines()
            if line.startswith('PPid:')
        )
        command = (process_dir / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace').strip()
    except (FileNotFoundError, PermissionError, ProcessLookupError, StopIteration):
        continue
    print(f'{process_dir.name}\t{ppid}\t{command}')
PY
}

workers() {
  process_rows |
    awk -v master="$master_pid" '$2 == master && /multiprocessing.spawn/ {print $1}' |
    sort -n
}

worker_count() {
  workers | awk 'END {print NR + 0}'
}

terminate_worker() {
  local worker_pid=$1
  docker exec -i "$container" python - "$worker_pid" <<'PY'
import os
import signal
import sys

os.kill(int(sys.argv[1]), signal.SIGTERM)
PY
}

ready_count() {
  docker logs --since "$rotation_started" "$container" 2>&1 |
    awk '/Application startup complete/ {count++} END {print count + 0}'
}

assert_anchor() {
  local actual_container_id actual_image_id health restart_count installed_sha ready_probe_sha
  actual_container_id=$(docker inspect "$container" --format '{{.Id}}')
  actual_image_id=$(docker inspect "$container" --format '{{.Image}}')
  health=$(docker inspect "$container" --format '{{.State.Health.Status}}')
  restart_count=$(docker inspect "$container" --format '{{.RestartCount}}')
  installed_sha=$(docker exec "$container" sha256sum "$target" | awk '{print $1}')
  ready_probe_sha=$(docker exec "$container" sha256sum "$ready_probe" | awk '{print $1}')
  [[ "$actual_container_id" == "$expected_container_id" ]] || return 1
  [[ "$actual_image_id" == "$expected_image_id" ]] || return 1
  [[ "$health" == healthy ]] || return 1
  [[ "$restart_count" == 0 ]] || return 1
  [[ "$installed_sha" == "$expected_patch_sha" ]] || return 1
  [[ "$ready_probe_sha" == "$expected_ready_probe_sha" ]] || return 1
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

master_pid=$(process_rows | awk '/python3 -m uvicorn/ && /--workers 4/ {print $1; exit}')
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
if [[ -n "$workers_to_replace_csv" ]]; then
  IFS=',' read -r -a rotation_workers <<< "$workers_to_replace_csv"
else
  rotation_workers=("${original_workers[@]}")
fi
for requested_pid in "${rotation_workers[@]}"; do
  [[ " ${original_workers[*]} " == *" $requested_pid "* ]] || {
    echo "requested_worker_missing=$requested_pid"
    exit 1
  }
done

if ! assert_anchor; then
  echo initial_anchor_failed
  echo "master_pid=$master_pid"
  echo "workers=$(workers | tr '\n' ',')"
  docker inspect "$container" --format 'container_id={{.Id}} image_id={{.Image}} health={{.State.Health.Status}} restart_count={{.RestartCount}} started_at={{.State.StartedAt}}'
  docker exec "$container" sha256sum "$target"
  exit 1
fi
if [[ "$verify_only" == true ]]; then
  echo anchor_verified
  echo "master_pid=$master_pid"
  echo "workers=${original_workers[*]}"
  exit 0
fi
availability_monitor &
monitor_pid=$!

echo "rotation_started=$rotation_started"
echo "master_pid=$master_pid"
echo "original_workers=${original_workers[*]}"
echo "rotation_workers=${rotation_workers[*]}"

for old_pid in "${rotation_workers[@]}"; do
  mapfile -t before_workers < <(workers)
  current_workers=" ${before_workers[*]} "
  [[ "$current_workers" == *" $old_pid "* ]] || {
    echo "expected_old_worker_missing=$old_pid"
    exit 1
  }

  echo "replacing_worker=$old_pid"
  terminate_worker "$old_pid"

  replacement_ready=false
  for _ in $(seq 1 240); do
    mapfile -t after_workers < <(workers)
    current_workers=" ${after_workers[*]} "
    replacement_pid=
    for candidate_pid in "${after_workers[@]}"; do
      if [[ " ${before_workers[*]} " != *" $candidate_pid "* ]]; then
        replacement_pid=$candidate_pid
        break
      fi
    done
    if [[ ${#after_workers[@]} == 4 && "$current_workers" != *" $old_pid "* && -n "$replacement_pid" ]]; then
      if probe_output=$(docker exec "$container" python "$ready_probe" "$replacement_pid" 2>&1) && assert_anchor; then
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
  echo "replacement_ready_for=$old_pid replacement_pid=$replacement_pid workers=$(workers | tr '\n' ',')"
  echo "targeted_probe=$probe_output"
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
