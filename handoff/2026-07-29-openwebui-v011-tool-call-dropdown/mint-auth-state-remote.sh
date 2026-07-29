#!/usr/bin/env bash
set -euo pipefail

container="open-webui-pr7"
helper="/tmp/v011-tool-dropdown-create-auth.py"
state="/tmp/v011-tool-dropdown-auth-state.json"

umask 077
rm -f "${state}"
docker cp "${helper}" "${container}:${helper}"
docker exec -e PYTHONPATH=/app/backend "${container}" python "${helper}" >"${state}"
python3 - "${state}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
assert data["cookies"] == []
assert len(data["origins"]) == 1
assert data["origins"][0]["origin"] == "http://192.168.2.238:18085"
assert [item["name"] for item in data["origins"][0]["localStorage"]] == ["token"]
PY
chmod 600 "${state}"
echo "${state}"
