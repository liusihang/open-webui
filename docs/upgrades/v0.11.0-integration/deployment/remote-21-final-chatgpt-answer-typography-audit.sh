#!/usr/bin/env bash
set -euo pipefail

web_container='open-webui-pr7'
formal_container='open-webui'
expected_web_image='sha256:e6749eb2fc8a4222a1a8965318abcc322a055e6f7a75f303e5e41bddb73505bb'
expected_formal_image='sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b'
expected_source='7684618281df7a9adbd4217d127c3abb284cc261'
admin_id='b6826286-1251-4576-b3a0-e109ff085a61'
evidence_dir='/home/aiserver/staging/openwebui-pr7-eea11194ed-test/evidence/v011-pre-v011-answer-typography-sidebar-7684618281df-r4-20260729-183400'
staged_functional_probe='/tmp/container-functional-api-probe.py'
staged_chat_probe='/tmp/container-chatgpt-answer-typography-api-probe.py'

[[ "$(docker inspect --format '{{.Image}}' "${web_container}")" == "${expected_web_image}" ]]
[[ "$(docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "${web_container}")" == "${expected_source}" ]]
[[ "$(docker inspect --format '{{.State.Health.Status}}' "${web_container}")" == 'healthy' ]]
[[ "$(docker inspect --format '{{.RestartCount}}' "${web_container}")" == '0' ]]
[[ "$(docker inspect --format '{{.State.OOMKilled}}' "${web_container}")" == 'false' ]]
[[ "$(docker inspect --format '{{.Image}}' "${formal_container}")" == "${expected_formal_image}" ]]
[[ "$(docker inspect --format '{{.State.Health.Status}}' "${formal_container}")" == 'healthy' ]]
[[ "$(docker inspect --format '{{.RestartCount}}' "${formal_container}")" == '0' ]]
test -s "${evidence_dir}/DEPLOY_OK"
test -s "${staged_functional_probe}"
test -s "${staged_chat_probe}"

port="$(docker inspect --format '{{(index (index .NetworkSettings.Ports "8080/tcp") 0).HostPort}}' "${web_container}")"
health="$(curl --noproxy '*' --fail --silent --show-error --max-time 15 "http://127.0.0.1:${port}/health")"
health_db="$(curl --noproxy '*' --fail --silent --show-error --max-time 15 "http://127.0.0.1:${port}/health/db")"
version="$(curl --noproxy '*' --fail --silent --show-error --max-time 15 "http://127.0.0.1:${port}/api/version")"
frontend_version="$(curl --noproxy '*' --fail --silent --show-error --max-time 15 "http://127.0.0.1:${port}/_app/version.json")"
python3 - "${health}" "${health_db}" "${version}" "${frontend_version}" "${expected_source}" <<'PY'
import json
import sys

assert json.loads(sys.argv[1]) == {"status": True}
assert json.loads(sys.argv[2]) == {"status": True}
assert json.loads(sys.argv[3]).get("version") == "0.11.0"
assert json.loads(sys.argv[4]).get("version") == sys.argv[5]
PY

docker exec "${web_container}" grep -R -F -q 'max-width:58rem' /app/build/_app/immutable/assets
docker exec "${web_container}" grep -R -F -q 'white-space:pre-line' /app/build/_app/immutable/assets
docker exec "${web_container}" grep -R -F -q 'max-width:none' /app/build/_app/immutable/assets
docker exec "${web_container}" grep -R -F -q 'border-inline-start-width:2px' /app/build/_app/immutable/assets
docker exec "${web_container}" grep -R -F -q 'margin-block:calc(var(--spacing,.25rem) * 0)' /app/build/_app/immutable/assets
! docker exec "${web_container}" grep -R -F -q 'sidebar-openclaw-button' /app/build/_app/immutable

docker cp "${staged_functional_probe}" "${web_container}:/tmp/container-functional-api-probe.py"
docker cp "${staged_chat_probe}" "${web_container}:/tmp/container-chatgpt-answer-typography-api-probe.py"
docker exec -e PYTHONPATH=/app/backend "${web_container}" python /tmp/container-functional-api-probe.py "${admin_id}" >"${evidence_dir}/functional-api.json"
docker exec -e PYTHONPATH=/app/backend "${web_container}" python /tmp/container-chatgpt-answer-typography-api-probe.py "${admin_id}" >"${evidence_dir}/persisted-chat-api.json"
python3 - "${evidence_dir}/functional-api.json" "${evidence_dir}/persisted-chat-api.json" <<'PY'
from pathlib import Path
import json
import sys

functional = json.loads(Path(sys.argv[1]).read_text())
chat = json.loads(Path(sys.argv[2]).read_text())
assert functional.get("ok") is True
assert chat.get("ok") is True
assert chat.get("chat_list_count", 0) > 0
assert chat.get("message_count", -1) >= 0
PY

started_at="$(cat "${evidence_dir}/started-at.txt")"
docker logs --since "${started_at}" --timestamps "${web_container}" >"${evidence_dir}/container-final.log" 2>&1
fatal_pattern='Traceback|worker.*(died|exited)|segmentation fault|out of memory|Task exception was never retrieved| HTTP/[^ ]+" 5[0-9][0-9] '
if grep -Eiq "${fatal_pattern}" "${evidence_dir}/container-final.log"; then
	grep -Ein "${fatal_pattern}" "${evidence_dir}/container-final.log" >"${evidence_dir}/fatal-log-signals.txt"
	exit 1
fi

{
	printf 'test_image=%s\n' "${expected_web_image}"
	printf 'test_source=%s\n' "${expected_source}"
	printf 'test_health=healthy\n'
	printf 'test_restart_count=0\n'
	printf 'formal_image=%s\n' "${expected_formal_image}"
	printf 'formal_health=healthy\n'
	printf 'formal_restart_count=0\n'
	printf 'runtime_css=pre-v011-prose-rhythm-58rem\n'
	printf 'sidebar_openclaw_asset=absent\n'
	printf 'functional_api=pass\n'
	printf 'persisted_chat_api=pass\n'
	printf 'recent_fatal_or_5xx=0\n'
	printf 'audited_at=%s\n' "$(date --iso-8601=seconds)"
} >"${evidence_dir}/FINAL_AUDIT_OK"

find "${evidence_dir}" -maxdepth 1 -type f ! -name manifest.sha256 -print0 \
	| sort -z \
	| xargs -0 sha256sum >"${evidence_dir}/manifest.sha256"
cat "${evidence_dir}/persisted-chat-api.json"
cat "${evidence_dir}/FINAL_AUDIT_OK"
sha256sum "${evidence_dir}/manifest.sha256"
