#!/usr/bin/env bash
set -Eeuo pipefail

RELEASE_DIR=${RELEASE_DIR:-/home/aiserver/staging/pr7-live-prep-20260727/release}
TEMPLATE_FILE=${TEMPLATE_FILE:-${RELEASE_DIR}/live-admin-mode-profile-template.json}
INVENTORY_SCRIPT=${INVENTORY_SCRIPT:-${RELEASE_DIR}/live-db-resource-inventory.sh}

if ! jq -e . "${TEMPLATE_FILE}" >/dev/null; then
  echo profile_template_invalid_json
  exit 1
fi
if jq -e '.. | objects | keys[] | select(test("model|reasoning"; "i"))' "${TEMPLATE_FILE}" >/dev/null; then
  echo forbidden_model_or_reasoning_field
  exit 1
fi
if jq -e '.[] | .profile | .. | strings | select(startswith("__"))' "${TEMPLATE_FILE}" >/dev/null; then
  echo unresolved_profile_placeholder
  exit 1
fi
if ! jq -e '.chat.expected_current_revision_id == "__READ_AFTER_UPGRADE__" and .agent.expected_current_revision_id == "__READ_AFTER_UPGRADE__"' "${TEMPLATE_FILE}" >/dev/null; then
  echo dynamic_head_placeholder_mismatch
  exit 1
fi

inventory=$("${INVENTORY_SCRIPT}")

assert_resource() {
  local resource_type=$1
  local resource_id=$2
  local expected_subtype=${3:-}
  local row
  local active
  local subtype
  row=$(awk -F '\t' -v resource_type="${resource_type}" -v resource_id="${resource_id}" '$1 == resource_type && $2 == resource_id {print; exit}' <<< "${inventory}")
  if [[ -z "${row}" ]]; then
    printf 'missing_resource=%s:%s\n' "${resource_type}" "${resource_id}"
    exit 1
  fi
  active=$(awk -F '\t' '{print $5}' <<< "${row}")
  subtype=$(awk -F '\t' '{print $4}' <<< "${row}")
  if [[ "${active}" != t ]]; then
    printf 'inactive_resource=%s:%s\n' "${resource_type}" "${resource_id}"
    exit 1
  fi
  if [[ -n "${expected_subtype}" && "${subtype}" != "${expected_subtype}" ]]; then
    printf 'wrong_resource_subtype=%s:%s:%s\n' "${resource_type}" "${resource_id}" "${subtype}"
    exit 1
  fi
}

for mode in chat agent; do
  terminal_id=$(jq -r --arg mode "${mode}" '.[$mode].profile.defaults.terminal_id | select(type == "string" and . != "inherit")' "${TEMPLATE_FILE}")
  if [[ -n "${terminal_id}" ]]; then
    assert_resource terminal "${terminal_id}" terminal
  fi

  while IFS= read -r tool_id; do
    [[ -z "${tool_id}" ]] || assert_resource tool "${tool_id}" tool
  done < <(jq -r --arg mode "${mode}" '.[$mode].profile.defaults.tool_ids | select(type == "array") | .[]' "${TEMPLATE_FILE}")

  while IFS= read -r skill_id; do
    [[ -z "${skill_id}" ]] || assert_resource skill "${skill_id}" skill
  done < <(jq -r --arg mode "${mode}" '.[$mode].profile.defaults.skill_ids | select(type == "array") | .[]' "${TEMPLATE_FILE}")

  while IFS= read -r filter_id; do
    [[ -z "${filter_id}" ]] || assert_resource function "${filter_id}" filter
  done < <(jq -r --arg mode "${mode}" '.[$mode].profile.defaults.filter_ids | select(type == "array") | .[]' "${TEMPLATE_FILE}")
done

printf 'profile_template=valid\n'
printf 'chat_defaults=explicit_empty_capabilities\n'
printf 'agent_resources=present_and_active\n'
