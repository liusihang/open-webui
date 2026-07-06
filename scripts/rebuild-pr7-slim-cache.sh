#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${OPENWEBUI_PR7_SLIM_REMOTE:-aiserver}"
GIT_REF="${OPENWEBUI_PR7_SLIM_GIT_REF:-HEAD}"
BUILD_DIR="${OPENWEBUI_PR7_SLIM_BUILD_DIR:-/home/aiserver/staging/openwebui-pr7-slim-build}"
CACHE_DIR="${OPENWEBUI_PR7_SLIM_CACHE_DIR:-/home/aiserver/.cache/openwebui-pr7-slim-buildx}"
BUILDER_NAME="${OPENWEBUI_PR7_SLIM_BUILDER:-codex-pr7-slim-cache}"
IMAGE_TAG="${OPENWEBUI_PR7_SLIM_IMAGE_TAG:-}"
PLATFORM="${OPENWEBUI_PR7_SLIM_PLATFORM:-}"
PROXY_URL="${OPENWEBUI_PR7_SLIM_PROXY_URL:-}"
ALPINE_MIRROR="${OPENWEBUI_PR7_SLIM_ALPINE_MIRROR:-}"
APT_DEBIAN_MIRROR="${OPENWEBUI_PR7_SLIM_APT_DEBIAN_MIRROR:-}"
APT_SECURITY_MIRROR="${OPENWEBUI_PR7_SLIM_APT_SECURITY_MIRROR:-}"
NPM_REGISTRY="${OPENWEBUI_PR7_SLIM_NPM_REGISTRY:-}"
UV_DEFAULT_INDEX="${OPENWEBUI_PR7_SLIM_UV_DEFAULT_INDEX:-}"
PYODIDE_INDEX_URL="${OPENWEBUI_PR7_SLIM_PYODIDE_INDEX_URL:-}"
PYODIDE_PYPI_API_BASE_URL="${OPENWEBUI_PR7_SLIM_PYODIDE_PYPI_API_BASE_URL:-}"
PYODIDE_PYPI_FILES_BASE_URL="${OPENWEBUI_PR7_SLIM_PYODIDE_PYPI_FILES_BASE_URL:-}"
PYODIDE_PYPI_INDEX_URLS="${OPENWEBUI_PR7_SLIM_PYODIDE_PYPI_INDEX_URLS:-}"
SEED_PYODIDE_DIR="${OPENWEBUI_PR7_SLIM_SEED_PYODIDE_DIR:-}"
DOCKERFILE_SYNTAX_IMAGE="${OPENWEBUI_PR7_SLIM_DOCKERFILE_SYNTAX_IMAGE:-}"
NODE_BASE_IMAGE="${OPENWEBUI_PR7_SLIM_NODE_BASE_IMAGE:-}"
PYTHON_BASE_IMAGE="${OPENWEBUI_PR7_SLIM_PYTHON_BASE_IMAGE:-}"

usage() {
	cat <<'EOF'
Usage:
  rebuild-pr7-slim-cache.sh [options]

Options:
  --remote <host>               SSH host for the build machine. Default: aiserver
  --git-ref <ref>               Local git ref to archive. Default: HEAD
  --image-tag <tag>             Docker image tag. Default: open-webui:pr7-slim-<short-ref>
  --build-dir <dir>             Remote staging directory for the clean archive.
  --cache-dir <dir>             Remote BuildKit local cache root.
  --builder <name>              Remote buildx builder name.
  --platform <platform>         Optional docker buildx --platform value.
  --proxy-url <url>             Optional proxy passed to build env and build args.
  --alpine-mirror <url>         Optional ALPINE_MIRROR build arg.
  --apt-debian-mirror <url>     Optional APT_DEBIAN_MIRROR build arg.
  --apt-security-mirror <url>   Optional APT_SECURITY_MIRROR build arg.
  --npm-registry <url>          Optional NPM_REGISTRY build arg.
  --uv-default-index <url>      Optional UV_DEFAULT_INDEX build arg.
  --pyodide-index-url <url>     Optional PYODIDE_INDEX_URL build arg.
  --pyodide-pypi-api-base-url <url>
                                 Optional PYODIDE_PYPI_API_BASE_URL build arg.
  --pyodide-pypi-files-base-url <url>
                                 Optional PYODIDE_PYPI_FILES_BASE_URL build arg.
  --pyodide-pypi-index-urls <csv>
                                 Optional PYODIDE_PYPI_INDEX_URLS build arg.
  --seed-pyodide-dir <dir>      Optional local static/pyodide seed dir overlay.
  --dockerfile-syntax-image <image>
                                 Optional remote staging patch for Dockerfile syntax image.
  --node-base-image <image>     Optional remote staging patch for the frontend base image.
  --python-base-image <image>   Optional remote staging patch for the backend base image.
  -h, --help                    Show this help.

Behavior:
  - Archives the selected git ref with git archive and extracts it remotely.
  - Builds only an image with docker buildx build --load.
  - Uses BuildKit local cache-from/cache-to under --cache-dir.
  - Promotes the new cache pointer only after docker buildx build succeeds.
  - Does not run docker compose, stop, restart, up, down, rm, or prune.
EOF
}

