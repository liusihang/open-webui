#!/usr/bin/env bash
set -euo pipefail

phase="${1:?usage: remote-08-ui-functional-state.sh before|after-chat|after-agent}"
web_container='open-webui-pr7'
formal_container='open-webui'
db_container='openwebui-pr7-db'
db_user='webui_pr7'
database='webui_pr7'
admin_id='b6826286-1251-4576-b3a0-e109ff085a61'
expected_image_id='sha256:a1f90018c256b603644c51c82a15b8018d632246c55511206e68f442c83a7d39'
expected_formal_image_id='sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b'
evidence_dir='/home/aiserver/staging/openwebui-pr7-eea11194ed-test/evidence/v011-ui-functional-93032060d9d5-20260729-111800'

capture_container() {
    local container="$1"
    docker inspect --format '{{.Id}}|{{.Image}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.RestartCount}}' "${container}"
}

db_scalar() {
    local sql="$1"
    docker exec "${db_container}" psql \
        --username "${db_user}" \
        --dbname "${database}" \
        --tuples-only \
        --no-align \
        --set ON_ERROR_STOP=1 \
        --command "${sql}"
}

capture_counts() {
    local output="$1"
    {
        printf 'chat\t%s\n' "$(db_scalar 'SELECT count(*) FROM chat;')"
        printf 'agent_run\t%s\n' "$(db_scalar 'SELECT count(*) FROM agent_run;')"
        printf 'agent_run_event\t%s\n' "$(db_scalar 'SELECT count(*) FROM agent_run_event;')"
    } >"${output}"
}

assert_runtime() {
    [[ "$(docker inspect --format '{{.Image}}' "${web_container}")" == "${expected_image_id}" ]]
    [[ "$(docker inspect --format '{{.State.Health.Status}}' "${web_container}")" == 'healthy' ]]
    [[ "$(docker inspect --format '{{.RestartCount}}' "${web_container}")" == '0' ]]
    [[ "$(docker inspect --format '{{.Image}}' "${formal_container}")" == "${expected_formal_image_id}" ]]
    [[ "$(docker inspect --format '{{.State.Health.Status}}' "${formal_container}")" == 'healthy' ]]
    [[ "$(docker inspect --format '{{.RestartCount}}' "${formal_container}")" == '0' ]]
}

assert_runtime

