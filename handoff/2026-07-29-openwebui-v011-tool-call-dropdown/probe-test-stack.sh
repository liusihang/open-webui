#!/usr/bin/env bash
set -euo pipefail

echo 'HOST'
hostname
echo 'CONTAINERS_PUBLISHING_18085'
docker ps --no-trunc --format '{{.ID}}\t{{.Image}}\t{{.Names}}\t{{.Ports}}\t{{.Status}}' | awk 'index($0, "18085") > 0'

container_id="$(docker ps --format '{{.ID}} {{.Ports}}' | awk 'index($0, "18085") > 0 { print $1; exit }')"
if [[ -z "${container_id}" ]]; then
  echo 'NO_CONTAINER_FOUND'
  exit 2
fi

echo 'CONTAINER_INSPECT'
docker inspect --format 'id={{.Id}} image={{.Image}} name={{.Name}} started={{.State.StartedAt}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${container_id}"
echo 'IMAGE_INSPECT'
image_id="$(docker inspect --format '{{.Image}}' "${container_id}")"
docker image inspect --format 'id={{.Id}} tags={{json .RepoTags}} created={{.Created}} labels={{json .Config.Labels}}' "${image_id}"
echo 'STATIC_ASSET_PROBE'
docker exec "${container_id}" sh -lc 'find /app/build/_app/immutable -type f -name "*.js" -print0 2>/dev/null | xargs -0 grep -l "Toggle details" 2>/dev/null | head -10 || true'
echo 'RECENT_ERRORS'
docker logs --since 20m "${container_id}" 2>&1 | grep -E 'ERROR|Exception|Traceback|ReferenceError|TypeError' | tail -80 || true
