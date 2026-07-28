#!/usr/bin/env bash
set -Eeuo pipefail

container=open-webui
private_dir=/home/aiserver/staging/pr7-announcement-hotpatch-6ba5c1398/live/private

docker exec "$container" rm -f \
  /tmp/pr7-announcement-admin.id \
  /tmp/pr7-announcement-admin.token \
  /tmp/pr7-announcement-user.id \
  /tmp/pr7-announcement-user.token \
  /tmp/issue-test-user-token.py

bash /tmp/prepare-live-admin-id.sh
docker cp "$private_dir/admin.id" "$container:/tmp/pr7-announcement-admin.id"
docker cp /tmp/issue-test-user-token.py "$container:/tmp/issue-test-user-token.py"
docker exec \
  -e PYTHONPATH=/app/backend \
  -e PR7_TEST_USER_ID_PATH=/tmp/pr7-announcement-admin.id \
  -e PR7_TEST_TOKEN_PATH=/tmp/pr7-announcement-admin.token \
  "$container" python /tmp/issue-test-user-token.py
docker cp "$container:/tmp/pr7-announcement-admin.token" "$private_dir/admin.token"
chmod 600 "$private_dir/admin.token"
docker exec "$container" rm -f \
  /tmp/pr7-announcement-admin.id \
  /tmp/pr7-announcement-admin.token \
  /tmp/issue-test-user-token.py \
  /tmp/pr7-announcement-user.token

echo 'live_browser_tokens_prepared=true'
