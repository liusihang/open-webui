#!/usr/bin/env bash
set -Eeuo pipefail

container=open-webui
isolated_private=/home/aiserver/staging/pr7-announcement-hotpatch-6ba5c1398/isolated/private
live_private=/home/aiserver/staging/pr7-announcement-hotpatch-6ba5c1398/live/private
mode=${1:-before-publish}

case "$mode" in
  before-publish)
    token_path=$live_private/admin.token
    expected_path=$isolated_private/admin-config.before-e2e.json
    ;;
  after-publish)
    token_path=$live_private/admin.token
    expected_path=$live_private/admin-config.after-publish.json
    ;;
  *)
    echo 'usage: run-live-announcement-worker-probe.sh before-publish|after-publish' >&2
    exit 2
    ;;
esac

docker exec "$container" rm -f \
  /tmp/pr7-announcement-admin.token \
  /tmp/pr7-announcement-expected-config.json \
  /tmp/announcement-four-worker-probe.py
docker cp "$token_path" "$container:/tmp/pr7-announcement-admin.token"
docker cp "$expected_path" "$container:/tmp/pr7-announcement-expected-config.json"
docker cp /tmp/announcement-four-worker-probe.py "$container:/tmp/announcement-four-worker-probe.py"
host_token_sha=$(sha256sum "$token_path" | awk '{print $1}')
container_token_sha=$(docker exec "$container" sha256sum /tmp/pr7-announcement-admin.token | awk '{print $1}')
[[ "$host_token_sha" == "$container_token_sha" ]]
echo 'container_token_copy_verified=true'

cleanup() {
  docker exec "$container" rm -f \
    /tmp/pr7-announcement-admin.token \
    /tmp/pr7-announcement-expected-config.json \
    /tmp/announcement-four-worker-probe.py
}
trap cleanup EXIT

docker exec "$container" python /tmp/announcement-four-worker-probe.py
