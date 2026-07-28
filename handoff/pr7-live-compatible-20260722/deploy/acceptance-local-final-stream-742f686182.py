from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import subprocess
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
OUT_DIR = pathlib.Path(
    os.environ.get(
        "OUT_DIR",
        "/home/aiserver/staging/openwebui-pr7-eea11194ed-test",
    )
)
EXPECTED_RUNTIME_IMAGE_ID = (
    "sha256:f7396ba23e49f934216ba8fc4b38c695b7f639722d852b44234769c66ca7f6e9"
)


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


def pipe_source(first_text: str, second_text: str) -> str:
    return f'''
"""
title: Temporary Agent final streaming fixture
version: 0.0.1
"""
import asyncio


class Pipe:
    async def pipe(self, body: dict):
        if not body.get("stream", False):
            return "stream mode required"

        async def stream():
            await asyncio.sleep(0.2)
            yield {{
                "choices": [{{
                    "index": 0,
                    "delta": {{
                        "content": {first_text!r},
                        "phase": "final_answer",
                    }},
                }}],
            }}
            await asyncio.sleep(1.0)
            yield {{
                "choices": [{{
                    "index": 0,
                    "delta": {{
                        "content": {second_text!r},
                        "phase": "final_answer",
                    }},
                }}],
            }}

        return stream()
'''.strip()


def tool_source() -> str:
    return '''
class Tools:
    def unused_streaming_fixture_tool(self) -> dict:
        """Temporary tool that must remain unused by the streaming fixture."""
        return {"status": "unexpected"}
'''.strip()


def container_anchor(name: str) -> dict[str, Any]:
    raw = subprocess.check_output(
        [
            "docker",
            "inspect",
            name,
            "--format",
            "{{json .}}",
        ],
        text=True,
    )
    item = json.loads(raw)
    return {
        "id": item["Id"],
        "image_id": item["Image"],
        "image": item["Config"]["Image"],
        "health": (item["State"].get("Health") or {}).get("Status"),
        "restarts": item["RestartCount"],
        "oom": item["State"]["OOMKilled"],
    }


def validate_events(
    events: list[dict[str, Any]],
    expected_text: str,
) -> dict[str, Any]:
    failures = [
        event
        for event in events
        if event.get("event_type")
        in {"run.failed", "run.cancelled", "run.budget_exceeded"}
    ]
    if failures:
        raise AssertionError(f"run ended unsuccessfully: {failures!r}")

    final_started = [event for event in events if event.get("event_type") == "final.started"]
    final_deltas = [event for event in events if event.get("event_type") == "final.delta"]
    completed = [event for event in events if event.get("event_type") == "run.completed"]
    tool_events = [
        event
        for event in events
        if str(event.get("event_type") or "").startswith("tool.")
    ]
    if len(final_started) != 1 or len(completed) != 1:
        raise AssertionError("final lifecycle is incomplete")
    if len(final_deltas) != 2:
        raise AssertionError(f"expected two live final deltas, got {len(final_deltas)}")
    if tool_events:
        raise AssertionError(f"temporary tool was unexpectedly called: {tool_events!r}")

    delta_indices = [(event.get("payload") or {}).get("delta_index") for event in final_deltas]
    if delta_indices != [0, 1]:
        raise AssertionError(f"unexpected final delta indexes: {delta_indices!r}")
    final_text = "".join(
        str((event.get("payload") or {}).get("delta") or "")
        for event in final_deltas
    )
    if final_text != expected_text:
        raise AssertionError(f"unexpected final text: {final_text!r}")

    first_created = int(final_deltas[0]["created_at"])
    second_created = int(final_deltas[1]["created_at"])
    completed_created = int(completed[0]["created_at"])
    delta_gap_ms = (second_created - first_created) / 1_000_000
    if delta_gap_ms < 500:
        raise AssertionError(f"final deltas were not live-separated: {delta_gap_ms} ms")
    if not first_created < second_created < completed_created:
        raise AssertionError("final delta/completion timestamps are not ordered")

    return {
        "event_types": [event.get("event_type") for event in events],
        "final_delta_count": len(final_deltas),
        "final_delta_indices": delta_indices,
        "final_delta_gap_ms": round(delta_gap_ms, 3),
        "final_text": final_text,
    }


