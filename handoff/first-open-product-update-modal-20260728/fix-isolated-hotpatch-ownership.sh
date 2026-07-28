#!/usr/bin/env bash
set -Eeuo pipefail

container=open-webui-pr7
expected_container_id=715d9301220d94b8e4bb1d58a01b67c17358fca7d7bb1ad2465885b2b22af714
expected_image_id=sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b

[[ "$(docker inspect "$container" --format '{{.Id}}')" == "$expected_container_id" ]]
[[ "$(docker inspect "$container" --format '{{.Image}}')" == "$expected_image_id" ]]
[[ "$(docker inspect "$container" --format '{{.State.Health.Status}}')" == healthy ]]
[[ "$(docker inspect "$container" --format '{{.RestartCount}}')" == 0 ]]

docker exec "$container" chown 0:0 \
  /app/backend/open_webui/config.py \
  /app/backend/open_webui/main.py \
  /app/backend/open_webui/routers/auths.py
docker exec "$container" chown -R 0:0 /app/build

docker exec "$container" stat -c '%n|%U:%G|%a' \
  /app/build/index.html \
  /app/backend/open_webui/config.py \
  /app/backend/open_webui/main.py \
  /app/backend/open_webui/routers/auths.py

[[ "$(docker inspect "$container" --format '{{.State.Health.Status}}')" == healthy ]]
[[ "$(docker inspect "$container" --format '{{.RestartCount}}')" == 0 ]]
