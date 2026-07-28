#!/usr/bin/env bash
set -Eeuo pipefail

container=${LIVE_WEBUI_CONTAINER:-open-webui}
expected_container_id=${EXPECTED_CONTAINER_ID:-ae1b858332b7bbe252359d46e610a7b595fa6bad36b459187955737cb386e255}
expected_image_id=${EXPECTED_IMAGE_ID:-sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b}
expected_original_sha=${EXPECTED_ORIGINAL_SHA:-c5520a79cb881bfd3559fe2bf545f2302dfb3a9749fe68e4ddd82a58d2767a8c}
patch_file=${PATCH_FILE:-/tmp/pr7-pgvector-hotpatch.py}
target=/app/backend/open_webui/retrieval/vector/dbs/pgvector.py
hotpatch_root=/home/aiserver/staging/pr7-live-prep-20260727/hotpatches
run_id=${HOTPATCH_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}
run_dir=${hotpatch_root}/${run_id}

actual_container_id=$(docker inspect "$container" --format '{{.Id}}')
actual_image_id=$(docker inspect "$container" --format '{{.Image}}')
health=$(docker inspect "$container" --format '{{.State.Health.Status}}')
restart_count=$(docker inspect "$container" --format '{{.RestartCount}}')

[[ "$actual_container_id" == "$expected_container_id" ]] || {
  echo container_id_mismatch
  exit 1
}
[[ "$actual_image_id" == "$expected_image_id" ]] || {
  echo image_id_mismatch
  exit 1
}
[[ "$health" == healthy ]] || {
  echo container_not_healthy
  exit 1
}
[[ "$restart_count" == 0 ]] || {
  echo restart_count_not_zero
  exit 1
}
[[ -f "$patch_file" ]] || {
  echo patch_file_missing
  exit 1
}

original_sha=$(docker exec "$container" sha256sum "$target" | awk '{print $1}')
patch_sha=$(sha256sum "$patch_file" | awk '{print $1}')
[[ "$original_sha" == "$expected_original_sha" ]] || {
  echo original_sha_mismatch
  exit 1
}
[[ "$patch_sha" != "$original_sha" ]] || {
  echo patch_does_not_change_target
  exit 1
}

umask 077
mkdir -p "$run_dir"
docker cp "$container:$target" "$run_dir/pgvector.py.before"
backup_sha=$(sha256sum "$run_dir/pgvector.py.before" | awk '{print $1}')
[[ "$backup_sha" == "$original_sha" ]] || {
  echo backup_sha_mismatch
  exit 1
}

docker cp "$patch_file" "$container:$target"
installed_sha=$(docker exec "$container" sha256sum "$target" | awk '{print $1}')
[[ "$installed_sha" == "$patch_sha" ]] || {
  echo installed_sha_mismatch
  exit 1
}

cat > "$run_dir/manifest.env" <<EOF
run_id=$run_id
container_id=$actual_container_id
image_id=$actual_image_id
target=$target
original_sha256=$original_sha
installed_sha256=$installed_sha
backup=$run_dir/pgvector.py.before
container_restart_count=$restart_count
EOF
chmod 600 "$run_dir/manifest.env" "$run_dir/pgvector.py.before"

echo "hotpatch_run_id=$run_id"
echo "original_sha256=$original_sha"
echo "installed_sha256=$installed_sha"
echo "backup=$run_dir/pgvector.py.before"
echo "container_id=$actual_container_id"
echo "image_id=$actual_image_id"
echo "health=$health"
echo "restart_count=$restart_count"