def main() -> int:
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    marker = f"LOCAL-FINAL-STREAM-{timestamp}-{suffix}"
    first_text = f"{marker}:first "
    second_text = "second"
    expected_text = first_text + second_text
    function_id = f"tmp_agent_final_stream_{timestamp.replace('-', '_')}_{suffix}".lower()
    tool_id = f"tmp_agent_final_stream_tool_{timestamp.replace('-', '_')}_{suffix}".lower()
    out_path = OUT_DIR / f"e2e-agentmode-local-final-stream-{timestamp}.json"
    summary: dict[str, Any] = {
        "timestamp": timestamp,
        "marker": marker,
        "function_id": function_id,
        "tool_id": tool_id,
        "audit_path": str(out_path),
    }
    function_created = False
    function_active = False
    tool_created = False
    started = time.monotonic()
    result_code = 1

    try:
        request_json(
            "POST",
            "/api/v1/functions/create",
            {
                "id": function_id,
                "name": "Temporary Agent final streaming fixture",
                "meta": {"description": "Temporary deterministic final streaming E2E."},
                "content": pipe_source(first_text, second_text),
            },
        )
        function_created = True
        request_json("POST", f"/api/v1/functions/id/{function_id}/toggle")
        function_active = True

        request_json(
            "POST",
            "/api/v1/tools/create",
            {
                "id": tool_id,
                "name": "Temporary unused final streaming tool",
                "content": tool_source(),
                "meta": {"description": "Forces the general Agent path; must not execute."},
                "access_grants": [],
            },
        )
        tool_created = True

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            models = request_json("GET", "/api/models?refresh=true", timeout=60)
            items = (models.get("data") if isinstance(models, dict) else models) or []
            if function_id in {
                item.get("id") for item in items if isinstance(item, dict)
            }:
                break
            time.sleep(0.5)
        else:
            raise AssertionError("temporary pipe model did not appear in /api/models")

        user_message_id = f"msg-user-{uuid.uuid4().hex}"
        assistant_message_id = f"msg-assistant-{uuid.uuid4().hex}"
        prompt = f"Return the deterministic fixture response for {marker}. Do not call tools."
        chat = request_json(
            "POST",
            "/api/chat/completions",
            {
                "model": function_id,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "params": {"function_calling": "native", "temperature": 0},
                "features": {},
                "variables": {},
                "session_id": f"agent-local-stream-{uuid.uuid4().hex}",
                "parent_id": None,
                "message_ids": {function_id: assistant_message_id},
                "user_message": {
                    "id": user_message_id,
                    "parentId": None,
                    "childrenIds": [assistant_message_id],
                    "role": "user",
                    "content": prompt,
                    "timestamp": int(time.time()),
                    "models": [function_id],
                },
                "tool_ids": [tool_id],
                "background_tasks": {},
            },
            timeout=180,
        )
        summary["chat_response"] = chat
        if not chat or not chat.get("status") or not chat.get("agent_run_id"):
            raise AssertionError(f"chat did not start Agent Mode run: {chat!r}")
        run_id = chat["agent_run_id"]
        summary["run_id"] = run_id

        events: list[dict[str, Any]] = []
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            payload = request_json(
                "GET",
                f"/api/agent/runs/{run_id}/events/list",
                timeout=30,
            )
            events = payload.get("events") or []
            types = [event.get("event_type") for event in events]
            if "run.completed" in types:
                break
            if any(
                event_type in {"run.failed", "run.cancelled", "run.budget_exceeded"}
                for event_type in types
            ):
                raise AssertionError("run ended unsuccessfully: " + json.dumps(events))
            time.sleep(0.1)
        else:
            raise TimeoutError("timed out waiting for local final streaming run")

        summary["events"] = events
        summary["event_validation"] = validate_events(events, expected_text)
        summary["runtime_anchor"] = container_anchor(
            "openwebui-pr7-agentscope-runtime"
        )
        if summary["runtime_anchor"]["image_id"] != EXPECTED_RUNTIME_IMAGE_ID:
            raise AssertionError("runtime image changed during acceptance")
        summary["webui_anchor"] = container_anchor("open-webui-pr7")
        summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
        summary["ok"] = True
        result_code = 0
    except Exception as exc:
        summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
        summary["ok"] = False
        summary["error"] = repr(exc)
    finally:
        if tool_created:
            try:
                request_json("DELETE", f"/api/v1/tools/id/{tool_id}/delete")
                summary["tool_deleted"] = True
            except Exception as exc:
                summary["tool_deleted"] = False
                summary["tool_delete_error"] = repr(exc)
                result_code = 1
        if function_created:
            try:
                if function_active:
                    request_json("POST", f"/api/v1/functions/id/{function_id}/toggle")
                request_json("DELETE", f"/api/v1/functions/id/{function_id}/delete")
                summary["function_deleted"] = True
            except Exception as exc:
                summary["function_deleted"] = False
                summary["function_delete_error"] = repr(exc)
                result_code = 1
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

    print(
        json.dumps(
            {
                "ok": summary.get("ok"),
                "run_id": summary.get("run_id"),
                "audit_path": str(out_path),
                "elapsed_seconds": summary.get("elapsed_seconds"),
                "final_delta_count": (summary.get("event_validation") or {}).get(
                    "final_delta_count"
                ),
                "final_delta_gap_ms": (summary.get("event_validation") or {}).get(
                    "final_delta_gap_ms"
                ),
                "function_deleted": summary.get("function_deleted"),
                "tool_deleted": summary.get("tool_deleted"),
                "error": summary.get("error"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return result_code


if __name__ == "__main__":
    raise SystemExit(main())
