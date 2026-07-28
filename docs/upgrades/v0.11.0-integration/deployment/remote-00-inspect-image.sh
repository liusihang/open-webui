#!/usr/bin/env bash
set -euo pipefail

IMAGE='open-webui:v011-test-4d3543438b-slim'
EXPECTED_SOURCE='4d3543438b6b147ae60f17a9b57b2355a0a026d0'
EXPECTED_BUILD='4d3543438b'
BUILD_DIR='/home/aiserver/staging/openwebui-v011-4d3543438b-build'
STATE_DIR='/home/aiserver/staging/openwebui-pr7-eea11194ed-test/deploy-v011-4d3543438b'

mkdir -p "${STATE_DIR}"
chmod 700 "${STATE_DIR}"

image_id="$(docker image inspect --format '{{.Id}}' "${IMAGE}")"
source_revision="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "${IMAGE}")"
build_version="$(docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${IMAGE}" | awk -F= '$1 == "WEBUI_BUILD_VERSION" {print substr($0, index($0, "=") + 1)}')"
created="$(docker image inspect --format '{{.Created}}' "${IMAGE}")"
size="$(docker image inspect --format '{{.Size}}' "${IMAGE}")"

[[ "${source_revision}" == "${EXPECTED_SOURCE}" ]]
[[ "${build_version}" == "${EXPECTED_BUILD}" ]]
test -s "${BUILD_DIR}/status.env"
grep -Fx 'state=complete' "${BUILD_DIR}/status.env" >/dev/null
grep -Fx 'detail=verified' "${BUILD_DIR}/status.env" >/dev/null
grep -Fx "source_sha=${EXPECTED_SOURCE}" "${BUILD_DIR}/status.env" >/dev/null
grep -Fx "image_tag=${IMAGE}" "${BUILD_DIR}/status.env" >/dev/null

{
  printf 'image=%s\n' "${IMAGE}"
  printf 'image_id=%s\n' "${image_id}"
  printf 'source=%s\n' "${source_revision}"
  printf 'build=%s\n' "${build_version}"
  printf 'created=%s\n' "${created}"
  printf 'size_bytes=%s\n' "${size}"
  printf 'verified_at=%s\n' "$(date --iso-8601=seconds)"
} >"${STATE_DIR}/IMAGE_OK"

cat "${STATE_DIR}/IMAGE_OK"
