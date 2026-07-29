#!/usr/bin/env bash
set -euo pipefail

source_root='/Users/liusihang/openwebui/.worktrees/v011-upstream-integration-base'
source_commit='4934cdf59bbf2d7661d138d7dc7959bd83e93dfb'
short_commit='4934cdf59bbf'
evidence_root="${source_root}/output/playwright/v011-chatgpt-answer-typography-20260729"
dockerfile="${source_root}/docs/upgrades/v0.11.0-integration/deployment/Dockerfile.hotpatch-${short_commit}"
artifact="${evidence_root}/openwebui-v011-hotfix-${short_commit}-context.tar.gz"
context_dir="$(mktemp -d /tmp/openwebui-v011-hotpatch-${short_commit}.XXXXXX)"

cleanup() {
	rm -rf -- "${context_dir}"
}
trap cleanup EXIT

[[ "$(git -C "${source_root}" rev-parse HEAD)" == "${source_commit}" ]]
test -s "${dockerfile}"
test -s "${source_root}/build/index.html"
grep -F -q "${source_commit}" "${source_root}/build/_app/version.json"
install -d -m 700 "${evidence_root}"
test ! -e "${artifact}"

export COPYFILE_DISABLE=1
install -d -m 700 "${context_dir}/build"
cp -R "${source_root}/build/." "${context_dir}/build/"
cp "${dockerfile}" "${context_dir}/Dockerfile.hotpatch-${short_commit}"
xattr -cr "${context_dir}"

tar --no-xattrs -czf "${artifact}" -C "${context_dir}" .
sha256sum "${artifact}"
du -h "${artifact}"
