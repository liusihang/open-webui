#!/usr/bin/env bash
set -Eeuo pipefail

container=${TARGET_CONTAINER:?TARGET_CONTAINER is required}
expected_container_id=${EXPECTED_CONTAINER_ID:?EXPECTED_CONTAINER_ID is required}
expected_image_id=${EXPECTED_IMAGE_ID:?EXPECTED_IMAGE_ID is required}
run_dir=${RUN_DIR:?RUN_DIR is required}
preserved_file=${PRESERVED_FILE:-}
preserved_sha=${PRESERVED_SHA:-}

actual_container_id=$(docker inspect "$container" --format '{{.Id}}')
actual_image_id=$(docker inspect "$container" --format '{{.Image}}')
health=$(docker inspect "$container" --format '{{.State.Health.Status}}')
restart_count=$(docker inspect "$container" --format '{{.RestartCount}}')

[[ "$actual_container_id" == "$expected_container_id" ]]
[[ "$actual_image_id" == "$expected_image_id" ]]
[[ "$health" == healthy ]]
[[ "$restart_count" == 0 ]]
[[ ! -e "$run_dir" ]]
if [[ -n "$preserved_file" ]]; then
  [[ -n "$preserved_sha" ]]
  [[ "$(docker exec "$container" sha256sum "$preserved_file" | awk '{print $1}')" == "$preserved_sha" ]]
fi

umask 077
mkdir -p "$run_dir/backup/backend/open_webui/routers"
mkdir -p "$run_dir/payload/backend/open_webui/routers"
mkdir -p "$run_dir/payload"

docker cp "$container:/app/build" "$run_dir/backup/build"
cp -a "$run_dir/backup/build" "$run_dir/build.next"

docker cp "$container:/app/backend/open_webui/config.py" "$run_dir/backup/backend/open_webui/config.py"
docker cp "$container:/app/backend/open_webui/main.py" "$run_dir/backup/backend/open_webui/main.py"
docker cp "$container:/app/backend/open_webui/routers/auths.py" "$run_dir/backup/backend/open_webui/routers/auths.py"

cat >"$run_dir/anchor.env" <<EOF
container=$container
container_id=$actual_container_id
image_id=$actual_image_id
health=$health
restart_count=$restart_count
started_at=$(docker inspect "$container" --format '{{.State.StartedAt}}')
EOF
chmod 600 "$run_dir/anchor.env"

sha256sum \
  "$run_dir/backup/build/index.html" \
  "$run_dir/backup/backend/open_webui/config.py" \
  "$run_dir/backup/backend/open_webui/main.py" \
  "$run_dir/backup/backend/open_webui/routers/auths.py"

echo "prepared_run_dir=$run_dir"
cat "$run_dir/anchor.env"
