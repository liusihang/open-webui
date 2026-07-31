#!/usr/bin/env bash
set -euo pipefail

target_container='open-webui-pr7'
formal_container='open-webui'

capture_container() {
	local container="$1"
	docker inspect --format '{{.Id}}|{{.Image}}|{{.Config.Image}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.RestartCount}}|{{.State.OOMKilled}}|{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.project.working_dir"}}|{{index .Config.Labels "com.docker.compose.project.config_files"}}' "${container}"
}

printf 'captured_at=%s\n' "$(date --iso-8601=seconds)"
printf 'target=%s\n' "$(capture_container "${target_container}")"
printf 'formal=%s\n' "$(capture_container "${formal_container}")"
printf 'target_frontend_version='
docker exec "${target_container}" cat /app/build/_app/version.json
printf '\n'
printf 'target_health='
curl --noproxy '*' --fail --silent --show-error --max-time 15 http://127.0.0.1:18085/health
printf '\n'
printf 'target_health_db='
curl --noproxy '*' --fail --silent --show-error --max-time 15 http://127.0.0.1:18085/health/db
printf '\n'
printf 'target_version='
curl --noproxy '*' --fail --silent --show-error --max-time 15 http://127.0.0.1:18085/api/version
printf '\n'
docker image inspect --format 'target_image={{.Id}}|{{json .RepoTags}}|revision={{index .Config.Labels "org.opencontainers.image.revision"}}|base={{index .Config.Labels "io.openwebui.hotpatch.base-image"}}|scope={{index .Config.Labels "io.openwebui.hotpatch.scope"}}' "$(docker inspect --format '{{.Image}}' "${target_container}")"
docker image inspect --format 'formal_image={{.Id}}|{{json .RepoTags}}|revision={{index .Config.Labels "org.opencontainers.image.revision"}}' "$(docker inspect --format '{{.Image}}' "${formal_container}")"
df -h /var/lib/docker /home/aiserver/staging 2>/dev/null || df -h /
