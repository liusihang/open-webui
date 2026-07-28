#!/usr/bin/env bash
set -Eeuo pipefail

container=open-webui-pr7
private_dir=/home/aiserver/staging/pr7-announcement-hotpatch-6ba5c1398/isolated/private

python3 /tmp/prepare-browser-auth.py
docker cp "$private_dir/user.id" "$container:/tmp/pr7-announcement-user.id"
docker cp /tmp/issue-test-user-token.py "$container:/tmp/issue-test-user-token.py"
docker exec -e PYTHONPATH=/app/backend "$container" python /tmp/issue-test-user-token.py
docker cp "$container:/tmp/pr7-announcement-user.token" "$private_dir/user.token"
chmod 600 "$private_dir/user.token"
docker exec "$container" rm -f \
  /tmp/pr7-announcement-user.id \
  /tmp/issue-test-user-token.py \
  /tmp/pr7-announcement-user.token

echo 'browser_tokens_prepared=true'
