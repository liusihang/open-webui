#!/usr/bin/env bash
set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/switch-pr7-4a4e43e206.sh"

bash -n "$SCRIPT"
source_text=$(cat "$SCRIPT")

grep -Fq 'probe_target_config_schema >"$BACKUP_DIR/target-config-probe.txt"' <<<"$source_text"
grep -Fq 'backup_runtime_state' <<<"$source_text"
grep -Fq 'restore_runtime_state' <<<"$source_text"
grep -Fq 'test "$(runtime_schema_version "$OLD_RUNTIME_IMAGE")" = 1' <<<"$source_text"
grep -Fq 'test "$(runtime_schema_version "$RUNTIME_IMAGE")" = 2' <<<"$source_text"
grep -Fq '"${target_compose[@]}" stop -t 30 open-webui-pr7 agentscope-runtime' <<<"$source_text"
grep -Fq 'run_alembic "downgrade $OLD_MIGRATION_HEAD"' <<<"$source_text"
grep -Fq 'state=rollback_failed' <<<"$source_text"

probe_line=$(grep -n 'probe_target_config_schema >' "$SCRIPT" | cut -d: -f1)
migrate_line=$(grep -n "run_alembic 'upgrade head'" "$SCRIPT" | cut -d: -f1)
backup_line=$(grep -n '^backup_runtime_state$' "$SCRIPT" | cut -d: -f1)
switch_line=$(grep -n '^runtime_state_may_change=1$' "$SCRIPT" | cut -d: -f1)

test "$probe_line" -lt "$migrate_line"
test "$backup_line" -lt "$switch_line"

if grep -Eq 'docker (system|builder) prune|docker compose .* down|docker rm' <<<"$source_text"; then
  echo 'switch script contains destructive broad Docker operations' >&2
  exit 1
fi