die() {
	echo "error: $*" >&2
	exit 1
}

require_cmd() {
	command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

shell_quote() {
	printf '%q' "$1"
}

validate_remote_path() {
	local name="$1"
	local path="$2"

	[[ "$path" == /* ]] || die "$name must be an absolute remote path: $path"
	case "$path" in
		/ | /home | /home/aiserver | /srv | /tmp | /var | /var/tmp)
			die "$name is too broad for build-cache operations: $path"
			;;
	esac
}

validate_local_dir() {
	local name="$1"
	local dir="$2"

	[[ -d "$dir" ]] || die "$name must be an existing directory: $dir"
}

extract_archive_to_remote_context() {
	echo "==> Archiving ${COMMIT} to ${REMOTE_HOST}:${REMOTE_CONTEXT_DIR}"
	COPYFILE_DISABLE=1 COPY_EXTENDED_ATTRIBUTES_DISABLE=1 git archive --format=tar "$COMMIT" -o "$LOCAL_ARCHIVE_TAR"
	scp "$LOCAL_ARCHIVE_TAR" "${REMOTE_HOST}:${REMOTE_ARCHIVE_TAR}" >/dev/null
	ssh "$REMOTE_HOST" "bash -lc 'set -euo pipefail; rm -rf $(shell_quote "$REMOTE_CONTEXT_DIR"); mkdir -p $(shell_quote "$REMOTE_CONTEXT_DIR"); tar -xf $(shell_quote "$REMOTE_ARCHIVE_TAR") -C $(shell_quote "$REMOTE_CONTEXT_DIR"); rm -f $(shell_quote "$REMOTE_ARCHIVE_TAR")'"
}

overlay_seed_pyodide_dir() {
	[[ -n "$SEED_PYODIDE_DIR" ]] || return 0

	echo "==> Overlaying seeded static/pyodide from ${SEED_PYODIDE_DIR}"
	COPYFILE_DISABLE=1 COPY_EXTENDED_ATTRIBUTES_DISABLE=1 tar \
		-C "$(dirname "$SEED_PYODIDE_DIR")" \
		-cf "$LOCAL_SEED_TAR" \
		"$(basename "$SEED_PYODIDE_DIR")"
	scp "$LOCAL_SEED_TAR" "${REMOTE_HOST}:${REMOTE_SEED_TAR}" >/dev/null
	ssh "$REMOTE_HOST" "bash -lc 'set -euo pipefail; mkdir -p $(shell_quote "$REMOTE_CONTEXT_DIR/static"); rm -rf $(shell_quote "$REMOTE_CONTEXT_DIR/static/pyodide"); tar -xf $(shell_quote "$REMOTE_SEED_TAR") -C $(shell_quote "$REMOTE_CONTEXT_DIR/static"); rm -f $(shell_quote "$REMOTE_SEED_TAR")'"
}

patch_remote_dockerfile() {
	[[ -n "$DOCKERFILE_SYNTAX_IMAGE$NODE_BASE_IMAGE$PYTHON_BASE_IMAGE" ]] || return 0

	echo "==> Patching staged Dockerfile image sources"
	{
		printf 'set -euo pipefail\n'
		printf 'context_dir=%q\n' "$REMOTE_CONTEXT_DIR"
		printf 'dockerfile_syntax_image=%q\n' "$DOCKERFILE_SYNTAX_IMAGE"
		printf 'node_base_image=%q\n' "$NODE_BASE_IMAGE"
		printf 'python_base_image=%q\n' "$PYTHON_BASE_IMAGE"
		cat <<'EOF'
cd "$context_dir"
cp Dockerfile Dockerfile.pre-rebuild-helper

python3 - "$dockerfile_syntax_image" "$node_base_image" "$python_base_image" <<'PY'
from pathlib import Path
import re
import sys

dockerfile_syntax_image, node_base_image, python_base_image = sys.argv[1:4]
path = Path("Dockerfile")
text = path.read_text()

if dockerfile_syntax_image:
    text = re.sub(
        r"^# syntax=.*$",
        f"# syntax={dockerfile_syntax_image}",
        text,
        count=1,
        flags=re.MULTILINE,
    )

if node_base_image:
    text = re.sub(
        r"^FROM --platform=\$BUILDPLATFORM \S+ AS build$",
        f"FROM --platform=$BUILDPLATFORM {node_base_image} AS build",
        text,
        count=1,
        flags=re.MULTILINE,
    )

if python_base_image:
    text = re.sub(
        r"^FROM \S+ AS base$",
        f"FROM {python_base_image} AS base",
        text,
        count=1,
        flags=re.MULTILINE,
    )

path.write_text(text)
PY
EOF
	} | ssh "$REMOTE_HOST" bash -s
}

while [[ $# -gt 0 ]]; do
	case "$1" in
		--remote)
			REMOTE_HOST="${2:?missing value for --remote}"
			shift 2
			;;
		--git-ref)
			GIT_REF="${2:?missing value for --git-ref}"
			shift 2
			;;
		--image-tag)
			IMAGE_TAG="${2:?missing value for --image-tag}"
			shift 2
			;;
		--build-dir)
			BUILD_DIR="${2:?missing value for --build-dir}"
			shift 2
			;;
		--cache-dir)
			CACHE_DIR="${2:?missing value for --cache-dir}"
			shift 2
			;;
		--builder)
			BUILDER_NAME="${2:?missing value for --builder}"
			shift 2
			;;
		--platform)
			PLATFORM="${2:?missing value for --platform}"
			shift 2
			;;
			--proxy-url)
				PROXY_URL="${2:?missing value for --proxy-url}"
				shift 2
				;;
			--alpine-mirror)
				ALPINE_MIRROR="${2:?missing value for --alpine-mirror}"
				shift 2
				;;
			--apt-debian-mirror)
				APT_DEBIAN_MIRROR="${2:?missing value for --apt-debian-mirror}"
				shift 2
			;;
		--apt-security-mirror)
			APT_SECURITY_MIRROR="${2:?missing value for --apt-security-mirror}"
			shift 2
			;;
		--npm-registry)
			NPM_REGISTRY="${2:?missing value for --npm-registry}"
			shift 2
			;;
			--uv-default-index)
				UV_DEFAULT_INDEX="${2:?missing value for --uv-default-index}"
				shift 2
				;;
			--pyodide-index-url)
				PYODIDE_INDEX_URL="${2:?missing value for --pyodide-index-url}"
				shift 2
				;;
			--pyodide-pypi-api-base-url)
				PYODIDE_PYPI_API_BASE_URL="${2:?missing value for --pyodide-pypi-api-base-url}"
				shift 2
			;;
		--pyodide-pypi-files-base-url)
			PYODIDE_PYPI_FILES_BASE_URL="${2:?missing value for --pyodide-pypi-files-base-url}"
			shift 2
			;;
		--pyodide-pypi-index-urls)
			PYODIDE_PYPI_INDEX_URLS="${2:?missing value for --pyodide-pypi-index-urls}"
			shift 2
			;;
		--seed-pyodide-dir)
			SEED_PYODIDE_DIR="${2:?missing value for --seed-pyodide-dir}"
			shift 2
			;;
		--dockerfile-syntax-image)
			DOCKERFILE_SYNTAX_IMAGE="${2:?missing value for --dockerfile-syntax-image}"
			shift 2
			;;
		--node-base-image)
			NODE_BASE_IMAGE="${2:?missing value for --node-base-image}"
			shift 2
			;;
		--python-base-image)
			PYTHON_BASE_IMAGE="${2:?missing value for --python-base-image}"
			shift 2
			;;
		-h | --help)
			usage
			exit 0
			;;
		*)
			die "unknown option: $1"
			;;
	esac
done

require_cmd git
require_cmd ssh
require_cmd scp
require_cmd tar
require_cmd mktemp

validate_remote_path "--build-dir" "$BUILD_DIR"
validate_remote_path "--cache-dir" "$CACHE_DIR"
if [[ -n "$SEED_PYODIDE_DIR" ]]; then
	validate_local_dir "--seed-pyodide-dir" "$SEED_PYODIDE_DIR"
fi

COMMIT="$(git rev-parse "$GIT_REF")"
BUILD_HASH="$(git rev-parse --short=10 "$GIT_REF")"
if [[ -z "$IMAGE_TAG" ]]; then
	IMAGE_TAG="open-webui:pr7-slim-${BUILD_HASH}"
fi

REMOTE_CONTEXT_DIR="${BUILD_DIR%/}/src"
REMOTE_LOG="${BUILD_DIR%/}/docker-build-${BUILD_HASH}.log"
REMOTE_ARCHIVE_TAR="${BUILD_DIR%/}/src.tar"
REMOTE_SEED_TAR="${BUILD_DIR%/}/seed-pyodide.tar"
LOCAL_STAGE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/openwebui-pr7-slim.XXXXXX")"
LOCAL_ARCHIVE_TAR="${LOCAL_STAGE_DIR}/src.tar"
LOCAL_SEED_TAR="${LOCAL_STAGE_DIR}/seed-pyodide.tar"
trap 'rm -rf "$LOCAL_STAGE_DIR"' EXIT

extract_archive_to_remote_context
overlay_seed_pyodide_dir
patch_remote_dockerfile

echo "==> Starting cached PR7 slim build for ${IMAGE_TAG}"
{
	printf 'set -euo pipefail\n'
	printf 'context_dir=%q\n' "$REMOTE_CONTEXT_DIR"
	printf 'cache_root=%q\n' "$CACHE_DIR"
	printf 'builder_name=%q\n' "$BUILDER_NAME"
	printf 'image_tag=%q\n' "$IMAGE_TAG"
	printf 'build_hash=%q\n' "$BUILD_HASH"
	printf 'log_path=%q\n' "$REMOTE_LOG"
	printf 'platform=%q\n' "$PLATFORM"
	printf 'proxy_url=%q\n' "$PROXY_URL"
	printf 'alpine_mirror=%q\n' "$ALPINE_MIRROR"
	printf 'apt_debian_mirror=%q\n' "$APT_DEBIAN_MIRROR"
	printf 'apt_security_mirror=%q\n' "$APT_SECURITY_MIRROR"
	printf 'npm_registry=%q\n' "$NPM_REGISTRY"
	printf 'uv_default_index=%q\n' "$UV_DEFAULT_INDEX"
	printf 'pyodide_index_url=%q\n' "$PYODIDE_INDEX_URL"
	printf 'pyodide_pypi_api_base_url=%q\n' "$PYODIDE_PYPI_API_BASE_URL"
	printf 'pyodide_pypi_files_base_url=%q\n' "$PYODIDE_PYPI_FILES_BASE_URL"
	printf 'pyodide_pypi_index_urls=%q\n' "$PYODIDE_PYPI_INDEX_URLS"
	cat <<'EOF'
mkdir -p "$(dirname "$log_path")" "$cache_root"
cache_current="$cache_root/current"
cache_next="$cache_root/cache-$build_hash-$(date +%Y%m%d%H%M%S)"

if ! docker buildx inspect "$builder_name" >/dev/null 2>&1; then
	docker buildx create --name "$builder_name" --use >/dev/null
else
	docker buildx use "$builder_name" >/dev/null
fi
docker buildx inspect "$builder_name" --bootstrap >/dev/null

build_cmd=(
	docker buildx build
	--builder "$builder_name"
	--load
	--progress=plain
	--build-arg "BUILD_HASH=$build_hash"
	--build-arg "USE_EXTERNAL_SERVICES_SLIM=true"
	--cache-to "type=local,dest=$cache_next,mode=max"
	-t "$image_tag"
)

if [[ -e "$cache_current" ]]; then
	build_cmd+=(--cache-from "type=local,src=$cache_current")
fi
if [[ -n "$platform" ]]; then
	build_cmd+=(--platform "$platform")
fi
if [[ -n "$alpine_mirror" ]]; then
	build_cmd+=(--build-arg "ALPINE_MIRROR=$alpine_mirror")
fi
if [[ -n "$apt_debian_mirror" ]]; then
	build_cmd+=(--build-arg "APT_DEBIAN_MIRROR=$apt_debian_mirror")
fi
if [[ -n "$apt_security_mirror" ]]; then
	build_cmd+=(--build-arg "APT_SECURITY_MIRROR=$apt_security_mirror")
fi
if [[ -n "$npm_registry" ]]; then
	build_cmd+=(--build-arg "NPM_REGISTRY=$npm_registry")
fi
if [[ -n "$uv_default_index" ]]; then
	build_cmd+=(--build-arg "UV_DEFAULT_INDEX=$uv_default_index")
fi
if [[ -n "$pyodide_index_url" ]]; then
	build_cmd+=(--build-arg "PYODIDE_INDEX_URL=$pyodide_index_url")
fi
if [[ -n "$pyodide_pypi_api_base_url" ]]; then
	build_cmd+=(--build-arg "PYODIDE_PYPI_API_BASE_URL=$pyodide_pypi_api_base_url")
fi
if [[ -n "$pyodide_pypi_files_base_url" ]]; then
	build_cmd+=(--build-arg "PYODIDE_PYPI_FILES_BASE_URL=$pyodide_pypi_files_base_url")
fi
if [[ -n "$pyodide_pypi_index_urls" ]]; then
	build_cmd+=(--build-arg "PYODIDE_PYPI_INDEX_URLS=$pyodide_pypi_index_urls")
fi

env_args=()
if [[ -n "$proxy_url" ]]; then
	env_args+=(
		"HTTP_PROXY=$proxy_url"
		"HTTPS_PROXY=$proxy_url"
		"ALL_PROXY=$proxy_url"
		"NO_PROXY=localhost,127.0.0.1"
	)
	build_cmd+=(
		--build-arg "HTTP_PROXY=$proxy_url"
		--build-arg "HTTPS_PROXY=$proxy_url"
		--build-arg "ALL_PROXY=$proxy_url"
		--build-arg "NO_PROXY=localhost,127.0.0.1"
	)
fi

build_cmd+=("$context_dir")

mv "$log_path" "$log_path.prev.$(date +%s)" 2>/dev/null || true
printf 'build_hash=%s\nimage_tag=%s\ncache_next=%s\n' "$build_hash" "$image_tag" "$cache_next" > "$log_path"

env "${env_args[@]}" "${build_cmd[@]}" >> "$log_path" 2>&1

if [[ -L "$cache_current" || ! -e "$cache_current" ]]; then
	ln -sfn "$cache_next" "$cache_current.next"
	mv -Tf "$cache_current.next" "$cache_current"
else
	mv "$cache_current" "$cache_current.previous.$(date +%Y%m%d%H%M%S)"
	ln -s "$cache_next" "$cache_current"
fi

echo "IMAGE=$image_tag"
echo "LOG=$log_path"
echo "CACHE_CURRENT=$cache_current"
EOF
} | ssh "$REMOTE_HOST" bash -s
