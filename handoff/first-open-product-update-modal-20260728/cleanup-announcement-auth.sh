#!/usr/bin/env bash
set -Eeuo pipefail

live_container=open-webui
isolated_private=/home/aiserver/staging/pr7-announcement-hotpatch-6ba5c1398/isolated/private
live_private=/home/aiserver/staging/pr7-announcement-hotpatch-6ba5c1398/live/private

docker exec "$live_container" rm -f \
  /tmp/pr7-announcement-admin.id \
  /tmp/pr7-announcement-admin.token \
  /tmp/pr7-announcement-user.id \
  /tmp/pr7-announcement-user.token \
  /tmp/announcement-four-worker-probe.py \
  /tmp/live-worker-auth-probe.py \
  /tmp/diagnose-live-token.py \
  /tmp/diagnose-live-secret-surface.py \
  /tmp/issue-test-user-token.py

rm -f \
  "$isolated_private/admin.token" \
  "$isolated_private/user.token" \
  "$isolated_private/user.id" \
  "$live_private/admin.token" \
  "$live_private/admin.id" \
  "$live_private/admin.email" \
  "$live_private/user.token" \
  "$live_private/user.id"

for path in \
  "$isolated_private/admin.token" \
  "$isolated_private/user.token" \
  "$isolated_private/user.id" \
  "$live_private/admin.token" \
  "$live_private/admin.id" \
  "$live_private/admin.email" \
  "$live_private/user.token" \
  "$live_private/user.id"; do
  [[ ! -e "$path" ]]
done

[[ -f "$isolated_private/admin-config.before-e2e.json" ]]
[[ -f "$live_private/admin-config.before-publish.json" ]]
[[ -f "$live_private/admin-config.after-publish.json" ]]
echo 'announcement_auth_cleanup_complete=true'
