# Rollback: isolated PR7 native-phase deployment

This procedure is retained for emergency rollback only. It has not been executed because the `79adbeface29` deployment passed protocol, cancellation, browser, health, and protected-anchor acceptance.

## Scope and invariants

- Target only project `openwebui-pr7` in `/home/aiserver/staging/openwebui-pr7-eea11194ed-test`.
- Never run `docker compose down`.
- Never include DB or Redis in an `up` command.
- Never mutate live WebUI port `18080`.
- Stop new test traffic to isolated port `18085` before starting.
- Restore the DB-managed `bifrostapi` function first, then runtime, then WebUI.
- Use `--no-build`: the old runtime override contains `build: null`, which does not clear the stale base build context.

## 1. Restore the original `bifrostapi` function

Run on `aiserver`. Credentials enter the process only from `.test-admin.env`; the script does not print the token, valves, password, or function content.

```bash
cd /home/aiserver/staging/openwebui-pr7-eea11194ed-test
AUDIT="$PWD/deploy-79adbeface29-20260710-163830"
export AUDIT

set -a
. ./.test-admin.env
set +a

python3 - <<'PY'
import hashlib
import json
import os
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:18085"
backup_path = Path(os.environ["AUDIT"]) / "bifrostapi.before.json"
raw = backup_path.read_bytes()

assert hashlib.sha256(raw).hexdigest() == (
    "e9aa846a39d48e39534c85bd2d983b210a8dbe3c0765afb339c40ad3ca38beb9"
)

backup = json.loads(raw)
content = backup["content"].encode("utf-8")
assert backup["id"] == "bifrostapi"
assert hashlib.sha256(content).hexdigest() == (
    "14c3a890456acecd13a21044ea9b5d658aa426f9e2968e8a398e5a899bf19cc8"
)
assert hashlib.md5(content).hexdigest() == "e1ce27be69222990d43373f6a3844ba5"


def api(path, method="GET", body=None, token=None):
    headers = {}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        BASE + path,
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


session = api(
    "/api/v1/auths/signin",
    "POST",
    {
        "email": os.environ["OPENWEBUI_PR7_ADMIN_EMAIL"],
        "password": os.environ["OPENWEBUI_PR7_ADMIN_PASSWORD"],
    },
)
token = session["token"]


def canonical_hash(value):
    data = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


valves_before = api("/api/v1/functions/id/bifrostapi/valves", token=token)
valves_hash = canonical_hash(valves_before)

# FunctionForm accepts only id/name/content/meta. Preserve valves separately.
payload = {key: backup[key] for key in ("id", "name", "content", "meta")}
api(
    "/api/v1/functions/id/bifrostapi/update",
    "POST",
    payload,
    token,
)

current = api("/api/v1/functions/id/bifrostapi", token=token)
if current["is_active"] != backup["is_active"]:
    api("/api/v1/functions/id/bifrostapi/toggle", "POST", token=token)
if current["is_global"] != backup["is_global"]:
    api("/api/v1/functions/id/bifrostapi/toggle/global", "POST", token=token)

after = api("/api/v1/functions/id/bifrostapi", token=token)
valves_after = api("/api/v1/functions/id/bifrostapi/valves", token=token)

for key, expected in backup.items():
    if key != "updated_at":
        assert after[key] == expected, key

assert canonical_hash(valves_after) == valves_hash
assert hashlib.sha256(after["content"].encode()).hexdigest() == (
    "14c3a890456acecd13a21044ea9b5d658aa426f9e2968e8a398e5a899bf19cc8"
)

print(
    "ROLLBACK_FUNCTION_OK "
    "sha256=14c3a890456acecd13a21044ea9b5d658aa426f9e2968e8a398e5a899bf19cc8 "
    f"valves_sha256={valves_hash} "
    f"active={after['is_active']} global={after['is_global']}"
)
PY
```

The backup does not contain valves. The procedure therefore compares their canonical hash before and after rather than overwriting them.

## 2. Restore the previous runtime and WebUI images

```bash
cd /home/aiserver/staging/openwebui-pr7-eea11194ed-test
AUDIT="$PWD/deploy-79adbeface29-20260710-163830"

DC=(
  docker compose
  --env-file .env
  -p openwebui-pr7
  -f compose.yaml
  -f compose.webui-rebuild-eaff69b0d317.yaml
  -f compose.webui-eaff69-no-migrations.yaml
  -f "$AUDIT/compose.agent-runtime-rebuild-4f6cda06d24c.yaml"
  -f "$AUDIT/compose.webui-7e7fd83ca2f7.yaml"
)

test "$(
  docker image inspect -f '{{.Id}}' \
    open-webui-pr7-agentscope-runtime:4f6cda06d24c-userinput
)" = "sha256:24dc094ab74fd1fa0dae52cd16f665fa05df06ab0d110bb01cef0035227f424a"

test "$(
  docker image inspect -f '{{.Id}}' \
    open-webui:agentmode-v0102-7e7fd83ca2f7-slim
)" = "sha256:1d6c1cf367519128b13baca625a435e547b9da9887e024afb4e0405f46eb3f83"

"${DC[@]}" config --services
"${DC[@]}" config --images

"${DC[@]}" up -d \
  --no-deps --force-recreate --no-build \
  agentscope-runtime

# Continue only after runtime is running, healthy, and restart count is zero.
"${DC[@]}" up -d \
  --no-deps --force-recreate --no-build \
  open-webui-pr7
```

Do not use the older `rollback-command-7e7fd83ca2f7.txt` verbatim: it points to `compose.webui-b2e665078056.yaml` and omits the required `--no-build` guard.

## 3. Verify rollback and protected anchors

Expected isolated targets:

- WebUI image `open-webui:agentmode-v0102-7e7fd83ca2f7-slim`, image ID `sha256:1d6c1cf367519128b13baca625a435e547b9da9887e024afb4e0405f46eb3f83`.
- Runtime image `open-webui-pr7-agentscope-runtime:4f6cda06d24c-userinput`, image ID `sha256:24dc094ab74fd1fa0dae52cd16f665fa05df06ab0d110bb01cef0035227f424a`.
- Both isolated services must be running, healthy, and restart count zero.

The live WebUI, isolated DB, and isolated Redis container IDs, image IDs, start times, restart counts, and health values must remain identical to `deploy-79adbeface29-20260710-163830/pre-switch-anchors.txt`.

```bash
curl -fsS http://127.0.0.1:18085/health
curl -fsS http://127.0.0.1:18085/health/db
curl -fsS http://127.0.0.1:18085/api/version

docker inspect -f '{{.Image}} {{.State.Health.Status}} {{.RestartCount}}' \
  openwebui-pr7-agentscope-runtime open-webui-pr7

docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' \
  open-webui-pr7 | \
  grep -E '^(ENABLE_DB_MIGRATIONS=false|UVICORN_WORKERS=1)$'
```

Rollback evidence sources:

- `deploy-79adbeface29-20260710-163830/bifrostapi.before.json`
- `deploy-79adbeface29-20260710-163830/pre-switch-anchors.txt`
- `deploy-79adbeface29-20260710-163830/compose.agent-runtime-rebuild-4f6cda06d24c.yaml`
- `deploy-79adbeface29-20260710-163830/compose.webui-7e7fd83ca2f7.yaml`
