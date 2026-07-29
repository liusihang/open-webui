#!/usr/bin/env bash
set -euo pipefail

action="${1:?usage: remote-22-manage-chatgpt-answer-typography-browser-fixture.sh create|share|repair|cleanup}"
web_container='open-webui-pr7'
expected_web_image='sha256:e6749eb2fc8a4222a1a8965318abcc322a055e6f7a75f303e5e41bddb73505bb'
staged_probe='/tmp/container-chatgpt-answer-typography-browser-fixture.py'
remote_password_file='/tmp/openwebui-chatgpt-answer-typography-e2e-password.txt'
evidence_dir='/home/aiserver/staging/openwebui-pr7-eea11194ed-test/evidence/v011-pre-v011-answer-typography-sidebar-7684618281df-r4-20260729-183400'
fixture_json="${evidence_dir}/browser-fixture.json"
cleanup_json="${evidence_dir}/browser-fixture-cleanup.json"
share_json="${evidence_dir}/browser-fixture-share.json"
repair_json="${evidence_dir}/browser-fixture-repair.json"

[[ "$(docker inspect --format '{{.Image}}' "${web_container}")" == "${expected_web_image}" ]]
[[ "$(docker inspect --format '{{.State.Health.Status}}' "${web_container}")" == 'healthy' ]]
[[ "$(docker inspect --format '{{.RestartCount}}' "${web_container}")" == '0' ]]
test -s "${staged_probe}"
docker cp "${staged_probe}" "${web_container}:/tmp/container-chatgpt-answer-typography-browser-fixture.py"

case "${action}" in
	create)
		test ! -e "${fixture_json}"
		test ! -e "${remote_password_file}"
		umask 077
		python3 - "${remote_password_file}" <<'PY'
from pathlib import Path
import secrets
import sys

password = f'Aa1!{secrets.token_urlsafe(30)}'
Path(sys.argv[1]).write_text(password, encoding='utf-8')
PY
		password="$(cat "${remote_password_file}")"
		docker exec -e PYTHONPATH=/app/backend "${web_container}" \
			python /tmp/container-chatgpt-answer-typography-browser-fixture.py create "${password}" \
			>"${fixture_json}"
		chmod 600 "${fixture_json}" "${remote_password_file}"
		cat "${fixture_json}"
		;;
	share)
		test -s "${fixture_json}"
		read -r user_id chat_id < <(python3 - "${fixture_json}" <<'PY'
from pathlib import Path
import json
import sys

fixture = json.loads(Path(sys.argv[1]).read_text())
print(fixture["user_id"], fixture["chat_id"])
PY
)
		docker exec -e PYTHONPATH=/app/backend "${web_container}" \
			python /tmp/container-chatgpt-answer-typography-browser-fixture.py share "${user_id}" "${chat_id}" \
			>"${share_json}"
		chmod 600 "${share_json}"
		cat "${share_json}"
		;;
	repair)
		test -s "${fixture_json}"
		read -r user_id chat_id < <(python3 - "${fixture_json}" <<'PY'
from pathlib import Path
import json
import sys

fixture = json.loads(Path(sys.argv[1]).read_text())
print(fixture["user_id"], fixture["chat_id"])
PY
)
		docker exec -e PYTHONPATH=/app/backend "${web_container}" \
			python /tmp/container-chatgpt-answer-typography-browser-fixture.py repair "${user_id}" "${chat_id}" \
			>"${repair_json}"
		chmod 600 "${repair_json}"
		cat "${repair_json}"
		;;
	cleanup)
		test -s "${fixture_json}"
		user_id="$(python3 - "${fixture_json}" <<'PY'
from pathlib import Path
import json
import sys

print(json.loads(Path(sys.argv[1]).read_text())["user_id"])
PY
)"
		docker exec -e PYTHONPATH=/app/backend "${web_container}" \
			python /tmp/container-chatgpt-answer-typography-browser-fixture.py cleanup "${user_id}" \
			>"${cleanup_json}"
		rm -f -- "${remote_password_file}"
		cat "${cleanup_json}"
		;;
	*)
		printf 'unknown action: %s\n' "${action}" >&2
		exit 2
		;;
esac