case "${phase}" in
    before)
        test ! -e "${evidence_dir}"
        install -d -m 700 "${evidence_dir}"
        date --iso-8601=seconds >"${evidence_dir}/started-at.txt"
        capture_container "${web_container}" >"${evidence_dir}/test.before.txt"
        capture_container "${formal_container}" >"${evidence_dir}/formal.before.txt"
        capture_counts "${evidence_dir}/counts.before.tsv"
        cat "${evidence_dir}/counts.before.tsv"
        ;;
    after-chat)
        test -s "${evidence_dir}/counts.before.tsv"
        capture_container "${web_container}" >"${evidence_dir}/test.after-chat.txt"
        capture_container "${formal_container}" >"${evidence_dir}/formal.after-chat.txt"
        capture_counts "${evidence_dir}/counts.after-chat.tsv"
        cmp "${evidence_dir}/counts.before.tsv" "${evidence_dir}/counts.after-chat.tsv"
        cmp "${evidence_dir}/test.before.txt" "${evidence_dir}/test.after-chat.txt"
        cmp "${evidence_dir}/formal.before.txt" "${evidence_dir}/formal.after-chat.txt"
        cat "${evidence_dir}/counts.after-chat.tsv"
        ;;
    after-agent)
        test -s "${evidence_dir}/counts.after-chat.tsv"
        capture_container "${web_container}" >"${evidence_dir}/test.after-agent.txt"
        capture_container "${formal_container}" >"${evidence_dir}/formal.after-agent.txt"
        capture_counts "${evidence_dir}/counts.after-agent.tsv"

        before_chat="$(awk -F '\t' '$1 == "chat" {print $2}' "${evidence_dir}/counts.before.tsv")"
        before_runs="$(awk -F '\t' '$1 == "agent_run" {print $2}' "${evidence_dir}/counts.before.tsv")"
        before_events="$(awk -F '\t' '$1 == "agent_run_event" {print $2}' "${evidence_dir}/counts.before.tsv")"
        after_chat="$(awk -F '\t' '$1 == "chat" {print $2}' "${evidence_dir}/counts.after-agent.tsv")"
        after_runs="$(awk -F '\t' '$1 == "agent_run" {print $2}' "${evidence_dir}/counts.after-agent.tsv")"
        after_events="$(awk -F '\t' '$1 == "agent_run_event" {print $2}' "${evidence_dir}/counts.after-agent.tsv")"
        [[ "${after_chat}" == "${before_chat}" ]]
        [[ "${after_runs}" == "$((before_runs + 1))" ]]

        run_id="$(db_scalar 'SELECT id FROM agent_run ORDER BY created_at DESC LIMIT 1;')"
        run_record="$(db_scalar "SELECT state || '|' || user_id FROM agent_run WHERE id = '${run_id}';")"
        run_event_count="$(db_scalar "SELECT count(*) FROM agent_run_event WHERE run_id = '${run_id}';")"
        event_types="$(db_scalar "SELECT event_type || ':' || count(*) FROM agent_run_event WHERE run_id = '${run_id}' GROUP BY event_type ORDER BY event_type;")"
        [[ "${run_record}" == "completed|${admin_id}" ]]
        [[ "${run_event_count}" -ge 4 ]]
        [[ "${after_events}" == "$((before_events + run_event_count))" ]]
        for required_type in run.running final.started final.delta run.completed; do
            grep -Eq "^${required_type}:[1-9][0-9]*$" <<<"${event_types}"
        done

        printf '%s\n' "${run_id}" >"${evidence_dir}/agent-run-id.txt"
        printf '%s\n' "${event_types}" >"${evidence_dir}/agent-event-types.txt"
        docker logs --since "$(cat "${evidence_dir}/started-at.txt")" --timestamps "${web_container}" >"${evidence_dir}/container.log" 2>&1
        if grep -Eiq 'Traceback|worker.*(died|exited)|segmentation fault|out of memory|Task exception was never retrieved' "${evidence_dir}/container.log"; then
            grep -Ein 'Traceback|worker.*(died|exited)|segmentation fault|out of memory|Task exception was never retrieved' "${evidence_dir}/container.log" >"${evidence_dir}/fatal-log-signals.txt"
            exit 1
        fi

        cmp "${evidence_dir}/test.before.txt" "${evidence_dir}/test.after-agent.txt"
        cmp "${evidence_dir}/formal.before.txt" "${evidence_dir}/formal.after-agent.txt"
        {
            printf 'image_id=%s\n' "${expected_image_id}"
            printf 'test_container=%s\n' "$(capture_container "${web_container}")"
            printf 'formal_container=%s\n' "$(capture_container "${formal_container}")"
            printf 'chat_rows_before=%s\n' "${before_chat}"
            printf 'chat_rows_after=%s\n' "${after_chat}"
            printf 'agent_runs_before=%s\n' "${before_runs}"
            printf 'agent_runs_after=%s\n' "${after_runs}"
            printf 'agent_run_id=%s\n' "${run_id}"
            printf 'agent_run_events=%s\n' "${run_event_count}"
            printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
        } >"${evidence_dir}/FINAL_UI_ACCEPTANCE_OK"
        find "${evidence_dir}" -maxdepth 1 -type f ! -name manifest.sha256 -print0 \
            | sort -z \
            | xargs -0 sha256sum >"${evidence_dir}/manifest.sha256"
        cat "${evidence_dir}/FINAL_UI_ACCEPTANCE_OK"
        cat "${evidence_dir}/agent-event-types.txt"
        sha256sum "${evidence_dir}/manifest.sha256"
        ;;
    *)
        printf 'unknown phase: %s\n' "${phase}" >&2
        exit 2
        ;;
esac
