#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${OPENWEBUI_AISERVER_HOST:-aiserver}"
LIVE_STACK_DIR="${OPENWEBUI_AISERVER_LIVE_DIR:-/srv/openwebui-migration}"
LIVE_COMPOSE_FILE="${OPENWEBUI_AISERVER_COMPOSE_FILE:-compose.yaml}"
LIVE_ENV_FILE="${OPENWEBUI_AISERVER_ENV_FILE:-.env}"
STAGING_ROOT="${OPENWEBUI_AISERVER_STAGING_ROOT:-/home/aiserver/staging}"
LIVE_PROJECT="${OPENWEBUI_AISERVER_COMPOSE_PROJECT:-openwebui-migration}"
SERVICE_NAME="open-webui"
DEFAULT_PROXY_URL="${OPENWEBUI_AISERVER_PROXY_URL:-}"

usage() {
	cat <<'EOF'
Usage:
  upgrade_aiserver_openwebui.sh inspect
  upgrade_aiserver_openwebui.sh build-only [--commit <git-ref>] [--image-tag <tag>] [--proxy-url <url>]
  upgrade_aiserver_openwebui.sh switch-image --image-tag <tag>

Commands:
  inspect       Show current live compose image, env path, runtime health, and image config.
  build-only    Sync a local commit to aiserver staging, patch the staged Dockerfile, and build a replacement image with docker buildx only.
  switch-image  Replace only the open-webui image line in the live compose and recreate only open-webui.

Defaults:
  host          aiserver
  live stack    /srv/openwebui-migration/compose.yaml
  live env      /srv/openwebui-migration/.env

Safety rule:
  This script only changes the open-webui image by default. It does not change ports,
  Caddy, frpc, Postgres, Redis, or any other service unless you edit it intentionally.
EOF
}

die() {
	echo "error: $*" >&2
	exit 1
}

require_cmd() {
	command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

remote_read() {
	local remote_cmd="$1"
	ssh "$REMOTE_HOST" "$remote_cmd"
}

remote_sudo_script() {
	local script_text="$1"
	local remote_script_path="/tmp/openwebui_aiserver_upgrade.$$.$RANDOM.sh"
	ssh "$REMOTE_HOST" "printf '%s\n' \"\$OPENWEBUI_AISERVER_SUDO_PASSWORD\" >/dev/null 2>&1 || true" >/dev/null 2>&1 || true
	ssh "$REMOTE_HOST" "cat >\"$remote_script_path\" && chmod +x \"$remote_script_path\"" <<<"$script_text"
	if [[ -n "${OPENWEBUI_AISERVER_SUDO_PASSWORD:-}" ]]; then
		ssh "$REMOTE_HOST" "printf '%s\n' '${OPENWEBUI_AISERVER_SUDO_PASSWORD}' | sudo -S -p '' bash '$remote_script_path'"
	else
		ssh -tt "$REMOTE_HOST" "sudo -k bash '$remote_script_path'"
	fi
	ssh "$REMOTE_HOST" "rm -f '$remote_script_path'" >/dev/null 2>&1 || true
}

build_only() {
	local git_ref="HEAD"
	local image_tag=""
	local proxy_url="${DEFAULT_PROXY_URL}"

	while [[ $# -gt 0 ]]; do
		case "$1" in
			--commit)
				git_ref="${2:?missing value for --commit}"
				shift 2
				;;
			--image-tag)
				image_tag="${2:?missing value for --image-tag}"
				shift 2
				;;
			--proxy-url)
				proxy_url="${2:?missing value for --proxy-url}"
				shift 2
				;;
			*)
				die "unknown build-only option: $1"
				;;
		esac
	done

	local commit_short
	commit_short="$(git rev-parse --short=10 "$git_ref")"
	if [[ -z "$image_tag" ]]; then
		image_tag="open-webui:${commit_short}"
	fi

	local staging_dir="${STAGING_ROOT}/openwebui-${commit_short}"
	local build_hash
	build_hash="$(git rev-parse --short=9 "$git_ref")"

	echo "==> Syncing ${git_ref} to ${REMOTE_HOST}:${staging_dir}"
	COPYFILE_DISABLE=1 git archive --format=tar "$git_ref" \
		| ssh "$REMOTE_HOST" "rm -rf '$staging_dir' && mkdir -p '$staging_dir' && tar -xf - -C '$staging_dir'"

	echo "==> Patching staged Dockerfile for buildx and domestic mirrors"
	local patch_script
	read -r -d '' patch_script <<EOF || true
