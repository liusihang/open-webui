#!/usr/bin/env bash
set -euo pipefail

DOCKERFILE="$(cd "$(dirname "${BASH_SOURCE[0]}")"/.. && pwd)/Dockerfile"

if [[ ! -f "$DOCKERFILE" ]]; then
	echo "Dockerfile is missing: $DOCKERFILE" >&2
	exit 1
fi

dockerfile_source="$(cat "$DOCKERFILE")"

grep -q '^# syntax=docker/dockerfile:1$' <<<"$dockerfile_source"
grep -q -- '--mount=type=cache,target=/root/.npm' <<<"$dockerfile_source"
grep -q '^ENV NODE_OPTIONS="--max-old-space-size=8192"$' <<<"$dockerfile_source"
grep -q -- '--mount=type=cache,target=/var/cache/apt,sharing=locked' <<<"$dockerfile_source"
grep -q -- '--mount=type=cache,target=/var/lib/apt/lists,sharing=locked' <<<"$dockerfile_source"
grep -q -- '--mount=type=cache,target=/root/.cache/pip' <<<"$dockerfile_source"
grep -q -- '--mount=type=cache,target=/root/.cache/uv' <<<"$dockerfile_source"
grep -q 'USE_EXTERNAL_SERVICES_SLIM' <<<"$dockerfile_source"
grep -q 'requirements-external-slim.txt' <<<"$dockerfile_source"
grep -q 'NLTK_DATA="/usr/local/share/nltk_data"' <<<"$dockerfile_source"
grep -q "raise_on_error=True" <<<"$dockerfile_source"

pyodide_line="$(grep -n 'npm run pyodide:fetch' "$DOCKERFILE" | head -n 1 | cut -d: -f1)"
copy_all_line="$(grep -n '^COPY \. \.$' "$DOCKERFILE" | head -n 1 | cut -d: -f1)"

if [[ -z "$pyodide_line" || -z "$copy_all_line" || "$pyodide_line" -ge "$copy_all_line" ]]; then
	echo "Pyodide prefetch must happen before full source COPY" >&2
	exit 1
fi

nltk_downloads="$(grep -c "nltk.download('punkt_tab'" "$DOCKERFILE")"
if [[ "$nltk_downloads" -ne 1 ]]; then
	echo "Expected exactly one Dockerfile punkt_tab download, found $nltk_downloads" >&2
	exit 1
fi

if grep -Eq 'uv pip install .*--no-cache-dir|pip3 install .*--no-cache-dir' "$DOCKERFILE"; then
	echo "Python dependency install still disables pip/uv cache" >&2
	exit 1
fi
