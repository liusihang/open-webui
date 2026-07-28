#!/usr/bin/env bash
set -Eeuo pipefail

container=${TARGET_CONTAINER:?TARGET_CONTAINER is required}
expected_container_id=${EXPECTED_CONTAINER_ID:?EXPECTED_CONTAINER_ID is required}
expected_image_id=${EXPECTED_IMAGE_ID:?EXPECTED_IMAGE_ID is required}
run_dir=${RUN_DIR:?RUN_DIR is required}
health_url=${HEALTH_URL:?HEALTH_URL is required}
run_id=${HOTPATCH_RUN_ID:?HOTPATCH_RUN_ID is required}

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

[[ "$(docker inspect "$container" --format '{{.Id}}')" == "$expected_container_id" ]]
[[ "$(docker inspect "$container" --format '{{.Image}}')" == "$expected_image_id" ]]
[[ "$(docker inspect "$container" --format '{{.RestartCount}}')" == 0 ]]

docker cp "$run_dir/backup/backend/open_webui/config.py" "$container:/app/backend/open_webui/config.py"
docker cp "$run_dir/backup/backend/open_webui/main.py" "$container:/app/backend/open_webui/main.py"
docker cp "$run_dir/backup/backend/open_webui/routers/auths.py" "$container:/app/backend/open_webui/routers/auths.py"
docker exec "$container" chown 0:0 \
  /app/backend/open_webui/config.py \
  /app/backend/open_webui/main.py \
  /app/backend/open_webui/routers/auths.py

if [[ -e "$run_dir/static-switched" ]]; then
  docker exec "$container" mv /app/build "/app/build.failed.$run_id"
  docker exec "$container" mv "/app/build.before.$run_id" /app/build
fi

master_pid=$(process_rows | awk '/python3 -m uvicorn/ && /--workers 4/ {print $1; exit}')
mapfile -t workers < <(process_rows |
  awk -v master="$master_pid" '$2 == master && /multiprocessing.spawn/ {print $1}' |
  sort -n)
[[ ${#workers[@]} == 4 ]]

for pid in "${workers[@]}"; do
  docker exec -i "$container" python - "$pid" <<'PY'
import os
import signal
import sys

os.kill(int(sys.argv[1]), signal.SIGTERM)
PY
  for _ in $(seq 1 120); do
    count=$(process_rows | awk -v master="$master_pid" '$2 == master && /multiprocessing.spawn/ {count++} END {print count + 0}')
    if [[ "$count" == 4 ]] && curl --fail --silent --show-error --max-time 5 "$health_url" >/dev/null; then
      break
    fi
    sleep 2
  done
done

[[ "$(docker inspect "$container" --format '{{.State.Health.Status}}')" == healthy ]]
[[ "$(docker inspect "$container" --format '{{.RestartCount}}')" == 0 ]]
echo "rollback_complete=$run_id"
docker inspect "$container" --format 'container_id={{.Id}} image_id={{.Image}} health={{.State.Health.Status}} restart_count={{.RestartCount}} started_at={{.State.StartedAt}}'
