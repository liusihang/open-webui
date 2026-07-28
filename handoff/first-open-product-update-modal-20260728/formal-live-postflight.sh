#!/usr/bin/env bash
set -Eeuo pipefail

container=open-webui
runtime_container=openwebui-agentscope-runtime
expected_container_id=ae1b858332b7bbe252359d46e610a7b595fa6bad36b459187955737cb386e255
expected_image_id=sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b
expected_started_at=2026-07-28T03:19:21.700659218Z
expected_index_sha=8f1164312bfb8d98258e103bba00aaf7acf963682cc0865c53978df35ef73f14
expected_pgvector_sha=2ce356413ce67047739487fc0833c69c912cef0fb456b2f58bc9bd35b543f156
run_dir=/home/aiserver/staging/pr7-announcement-hotpatch-6ba5c1398/live

actual_container_id=$(docker inspect "$container" --format '{{.Id}}')
actual_image_id=$(docker inspect "$container" --format '{{.Image}}')
actual_started_at=$(docker inspect "$container" --format '{{.State.StartedAt}}')
actual_health=$(docker inspect "$container" --format '{{.State.Health.Status}}')
actual_restart_count=$(docker inspect "$container" --format '{{.RestartCount}}')
actual_index_sha=$(docker exec "$container" sha256sum /app/build/index.html | awk '{print $1}')
actual_pgvector_sha=$(docker exec "$container" sha256sum /app/backend/open_webui/retrieval/vector/dbs/pgvector.py | awk '{print $1}')

[[ "$actual_container_id" == "$expected_container_id" ]]
[[ "$actual_image_id" == "$expected_image_id" ]]
[[ "$actual_started_at" == "$expected_started_at" ]]
[[ "$actual_health" == healthy ]]
[[ "$actual_restart_count" == 0 ]]
[[ "$actual_index_sha" == "$expected_index_sha" ]]
[[ "$actual_pgvector_sha" == "$expected_pgvector_sha" ]]

for relative in \
  backend/open_webui/config.py \
  backend/open_webui/main.py \
  backend/open_webui/routers/auths.py; do
  expected_sha=$(sha256sum "$run_dir/payload/$relative" | awk '{print $1}')
  actual_sha=$(docker exec "$container" sha256sum "/app/$relative" | awk '{print $1}')
  [[ "$actual_sha" == "$expected_sha" ]]
done

mapfile -t worker_pids < <(
  docker exec -i "$container" python - <<'PY'
from pathlib import Path

for process_dir in Path('/proc').glob('[0-9]*'):
    try:
        command = (process_dir / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
    except OSError:
        continue
    if 'multiprocessing.spawn' in command and 'spawn_main' in command:
        print(process_dir.name)
PY
)
[[ ${#worker_pids[@]} == 4 ]]
for worker_pid in "${worker_pids[@]}"; do
  docker exec "$container" python /tmp/pr7-announcement-ready-probe.py "$worker_pid" >/dev/null
done
printf 'ready_worker_pids=%s\n' "${worker_pids[*]}"

docker inspect "$container" --format 'webui container_id={{.Id}} image_id={{.Image}} health={{.State.Health.Status}} restart_count={{.RestartCount}} started_at={{.State.StartedAt}}'
docker inspect "$runtime_container" --format 'runtime container_id={{.Id}} image_id={{.Image}} health={{.State.Health.Status}} restart_count={{.RestartCount}} started_at={{.State.StartedAt}}'
printf 'index_sha256=%s\n' "$actual_index_sha"
printf 'pgvector_sha256=%s\n' "$actual_pgvector_sha"
docker exec "$container" stat -c 'source_owner=%U:%G path=%n' \
  /app/backend/open_webui/config.py \
  /app/backend/open_webui/main.py \
  /app/backend/open_webui/routers/auths.py
docker exec "$container" stat -c 'build_owner=%U:%G path=%n' /app/build /app/build/index.html
