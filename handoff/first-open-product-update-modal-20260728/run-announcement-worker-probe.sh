#!/usr/bin/env bash
set -Eeuo pipefail

container=open-webui-pr7
private_dir=/home/aiserver/staging/pr7-announcement-hotpatch-6ba5c1398/isolated/private

docker cp "$private_dir/admin.token" "$container:/tmp/pr7-announcement-admin.token"
docker cp "$private_dir/admin-config.before-e2e.json" "$container:/tmp/pr7-announcement-expected-config.json"
docker cp /tmp/announcement-four-worker-probe.py "$container:/tmp/announcement-four-worker-probe.py"
docker exec "$container" python /tmp/announcement-four-worker-probe.py
docker exec "$container" rm -f \
  /tmp/pr7-announcement-admin.token \
  /tmp/pr7-announcement-expected-config.json \
  /tmp/announcement-four-worker-probe.py
