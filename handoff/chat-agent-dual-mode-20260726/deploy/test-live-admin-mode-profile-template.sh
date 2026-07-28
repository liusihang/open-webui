#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
TEMPLATE_FILE=${TEMPLATE_FILE:-${SCRIPT_DIR}/live-admin-mode-profile-template.json}

jq -e . "${TEMPLATE_FILE}" >/dev/null

if ! jq -e '
  .chat.profile.system_prompt == "" and
  .chat.profile.defaults == {
    "terminal_id": null,
    "tool_ids": [],
    "skill_ids": [],
    "filter_ids": "inherit",
    "feature_ids": "inherit"
  }
' "${TEMPLATE_FILE}" >/dev/null; then
  echo chat_defaults_mismatch
  exit 1
fi

if ! jq -e '
  .agent.profile.system_prompt == "" and
  .agent.profile.defaults == {
    "terminal_id": "terminals",
    "tool_ids": ["sub_agent"],
    "skill_ids": [],
    "filter_ids": "inherit",
    "feature_ids": "inherit"
  }
' "${TEMPLATE_FILE}" >/dev/null; then
  echo agent_defaults_mismatch
  exit 1
fi

if jq -e '.. | objects | keys[] | select(test("model|reasoning"; "i"))' "${TEMPLATE_FILE}" >/dev/null; then
  echo forbidden_model_or_reasoning_field
  exit 1
fi

printf 'live_profile_template=accepted_latest_stack_defaults\n'
