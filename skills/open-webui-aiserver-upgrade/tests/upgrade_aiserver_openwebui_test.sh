#!/usr/bin/env bash
set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")"/.. && pwd)/scripts/upgrade_aiserver_openwebui.sh"
SKILL_DOC="$(cd "$(dirname "${BASH_SOURCE[0]}")"/.. && pwd)/SKILL.md"

if [[ ! -x "$SCRIPT" ]]; then
	echo "script is missing or not executable: $SCRIPT" >&2
	exit 1
fi

if [[ ! -f "$SKILL_DOC" ]]; then
	echo "skill doc is missing: $SKILL_DOC" >&2
	exit 1
fi

help_output="$("$SCRIPT" --help)"

grep -q "inspect" <<<"$help_output"
grep -q "build-only" <<<"$help_output"
grep -q "switch-image" <<<"$help_output"
grep -q -- "--commit <git-ref>" <<<"$help_output"
grep -q -- "--image-tag <tag>" <<<"$help_output"
grep -q -- "--proxy-url <url>" <<<"$help_output"

script_source="$(cat "$SCRIPT")"
skill_source="$(cat "$SKILL_DOC")"

grep -q "CYPRESS_INSTALL_BINARY=0" <<<"$script_source"
grep -q "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1" <<<"$script_source"
grep -q "PUPPETEER_SKIP_DOWNLOAD=true" <<<"$script_source"
grep -q "ONNXRUNTIME_NODE_INSTALL_CUDA=skip" <<<"$script_source"
grep -q "mirrors.tuna.tsinghua.edu.cn" <<<"$script_source"
grep -q "registry.npmmirror.com" <<<"$script_source"
grep -q "seq 1 60" <<<"$script_source"
grep -q "docker buildx build" <<<"$script_source"
grep -q -- "--load" <<<"$script_source"
grep -q -- "--progress=plain" <<<"$script_source"
grep -q "docker buildx inspect" <<<"$script_source"
grep -q "docker compose -p" <<<"$script_source"
grep -q "local remote_script_path" <<<"$script_source"
grep -Eq 'cat >\\?"\$remote_script_path\\?" && chmod \+x \\?"\$remote_script_path\\?"' <<<"$script_source"
if grep -q 'cat >/tmp/openwebui_aiserver_upgrade.\$\$\.\$RANDOM.sh && chmod +x /tmp/openwebui_aiserver_upgrade.\$\$\.\$RANDOM.sh' <<<"$script_source"; then
	echo "remote_sudo_script still uses mismatched inline random temp paths" >&2
	exit 1
fi

grep -q "session" <<<"$skill_source"
grep -q "unified_tool_mcp_router_filter" <<<"$skill_source"
grep -q "CMD" <<<"$skill_source"
