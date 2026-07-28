#!/usr/bin/env bash
set -Eeuo pipefail

container=${TARGET_CONTAINER:?TARGET_CONTAINER is required}
expected_container_id=${EXPECTED_CONTAINER_ID:?EXPECTED_CONTAINER_ID is required}
expected_image_id=${EXPECTED_IMAGE_ID:?EXPECTED_IMAGE_ID is required}
run_dir=${RUN_DIR:?RUN_DIR is required}
health_url=${HEALTH_URL:?HEALTH_URL is required}
base_url=${BASE_URL:?BASE_URL is required}
run_id=${HOTPATCH_RUN_ID:?HOTPATCH_RUN_ID is required}
preserved_file=${PRESERVED_FILE:-}
preserved_sha=${PRESERVED_SHA:-}

ready_probe_host="$run_dir/payload/live-worker-ready-probe.py"
ready_probe_container=/tmp/pr7-announcement-ready-probe.py
rotation_started=$(date -u +%Y-%m-%dT%H:%M:%SZ)

targets=(
  /app/backend/open_webui/config.py
  /app/backend/open_webui/main.py
  /app/backend/open_webui/routers/auths.py
)

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

assert_anchor() {
  [[ "$(docker inspect "$container" --format '{{.Id}}')" == "$expected_container_id" ]]
  [[ "$(docker inspect "$container" --format '{{.Image}}')" == "$expected_image_id" ]]
  [[ "$(docker inspect "$container" --format '{{.State.Health.Status}}')" == healthy ]]
  [[ "$(docker inspect "$container" --format '{{.RestartCount}}')" == 0 ]]
  [[ "$(workers | awk 'END {print NR + 0}')" == 4 ]]
  if [[ -n "$preserved_file" ]]; then
    [[ -n "$preserved_sha" ]]
    [[ "$(docker exec "$container" sha256sum "$preserved_file" | awk '{print $1}')" == "$preserved_sha" ]]
  fi
  curl --fail --silent --show-error --max-time 5 "$health_url" >/dev/null
}

assert_sources() {
  local target relative payload_sha installed_sha
  for target in "${targets[@]}"; do
    relative=${target#/app/}
    payload_sha=$(sha256sum "$run_dir/payload/$relative" | awk '{print $1}')
    installed_sha=$(docker exec "$container" sha256sum "$target" | awk '{print $1}')
    [[ "$installed_sha" == "$payload_sha" ]]
  done
}

terminate_worker() {
  docker exec -i "$container" python - "$1" <<'PY'
import os
import signal
import sys

os.kill(int(sys.argv[1]), signal.SIGTERM)
PY
}

monitor_file="$run_dir/availability.tsv"
monitor_stop="$run_dir/availability.stop"
rm -f "$monitor_file" "$monitor_stop"
availability_monitor() {
  local total=0 failures=0 code
  set +e
  while [[ ! -e "$monitor_stop" ]]; do
    code=$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 3 "$health_url")
    total=$((total + 1))
    [[ "$code" == 200 ]] || failures=$((failures + 1))
    sleep 0.25
  done
  printf 'total\t%s\nfailures\t%s\n' "$total" "$failures" >"$monitor_file"
}

stop_monitor() {
  touch "$monitor_stop"
  if [[ -n ${monitor_pid:-} ]]; then
    wait "$monitor_pid" || true
  fi
}
trap stop_monitor EXIT

[[ -d "$run_dir/backup/build" ]]
[[ -d "$run_dir/build.next" ]]
[[ -f "$ready_probe_host" ]]

master_pid=$(process_rows | awk '/python3 -m uvicorn/ && /--workers 4/ {print $1; exit}')
[[ -n "$master_pid" ]]
mapfile -t original_workers < <(workers)
[[ ${#original_workers[@]} == 4 ]]
assert_anchor

for target in "${targets[@]}"; do
  relative=${target#/app/}
  backup_sha=$(sha256sum "$run_dir/backup/$relative" | awk '{print $1}')
  installed_sha=$(docker exec "$container" sha256sum "$target" | awk '{print $1}')
  [[ "$installed_sha" == "$backup_sha" ]]
  docker cp "$run_dir/payload/$relative" "$container:$target"
  docker exec "$container" chmod 664 "$target"
  docker exec "$container" chown 0:0 "$target"
done
docker cp "$ready_probe_host" "$container:$ready_probe_container"
docker exec "$container" chmod 644 "$ready_probe_container"
assert_sources

availability_monitor &
monitor_pid=$!

echo "rotation_started=$rotation_started"
echo "master_pid=$master_pid"
echo "original_workers=${original_workers[*]}"

for old_pid in "${original_workers[@]}"; do
  mapfile -t before_workers < <(workers)
  terminate_worker "$old_pid"
  replacement_pid=
  for _ in $(seq 1 180); do
    mapfile -t after_workers < <(workers)
    for candidate_pid in "${after_workers[@]}"; do
      if [[ " ${before_workers[*]} " != *" $candidate_pid "* ]]; then
        replacement_pid=$candidate_pid
        break
      fi
    done
    if [[ ${#after_workers[@]} == 4 && -n "$replacement_pid" ]]; then
      if docker exec "$container" python "$ready_probe_container" "$replacement_pid" >/dev/null 2>&1; then
        assert_anchor
        assert_sources
        break
      fi
    fi
    replacement_pid=
    sleep 2
  done
  [[ -n "$replacement_pid" ]]
  echo "replacement_ready old=$old_pid new=$replacement_pid workers=$(workers | tr '\n' ',')"
done

docker exec "$container" mkdir "/app/build.$run_id"
docker cp "$run_dir/build.next/." "$container:/app/build.$run_id"
docker exec "$container" chown -R 0:0 "/app/build.$run_id"
payload_index_sha=$(sha256sum "$run_dir/build.next/index.html" | awk '{print $1}')
staged_index_sha=$(docker exec "$container" sha256sum "/app/build.$run_id/index.html" | awk '{print $1}')
[[ "$staged_index_sha" == "$payload_index_sha" ]]
[[ ! -e "$run_dir/static-switched" ]]
docker exec "$container" mv /app/build "/app/build.before.$run_id"
docker exec "$container" mv "/app/build.$run_id" /app/build
touch "$run_dir/static-switched"

assert_anchor
assert_sources
installed_index_sha=$(docker exec "$container" sha256sum /app/build/index.html | awk '{print $1}')
served_index_sha=$(curl --fail --silent --show-error --max-time 10 "$base_url/" | sha256sum | awk '{print $1}')
[[ "$installed_index_sha" == "$payload_index_sha" ]]
[[ "$served_index_sha" == "$payload_index_sha" ]]

stop_monitor
trap - EXIT

traceback_count=$(docker logs --since "$rotation_started" "$container" 2>&1 |
  awk '/Traceback \(most recent call last\)/ {count++} END {print count + 0}')
child_death_count=$(docker logs --since "$rotation_started" "$container" 2>&1 |
  awk '/Child process \[[0-9]+\] died/ {count++} END {print count + 0}')

echo "final_workers=$(workers | tr '\n' ',')"
echo "planned_child_replacements=$child_death_count"
echo "traceback_count=$traceback_count"
echo "installed_index_sha256=$installed_index_sha"
cat "$monitor_file"
docker inspect "$container" --format 'container_id={{.Id}} image_id={{.Image}} health={{.State.Health.Status}} restart_count={{.RestartCount}} started_at={{.State.StartedAt}}'
