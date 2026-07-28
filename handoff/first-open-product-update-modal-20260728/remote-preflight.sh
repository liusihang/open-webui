#!/usr/bin/env bash
set -Eeuo pipefail

containers=(
  open-webui-pr7
  openwebui-pr7-agentscope-runtime
  open-webui
  openwebui-agentscope-runtime
)

for container in "${containers[@]}"; do
  if ! docker inspect "$container" >/dev/null 2>&1; then
    echo "missing_container=$container"
    continue
  fi

  docker inspect "$container" --format '{{.Name}}|{{.Id}}|{{.Image}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}|{{.RestartCount}}|{{.State.StartedAt}}|{{index .Config.Labels "com.docker.compose.project.config_files"}}'
  docker port "$container" || true
done

for container in open-webui-pr7 open-webui; do
  echo "workers=$container"
  docker exec "$container" printenv UVICORN_WORKERS
  docker top "$container" -eo pid,ppid,lstart,args
  echo "paths=$container"
  docker exec "$container" stat -c '%n|%U:%G|%a|%s|%Y' \
    /app/build/index.html \
    /app/backend/open_webui/config.py \
    /app/backend/open_webui/main.py \
    /app/backend/open_webui/routers/auths.py
done

curl --fail --silent --show-error --max-time 5 http://127.0.0.1:18085/health
printf '\n'
curl --fail --silent --show-error --max-time 5 http://127.0.0.1/health
printf '\n'

docker stats --no-stream open-webui-pr7 open-webui
