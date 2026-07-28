#!/usr/bin/env bash
set -Eeuo pipefail

WEB_CONTAINER=${WEB_CONTAINER:-open-webui}
SINCE=${SINCE:-2026-07-27T21:20:00+08:00}
UNTIL=${UNTIL:-2026-07-27T21:35:00+08:00}

echo CONTAINER_STATE
docker inspect "${WEB_CONTAINER}" --format 'container_id={{.Id}} image_id={{.Image}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} oom_killed={{.State.OOMKilled}} restarts={{.RestartCount}} started={{.State.StartedAt}}'

echo DOCKER_EVENTS
docker events \
  --since "${SINCE}" \
  --until "${UNTIL}" \
  --filter "container=${WEB_CONTAINER}" \
  --filter event=start \
  --filter event=stop \
  --filter event=die \
  --filter event=kill \
  --filter event=oom \
  --filter event=restart

echo WEBUI_WORKER_WINDOW
docker logs \
  --timestamps \
  --since "${SINCE}" \
  --until "${UNTIL}" \
  "${WEB_CONTAINER}" 2>&1 | grep -Ea 'Waiting for child process|Child process .* died|Started server process|Finished server process|Waiting for application startup|Application startup complete|Application shutdown complete|Startup singleton tasks|External dependencies of functions and tools' || true

echo KERNEL_OOM_WINDOW
journalctl -k \
  --since "${SINCE}" \
  --until "${UNTIL}" \
  --no-pager | grep -Eai 'oom|out of memory|killed process|segfault|general protection|python.*trap' || true
