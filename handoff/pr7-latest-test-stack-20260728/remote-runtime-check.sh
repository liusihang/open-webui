#!/usr/bin/env bash
set -Eeuo pipefail

WEB_CONTAINER=open-webui-pr7

echo ANCHOR
docker inspect "${WEB_CONTAINER}" --format 'container_id={{.Id}} image_ref={{.Config.Image}} image_id={{.Image}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restarts={{.RestartCount}} started={{.State.StartedAt}}'
docker inspect "${WEB_CONTAINER}" --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^(ENABLE_DB_MIGRATIONS|UVICORN_WORKERS|ENABLE_AGENT_MODE|AGENT_RUNTIME_BASE_URL)=' | sort

echo PROCESSES
docker top "${WEB_CONTAINER}" -eo pid,ppid,args

started=$(docker inspect "${WEB_CONTAINER}" --format '{{.State.StartedAt}}')
now=$(date --iso-8601=seconds)

echo STARTUP_LIFECYCLE
docker logs --timestamps --since "${started}" "${WEB_CONTAINER}" 2>&1 | grep -Ea 'Started server process|Application startup complete|Startup singleton tasks|External dependencies of functions and tools|Initialized [0-9]+ tool server' || true

echo WORKER_FAILURE_LIFECYCLE
docker logs --timestamps --since "${started}" "${WEB_CONTAINER}" 2>&1 | grep -Ea 'Waiting for child process|Child process .* died|Finished server process|Application shutdown complete' || true

echo CONTAINER_EVENTS
docker events \
  --since "${started}" \
  --until "${now}" \
  --filter "container=${WEB_CONTAINER}" \
  --filter event=die \
  --filter event=kill \
  --filter event=oom \
  --filter event=restart

echo HEALTH
curl -fsS http://127.0.0.1:18085/health
printf '\n'
curl -fsS http://127.0.0.1:18085/health/db
printf '\n'

echo RESOURCES
docker stats --no-stream --format '{{.Name}} cpu={{.CPUPerc}} memory={{.MemUsage}} pids={{.PIDs}}' "${WEB_CONTAINER}" openwebui-pr7-db openwebui-pr7-redis openwebui-pr7-agentscope-runtime
