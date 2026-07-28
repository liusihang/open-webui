#!/usr/bin/env bash
set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/rebuild-pr7-slim-cache.sh"

if [[ ! -x "$SCRIPT" ]]; then
	echo "script is missing or not executable: $SCRIPT" >&2
	exit 1
fi

help_output="$("$SCRIPT" --help)"
script_source="$(cat "$SCRIPT")"

grep -q "Usage:" <<<"$help_output"
grep -q -- "--remote <host>" <<<"$help_output"
grep -q -- "--image-tag <tag>" <<<"$help_output"
grep -q -- "--cache-dir <dir>" <<<"$help_output"
grep -q -- "--build-dir <dir>" <<<"$help_output"
grep -q -- "--git-ref <ref>" <<<"$help_output"
grep -q -- "--alpine-mirror <url>" <<<"$help_output"
grep -q -- "--pyodide-pypi-api-base-url <url>" <<<"$help_output"
grep -q -- "--pyodide-pypi-files-base-url <url>" <<<"$help_output"
grep -q -- "--pyodide-pypi-index-urls <csv>" <<<"$help_output"
grep -q -- "--pyodide-index-url <url>" <<<"$help_output"
grep -q -- "--seed-pyodide-dir <dir>" <<<"$help_output"
grep -q -- "--dockerfile-syntax-image <image>" <<<"$help_output"
grep -q -- "--node-base-image <image>" <<<"$help_output"
grep -q -- "--python-base-image <image>" <<<"$help_output"

grep -q "git archive" <<<"$script_source"
grep -q "docker buildx build" <<<"$script_source"
grep -q -- "--cache-from" <<<"$script_source"
grep -q -- "--cache-to" <<<"$script_source"
grep -q "type=local" <<<"$script_source"
grep -q "mode=max" <<<"$script_source"
grep -q "USE_EXTERNAL_SERVICES_SLIM=true" <<<"$script_source"
grep -q "BUILD_HASH" <<<"$script_source"
grep -q "mv .*cache" <<<"$script_source"
grep -q 'ssh "\$REMOTE_HOST" bash -s' <<<"$script_source"
grep -q 'ssh "\$REMOTE_HOST" "mkdir -p -- .*\$BUILD_DIR' <<<"$script_source"
grep -q "overlay_seed_pyodide_dir" <<<"$script_source"
grep -q "patch_remote_dockerfile" <<<"$script_source"
grep -q "ALPINE_MIRROR" <<<"$script_source"
grep -q "PYODIDE_INDEX_URL" <<<"$script_source"
grep -q "PYODIDE_PYPI_API_BASE_URL" <<<"$script_source"
grep -q "PYODIDE_PYPI_FILES_BASE_URL" <<<"$script_source"
grep -q "PYODIDE_PYPI_INDEX_URLS" <<<"$script_source"

if grep -Eq "docker (compose )?(stop|restart|up|down)|docker rm|docker system prune|docker builder prune" <<<"$script_source"; then
	echo "script contains forbidden live/destructive docker operations" >&2
	exit 1
fi
