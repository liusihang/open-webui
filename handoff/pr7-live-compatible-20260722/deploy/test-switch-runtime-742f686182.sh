#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/switch-runtime-742f686182.sh"
source_text=$(<"$SCRIPT")

bash -n "$SCRIPT"
grep -Fq 'RECENT_ACTIVE_WINDOW_NS=600000000000' <<<"$source_text"
grep -Fq 'backup_runtime_state' <<<"$source_text"
grep -Fq 'test "$(runtime_schema_version "$OLD_RUNTIME_IMAGE")" = 2' <<<"$source_text"
grep -Fq 'test "$(runtime_schema_version "$RUNTIME_IMAGE")" = 2' <<<"$source_text"
grep -Fq 'up -d --no-deps --force-recreate agentscope-runtime' <<<"$source_text"
test "$(grep -c 'force-recreate open-webui-pr7' <<<"$source_text")" = 0
test "$(grep -c 'restore_runtime_state' <<<"$source_text")" = 0
grep -Fq 'state=rollback_failed' <<<"$source_text"
grep -Fq 'before_webui=' <<<"$source_text"
grep -Fq 'before_db=' <<<"$source_text"
grep -Fq 'before_redis=' <<<"$source_text"
grep -Fq 'before_terminals=' <<<"$source_text"
grep -Fq 'before_main=' <<<"$source_text"

printf 'runtime switch contract passed\n'