set -euo pipefail
cd '$staging_dir'
cp Dockerfile Dockerfile.bak.codex
cp .npmrc .npmrc.bak.codex
printf '\nregistry=https://registry.npmmirror.com\n' >> .npmrc
python3 - <<'PY'
from pathlib import Path
p = Path("Dockerfile")
text = p.read_text()
old = "WORKDIR /app\\n\\nCOPY package.json package-lock.json .npmrc ./\\n"
new = "WORKDIR /app\\n\\nENV NPM_CONFIG_REGISTRY=https://registry.npmmirror.com \\\\\n    CYPRESS_INSTALL_BINARY=0 \\\\\n    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \\\\\n    PUPPETEER_SKIP_DOWNLOAD=true \\\\\n    ONNXRUNTIME_NODE_INSTALL_CUDA=skip \\\\\n    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \\\\\n    PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn \\\\\n    UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple\\n\\nCOPY package.json package-lock.json .npmrc ./\\n"
if old not in text:
    raise SystemExit("frontend env anchor not found")
text = text.replace(old, new, 1)
old2 = "FROM python:3.11.14-slim-bookworm AS base\\n\\n# Use args\\n"
new2 = "FROM python:3.11.14-slim-bookworm AS base\\n\\nENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \\\\\n    PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn \\\\\n    UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple\\n\\n# Use args\\n"
if old2 not in text:
    raise SystemExit("backend env anchor not found")
text = text.replace(old2, new2, 1)
old3 = "RUN apt-get update && \\\\\n    apt-get install -y --no-install-recommends \\\\\n"
new3 = "RUN sed -i 's|http://deb.debian.org/debian|https://mirrors.tuna.tsinghua.edu.cn/debian|g; s|http://deb.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' /etc/apt/sources.list.d/debian.sources && \\\\\n    apt-get update && \\\\\n    apt-get install -y --no-install-recommends \\\\\n"
if old3 not in text:
    raise SystemExit("apt block not found")
text = text.replace(old3, new3, 1)
p.write_text(text)
PY
EOF
	remote_sudo_script "$patch_script"

	local build_cmd
read -r -d '' build_cmd <<EOF || true
set -euo pipefail
cd '$staging_dir'
LOG='${staging_dir}/docker-build-${commit_short}.log'
mv "\$LOG" "\${LOG}.prev.\$(date +%s)" 2>/dev/null || true
: > "\$LOG"
BUILDER_NAME='default'
if ! docker buildx inspect "\$BUILDER_NAME" >/dev/null 2>&1; then
  BUILDER_NAME='codex-buildx'
  if ! docker buildx inspect "\$BUILDER_NAME" >/dev/null 2>&1; then
    docker buildx create --name "\$BUILDER_NAME" --use >/dev/null
  else
    docker buildx use "\$BUILDER_NAME" >/dev/null
  fi
fi
docker buildx inspect "\$BUILDER_NAME" --bootstrap >/dev/null
nohup env \\
  HTTP_PROXY='${proxy_url}' \\
  HTTPS_PROXY='${proxy_url}' \\
  ALL_PROXY='${proxy_url}' \\
  NO_PROXY='localhost,127.0.0.1' \\
  docker buildx build \\
    --builder "\$BUILDER_NAME" \\
    --load \\
    --progress=plain \\
    --build-arg HTTP_PROXY='${proxy_url}' \\
    --build-arg HTTPS_PROXY='${proxy_url}' \\
    --build-arg ALL_PROXY='${proxy_url}' \\
    --build-arg NO_PROXY='localhost,127.0.0.1' \\
    --build-arg BUILD_HASH='${build_hash}' \\
    -t '${image_tag}' \\
    . > "\$LOG" 2>&1 < /dev/null &
