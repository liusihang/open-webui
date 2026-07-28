#!/usr/bin/env bash
set -euo pipefail

WEBUI_CONTAINER='open-webui-pr7'
DB_CONTAINER='openwebui-pr7-db'

printf 'webui_networks='
docker inspect --format '{{range $name, $_ := .NetworkSettings.Networks}}{{printf "%s " $name}}{{end}}' "${WEBUI_CONTAINER}"
printf '\n'
printf 'db_networks='
docker inspect --format '{{range $name, $_ := .NetworkSettings.Networks}}{{printf "%s " $name}}{{end}}' "${DB_CONTAINER}"
printf '\n'
printf 'project=%s\n' "$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "${WEBUI_CONTAINER}")"
printf 'service=%s\n' "$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "${WEBUI_CONTAINER}")"
