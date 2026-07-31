#!/usr/bin/env bash
set -euo pipefail

source_root="/Users/liusihang/openwebui/.worktrees/v011-upstream-integration-base"
source_commit="9643bd7ad189ddc1e65fd6996d8b5c047e6e06e8"
evidence_root="${source_root}/output/playwright/v011-tool-call-dropdown-20260729"
dockerfile="${source_root}/docs/upgrades/v0.11.0-integration/deployment/Dockerfile.hotpatch-9643bd7ad189"
artifact="${evidence_root}/openwebui-v011-hotfix-9643bd7ad189-context.tar.gz"
context_dir="$(mktemp -d /tmp/openwebui-v011-hotpatch-9643bd7ad189.XXXXXX)"

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
cp "${dockerfile}" "${context_dir}/Dockerfile.hotpatch-9643bd7ad189"
xattr -cr "${context_dir}"

tar --no-xattrs -czf "${artifact}" -C "${context_dir}" .
sha256sum "${artifact}"
du -h "${artifact}"