echo "PID=\$!"
echo "LOG=\$LOG"
EOF

	echo "==> Starting remote build for ${image_tag}"
	remote_sudo_script "$build_cmd"
	echo "==> After completion, inspect with:"
	echo "    ssh ${REMOTE_HOST} \"printf '***' | sudo -S -p '' docker image inspect ${image_tag}\""
}

inspect_live() {
	require_cmd ssh
	remote_sudo_script "set -e; echo '=== runtime ==='; docker inspect '${SERVICE_NAME}' --format 'IMAGE={{.Config.Image}} STATUS={{.State.Status}} HEALTH={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'; echo '=== image config ==='; docker inspect '${SERVICE_NAME}' --format 'CMD={{json .Config.Cmd}} ENTRYPOINT={{json .Config.Entrypoint}}'; echo '=== live compose image ==='; grep -n 'image: open-webui:' '${LIVE_STACK_DIR}/${LIVE_COMPOSE_FILE}'; echo '=== db ==='; docker inspect openwebui-db --format 'STATUS={{.State.Status}} HEALTH={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'; echo '=== redis ==='; docker inspect openwebui-redis --format 'STATUS={{.State.Status}} HEALTH={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'"
	echo "=== env file ==="
	remote_read "sed -n '1,80p' '${LIVE_STACK_DIR}/${LIVE_ENV_FILE}'"
}

switch_image() {
	local image_tag=""

	while [[ $# -gt 0 ]]; do
		case "$1" in
			--image-tag)
				image_tag="${2:?missing value for --image-tag}"
				shift 2
				;;
			*)
				die "unknown switch-image option: $1"
				;;
		esac
	done

	[[ -n "$image_tag" ]] || die "switch-image requires --image-tag"

	local backup_name
	backup_name="compose.yaml.bak.$(date +%Y%m%d%H%M%S)"

	echo "==> Switching live compose image to ${image_tag}"
	local switch_cmd
	read -r -d '' switch_cmd <<EOF || true
set -euo pipefail
cd '${LIVE_STACK_DIR}'
cp '${LIVE_COMPOSE_FILE}' '${backup_name}'
python3 - <<'PY'
from pathlib import Path
p = Path('${LIVE_COMPOSE_FILE}')
text = p.read_text()
old_prefix = '    image: open-webui:'
lines = text.splitlines()
for idx, line in enumerate(lines):
    if line.startswith(old_prefix):
        lines[idx] = '    image: ${image_tag}'
        break
else:
    raise SystemExit('open-webui image line not found')
p.write_text('\\n'.join(lines) + '\\n')
PY
grep -n 'image: open-webui:' '${LIVE_COMPOSE_FILE}'
docker compose -p '${LIVE_PROJECT}' -f '${LIVE_COMPOSE_FILE}' up -d --no-deps --force-recreate '${SERVICE_NAME}'
for i in \$(seq 1 60); do
  status=\$(docker inspect '${SERVICE_NAME}' --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}')
  if [ "\$status" = 'healthy' ]; then
    break
  fi
  if [ "\$status" = 'unhealthy' ]; then
    break
  fi
  sleep 5
done
docker inspect '${SERVICE_NAME}' --format 'IMAGE={{.Config.Image}} STATUS={{.State.Status}} HEALTH={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'
EOF
	remote_sudo_script "$switch_cmd"
}

main() {
	require_cmd git
	require_cmd ssh

	local cmd="${1:-}"
	if [[ -z "$cmd" || "$cmd" == "--help" || "$cmd" == "-h" ]]; then
		usage
		exit 0
	fi
	shift || true

	case "$cmd" in
		inspect)
			inspect_live "$@"
			;;
		build-only)
			build_only "$@"
			;;
		switch-image)
			switch_image "$@"
			;;
		*)
			die "unknown command: $cmd"
			;;
	esac
}

main "$@"
