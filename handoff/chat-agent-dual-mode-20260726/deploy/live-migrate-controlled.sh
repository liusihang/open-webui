#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

MIGRATION_ACTION=${MIGRATION_ACTION:-current}
STACK_DIR=${STACK_DIR:-/srv/openwebui-migration}
WEB_CONTAINER=${WEB_CONTAINER:-open-webui}
DB_CONTAINER=${DB_CONTAINER:-openwebui-db}
CANDIDATE_IMAGE=${CANDIDATE_IMAGE:-open-webui:pr7-chat-agent-dual-mode-1d8dba8a7-slim}
EXPECTED_CANDIDATE_IMAGE_ID=${EXPECTED_CANDIDATE_IMAGE_ID:-sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b}
EXPECTED_CANDIDATE_REVISION=${EXPECTED_CANDIDATE_REVISION:-1d8dba8a77e6e8adc5952891bac83a2a7c5a4804}
SOURCE_REVISION=${SOURCE_REVISION:-f3a4b5c6d7e8}
TARGET_REVISION=${TARGET_REVISION:-c0d3b4a5e6f7}
PREP_ROOT=${PREP_ROOT:-/home/aiserver/staging/pr7-live-prep-20260727}
MAX_BACKUP_AGE_SECONDS=${MAX_BACKUP_AGE_SECONDS:-3600}

mkdir -p "${PREP_ROOT}"
env_file=$(mktemp "${PREP_ROOT}/migration-env.XXXXXX")
trap 'rm -f "${env_file}"' EXIT

db_user=
db_name=
while IFS= read -r entry; do
  case "${entry}" in
    POSTGRES_USER=*) db_user=${entry#*=} ;;
    POSTGRES_DB=*) db_name=${entry#*=} ;;
  esac
done < <(docker inspect "${DB_CONTAINER}" --format '{{range .Config.Env}}{{println .}}{{end}}')
if [[ -z "${db_user}" || -z "${db_name}" ]]; then
  echo database_identity_missing
  exit 1
fi

database_revision() {
  docker exec -i "${DB_CONTAINER}" psql -X -v ON_ERROR_STOP=1 -U "${db_user}" -d "${db_name}" -At <<'SQL'
SELECT version_num FROM alembic_version;
SQL
}

candidate_image_id=$(docker image inspect "${CANDIDATE_IMAGE}" --format '{{.Id}}')
candidate_revision=$(docker image inspect "${CANDIDATE_IMAGE}" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
if [[ "${candidate_image_id}" != "${EXPECTED_CANDIDATE_IMAGE_ID}" || "${candidate_revision}" != "${EXPECTED_CANDIDATE_REVISION}" ]]; then
  echo candidate_identity_mismatch
  exit 1
fi

web_networks=$(docker inspect "${WEB_CONTAINER}" --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{println}}{{end}}')
db_networks=$(docker inspect "${DB_CONTAINER}" --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{println}}{{end}}')
shared_network=
while IFS= read -r network; do
  if [[ -n "${network}" ]] && grep -Fxq "${network}" <<< "${db_networks}"; then
    shared_network=${network}
    break
  fi
done <<< "${web_networks}"
if [[ -z "${shared_network}" ]]; then
  echo shared_database_network_missing
  exit 1
fi

docker inspect "${WEB_CONTAINER}" --format '{{range .Config.Env}}{{println .}}{{end}}' | awk '!/^ENABLE_DB_MIGRATIONS=/' > "${env_file}"
printf 'ENABLE_DB_MIGRATIONS=false\n' >> "${env_file}"

run_alembic() {
  docker run --rm \
    --network "${shared_network}" \
    --env-file "${env_file}" \
    --entrypoint alembic \
    --workdir /app/backend/open_webui \
    "${CANDIDATE_IMAGE}" "$@"
}

current_revision=$(database_revision)
printf 'current_revision=%s\n' "${current_revision}"

case "${MIGRATION_ACTION}" in
  current)
    run_alembic current
    ;;
  upgrade)
    if [[ "${CONFIRM_LIVE_DATABASE_MIGRATION:-}" != "upgrade-f3-to-c0-on-aiserver-live" ]]; then
      echo live_upgrade_confirmation_missing
      exit 1
    fi
    if [[ "${current_revision}" != "${SOURCE_REVISION}" ]]; then
      echo unexpected_upgrade_source_revision
      exit 1
    fi
    backup_manifest=${BACKUP_MANIFEST:?BACKUP_MANIFEST is required}
    dump_file=$(awk -F= '$1 == "dump_file" {sub(/^[^=]*=/, ""); print; exit}' "${backup_manifest}")
    expected_sha256=$(awk -F= '$1 == "dump_sha256" {print $2; exit}' "${backup_manifest}")
    if [[ -z "${dump_file}" || ! -f "${dump_file}" ]]; then
      echo backup_dump_missing
      exit 1
    fi
    actual_sha256=$(sha256sum "${dump_file}" | awk '{print $1}')
    if [[ "${actual_sha256}" != "${expected_sha256}" ]]; then
      echo backup_checksum_mismatch
      exit 1
    fi
    backup_age=$(( $(date +%s) - $(stat --format '%Y' "${dump_file}") ))
    if (( backup_age > MAX_BACKUP_AGE_SECONDS )); then
      printf 'backup_too_old_seconds=%s\n' "${backup_age}"
      exit 1
    fi
    docker exec -i "${DB_CONTAINER}" pg_restore --list < "${dump_file}" >/dev/null
    run_alembic upgrade "${TARGET_REVISION}"
    if [[ "$(database_revision)" != "${TARGET_REVISION}" ]]; then
      echo upgrade_revision_verification_failed
      exit 1
    fi
    ;;
  downgrade)
    if [[ "${CONFIRM_LIVE_DATABASE_MIGRATION:-}" != "downgrade-c0-to-f3-on-aiserver-live" ]]; then
      echo live_downgrade_confirmation_missing
      exit 1
    fi
    if [[ "${CONFIRM_ROLLBACK_DATA_LOSS:-}" != "drop-new-agent-and-mode-profile-schema" ]]; then
      echo rollback_data_ack_missing
      exit 1
    fi
    if [[ "${current_revision}" != "${TARGET_REVISION}" ]]; then
      echo unexpected_downgrade_source_revision
      exit 1
    fi
    run_alembic downgrade "${SOURCE_REVISION}"
    if [[ "$(database_revision)" != "${SOURCE_REVISION}" ]]; then
      echo downgrade_revision_verification_failed
      exit 1
    fi
    ;;
  *)
    echo invalid_migration_action
    exit 1
    ;;
esac

printf 'final_revision=%s\n' "$(database_revision)"
