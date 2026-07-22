from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any


BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:18085").rstrip("/")
ADMIN_USER_ID = os.environ.get(
    "ADMIN_USER_ID",
    "b6826286-1251-4576-b3a0-e109ff085a61",
)
SOURCE_FUNCTION_ID = "bifrostapi"
TARGET_MODEL_SUFFIX = "Cliproxy/gpt-5.4"


def docker_token() -> str:
    script = f'''\
from datetime import timedelta
from open_webui.utils.auth import create_token
print(create_token({{"id": "{ADMIN_USER_ID}"}}, expires_delta=timedelta(hours=2)))
'''
    proc = subprocess.run(
        ["docker", "exec", "-i", "open-webui-pr7", "python", "-"],
        input=script,
        text=True,
        capture_output=True,
        check=True,
    )
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("open-webui-pr7 did not return a JWT")
    return lines[-1]


TOKEN = docker_token()
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}


def request_json(method: str, path: str, body: Any = None, timeout: int = 60) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        BASE_URL + path,
        data=data,
        method=method,
        headers=HEADERS,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed {exc.code}: {raw[:3000]}") from exc


def add_gpt54_fallback(content: str) -> str:
    needle = '''    def _fallback_models(self) -> List[dict]:
        return [
'''
    if needle not in content:
        raise RuntimeError("BifrostAPI fallback model list was not found")
    replacement = needle + '''            {"id": "Cliproxy/gpt-5.4", "name": "Cliproxy/gpt-5.4"},
'''
    return content.replace(needle, replacement, 1)


def main() -> int:
    suffix = uuid.uuid4().hex[:8]
    clone_id = f"bifrostapi_gpt54_test_{suffix}"
    model_id = f"{clone_id}.{TARGET_MODEL_SUFFIX}"
    clone_created = False
    clone_active = False
    child_code = 1
    cleanup_error: str | None = None

    try:
        source = request_json("GET", f"/api/v1/functions/id/{SOURCE_FUNCTION_ID}")
        valves = request_json(
            "GET",
            f"/api/v1/functions/id/{SOURCE_FUNCTION_ID}/valves",
        )
        if not isinstance(source, dict) or not isinstance(valves, dict):
            raise RuntimeError("failed to read BifrostAPI function or valves")
        content = add_gpt54_fallback(str(source.get("content") or ""))
        request_json(
            "POST",
            "/api/v1/functions/create",
            {
                "id": clone_id,
                "name": "Temporary BifrostAPI GPT-5.4 acceptance clone",
                "meta": {
                    "description": "Temporary isolated GPT-5.4 Agent Mode acceptance route."
                },
                "content": content,
            },
        )
        clone_created = True
        request_json(
            "POST",
            f"/api/v1/functions/id/{clone_id}/valves/update",
            valves,
        )
        request_json("POST", f"/api/v1/functions/id/{clone_id}/toggle")
        clone_active = True

        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            models = request_json("GET", "/api/models?refresh=true", timeout=180)
            items = (models.get("data") if isinstance(models, dict) else models) or []
            if model_id in {
                item.get("id") for item in items if isinstance(item, dict)
            }:
                break
            time.sleep(1)
        else:
            raise AssertionError(f"temporary GPT-5.4 model did not appear: {model_id}")

        acceptance_script = pathlib.Path(__file__).with_name(
            "acceptance-native-phase-streaming-742f686182.py"
        )
        env = {
            **os.environ,
            "MODEL_ID": model_id,
            "BIFROST_PROVIDER": "Cliproxy",
            "BIFROST_MODEL": "gpt-5.4",
            "SKIP_BIFROST_ORDER": "false",
        }
        child = subprocess.run(
            [sys.executable, str(acceptance_script)],
            env=env,
            text=True,
            capture_output=True,
        )
        if child.stdout:
            print(child.stdout, end="")
        if child.stderr:
            print(child.stderr, file=sys.stderr, end="")
        child_code = child.returncode
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "model_id": model_id,
                    "error": repr(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        child_code = 1
    finally:
        if clone_created:
            try:
                if clone_active:
                    request_json("POST", f"/api/v1/functions/id/{clone_id}/toggle")
                request_json("DELETE", f"/api/v1/functions/id/{clone_id}/delete")
            except Exception as exc:
                cleanup_error = repr(exc)
                child_code = 1
        print(
            json.dumps(
                {
                    "temporary_function_id": clone_id,
                    "model_id": model_id,
                    "temporary_function_deleted": clone_created
                    and cleanup_error is None,
                    "cleanup_error": cleanup_error,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return child_code


if __name__ == "__main__":
    raise SystemExit(main())
