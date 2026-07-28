from __future__ import annotations

import concurrent.futures
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
MODEL_ID = os.environ.get("MODEL_ID", "bifrostapi.Cliproxy/gpt-5.5")
RUN_COUNT = int(os.environ.get("RUN_COUNT", "5"))
OUT_DIR = pathlib.Path(
    os.environ.get(
        "OUT_DIR",
        "/home/aiserver/staging/openwebui-pr7-eea11194ed-test",
    )
)


def docker_token() -> str:
    script = """
import asyncio
from datetime import timedelta
from open_webui.models.users import Users
from open_webui.utils.auth import create_token
user = asyncio.run(Users.get_first_user())
print(create_token({'id': user.id}, expires_delta=timedelta(hours=2)))
""".strip()
    proc = subprocess.run(
        ["docker", "exec", "-i", "open-webui-pr7", "python", "-"],
        input=script,
        text=True,
        capture_output=True,
        check=True,
    )
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("failed to mint test JWT")
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
        raise RuntimeError(f"{method} {path} failed {exc.code}: {raw[:2000]}") from exc


def docker_anchor(container: str) -> str:
    return subprocess.check_output(
        [
            "docker",
            "inspect",
            container,
            "--format",
            "{{.Id}}|{{.Config.Image}}|{{.Image}}|"
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|"
            "{{.RestartCount}}|{{.State.OOMKilled}}",
        ],
        text=True,
    ).strip()


def psql_json(sql: str) -> Any:
    proc = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "openwebui-pr7-db",
            "psql",
            "-U",
            "webui_pr7",
            "-d",
            "webui_pr7",
            "-At",
        ],
        input=sql,
        text=True,
        capture_output=True,
        check=True,
    )
    raw = proc.stdout.strip()
    return json.loads(raw) if raw else []


def tool_source(tokens: dict[str, str]) -> str:
    return f"""
TOKENS = {tokens!r}

class Tools:
    def concurrency_step_one(self, marker: str) -> dict:
        \"\"\"Return the opaque token for one concurrency-gate marker.

        :param marker: Exact marker from the user request.
        \"\"\"
        token = TOKENS.get(marker)
        if token is None:
            return {{"status": "error", "reason": "unknown marker"}}
        return {{"status": "ok", "marker": marker, "token": token, "step": 1}}

    def concurrency_step_two(self, token: str) -> dict:
        \"\"\"Complete the fixture using the opaque token from step one.

        :param token: Exact token returned by concurrency_step_one.
        \"\"\"
        for marker, expected in TOKENS.items():
            if token == expected:
                return {{"status": "ok", "marker": marker, "step": 2}}
        return {{"status": "error", "reason": "token mismatch"}}
""".strip()


def start_run(marker: str, tool_id: str) -> dict[str, str]:
    user_message_id = f"msg-user-{uuid.uuid4().hex}"
    assistant_message_id = f"msg-assistant-{uuid.uuid4().hex}"
    prompt = (
        f"Pre-production concurrency marker {marker}. "
        "First call concurrency_step_one with the exact marker. "
        "Only after reading the returned token, call concurrency_step_two with it. "
        "Do not call both tools in parallel. Before each tool call, emit one short public progress sentence. "
        "After step two succeeds, return a short final answer containing the exact marker."
    )
    body = {
        "model": MODEL_ID,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "params": {"function_calling": "native", "temperature": 0},
        "features": {},
        "variables": {},
        "session_id": f"preprod-concurrency-{uuid.uuid4().hex}",
        "parent_id": None,
        "message_ids": {MODEL_ID: assistant_message_id},
        "user_message": {
            "id": user_message_id,
            "parentId": None,
            "childrenIds": [assistant_message_id],
            "role": "user",
            "content": prompt,
            "timestamp": int(time.time()),
            "models": [MODEL_ID],
        },
        "tool_ids": [tool_id],
        "background_tasks": {},
    }
    response = request_json("POST", "/api/chat/completions", body, timeout=180)
    if not response or not response.get("agent_run_id"):
        raise RuntimeError(f"run did not start for {marker}: {response!r}")
    return {"marker": marker, "run_id": response["agent_run_id"]}


def wait_for_run(item: dict[str, str]) -> dict[str, Any]:
    deadline = time.monotonic() + 300
    events: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        payload = request_json(
            "GET",
            f"/api/agent/runs/{item['run_id']}/events/list",
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
            raise RuntimeError(
                f"run {item['run_id']} ended unsuccessfully: "
                + json.dumps(events, ensure_ascii=False)
            )
        time.sleep(1)
    else:
        raise TimeoutError(f"run {item['run_id']} timed out")

    tool_events = [
        event
        for event in events
        if event.get("event_type") in {"tool.requested", "tool.completed"}
    ]
    requested = [event for event in tool_events if event.get("event_type") == "tool.requested"]
    completed = [event for event in tool_events if event.get("event_type") == "tool.completed"]
    names = [(event.get("payload") or {}).get("tool_name") for event in requested]
    call_ids = [
        str((event.get("payload") or {}).get("tool_call_id") or "")
        for event in requested
    ]
    if names != ["concurrency_step_one", "concurrency_step_two"]:
        raise AssertionError(f"unexpected tool sequence for {item['run_id']}: {names!r}")
    if len(completed) != 2:
        raise AssertionError(f"expected two completed tools for {item['run_id']}")
    if any(not call_id for call_id in call_ids) or len(set(call_ids)) != len(call_ids):
        raise AssertionError(
            f"tool_call_id is not unique within run {item['run_id']}: {call_ids!r}"
        )

    commentary_seqs = [
        event["seq"]
        for event in events
        if event.get("event_type") == "text.delta"
        and (event.get("payload") or {}).get("response_phase") == "commentary"
    ]
    requested_seqs = [event["seq"] for event in requested]
    completed_seqs = [event["seq"] for event in completed]
    if not (
        len(commentary_seqs) >= 2
        and commentary_seqs[0] < requested_seqs[0] < completed_seqs[0]
        and completed_seqs[0] < commentary_seqs[1] < requested_seqs[1] < completed_seqs[1]
    ):
        raise AssertionError(
            f"commentary/tool order invalid for {item['run_id']}: "
            f"commentary={commentary_seqs}, requested={requested_seqs}, completed={completed_seqs}"
        )

    final_text = "".join(
        str((event.get("payload") or {}).get("delta") or "")
        for event in events
        if event.get("event_type") == "final.delta"
    )
    if item["marker"] not in final_text:
        raise AssertionError(f"own marker missing from final for {item['run_id']}")

    foreign_markers = [
        marker
        for marker in MARKERS
        if marker != item["marker"] and marker in json.dumps(events, ensure_ascii=False)
    ]
    if foreign_markers:
        raise AssertionError(
            f"cross-run marker contamination for {item['run_id']}: {foreign_markers!r}"
        )

    return {
        **item,
        "event_count": len(events),
        "event_types": [event.get("event_type") for event in events],
        "tool_call_ids": call_ids,
        "final_delta_count": sum(
            event.get("event_type") == "final.delta" for event in events
        ),
        "final_text": final_text,
    }


timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
MARKERS = [f"GATE4-{timestamp}-{index}-{uuid.uuid4().hex[:8]}" for index in range(RUN_COUNT)]
TOKENS = {marker: f"token-{uuid.uuid4().hex}" for marker in MARKERS}


def main() -> int:
    tool_id = f"preprod_concurrency_{timestamp.replace('-', '_')}"
    out_path = OUT_DIR / f"preprod-concurrency-{timestamp}.json"
    result: dict[str, Any] = {
        "timestamp": timestamp,
        "run_count": RUN_COUNT,
        "model_id": MODEL_ID,
        "markers": MARKERS,
        "tool_id": tool_id,
        "webui_before": docker_anchor("open-webui-pr7"),
        "runtime_before": docker_anchor("openwebui-pr7-agentscope-runtime"),
    }
    tool_created = False
    exit_code = 1
    started = time.monotonic()
    try:
        request_json(
            "POST",
            "/api/v1/tools/create",
            {
                "id": tool_id,
                "name": "Pre-production Concurrency Gate",
                "content": tool_source(TOKENS),
                "meta": {"description": "Temporary deterministic two-step concurrency fixture."},
                "access_grants": [],
            },
            timeout=60,
        )
        tool_created = True

        with concurrent.futures.ThreadPoolExecutor(max_workers=RUN_COUNT) as executor:
            started_runs = list(executor.map(lambda marker: start_run(marker, tool_id), MARKERS))
        if len({item["run_id"] for item in started_runs}) != RUN_COUNT:
            raise AssertionError("run IDs are not unique")

        with concurrent.futures.ThreadPoolExecutor(max_workers=RUN_COUNT) as executor:
            completed_runs = list(executor.map(wait_for_run, started_runs))
        result["runs"] = completed_runs

        all_call_ids = [
            call_id
            for item in completed_runs
            for call_id in item["tool_call_ids"]
        ]
        scoped_call_ids = [
            f"{item['run_id']}:{call_id}"
            for item in completed_runs
            for call_id in item["tool_call_ids"]
        ]
        if len(set(scoped_call_ids)) != len(scoped_call_ids):
            raise AssertionError(f"duplicate scoped tool call key: {scoped_call_ids!r}")
        result["raw_tool_call_ids"] = all_call_ids
        result["globally_reused_tool_call_ids"] = sorted(
            call_id for call_id in set(all_call_ids) if all_call_ids.count(call_id) > 1
        )
        result["scoped_tool_call_ids"] = scoped_call_ids

        escaped = ",".join("'" + item["run_id"].replace("'", "''") + "'" for item in completed_runs)
        db_runs = psql_json(
            "select coalesce(json_agg(row_to_json(r) order by r.id), '[]'::json) "
            "from (select id,state,error,final_text from agent_run "
            f"where id in ({escaped})) r;\n"
        )
        if len(db_runs) != RUN_COUNT or any(item["state"] != "completed" for item in db_runs):
            raise AssertionError(f"DB terminal states invalid: {db_runs!r}")

        result.update(
            {
                "ok": True,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "db_runs": db_runs,
                "webui_after": docker_anchor("open-webui-pr7"),
                "runtime_after": docker_anchor("openwebui-pr7-agentscope-runtime"),
            }
        )
        if result["webui_before"] != result["webui_after"]:
            raise AssertionError("WebUI container health/identity changed during gate")
        if result["runtime_before"] != result["runtime_after"]:
            raise AssertionError("runtime container health/identity changed during gate")
        exit_code = 0
    except Exception as exc:
        result.update(
            {
                "ok": False,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "error": repr(exc),
                "webui_after": docker_anchor("open-webui-pr7"),
                "runtime_after": docker_anchor("openwebui-pr7-agentscope-runtime"),
            }
        )
    finally:
        if tool_created:
            try:
                request_json("DELETE", f"/api/v1/tools/id/{tool_id}/delete", timeout=30)
                result["tool_deleted"] = True
            except Exception as exc:
                result["tool_deleted"] = False
                result["tool_delete_error"] = repr(exc)
                result["ok"] = False
                exit_code = 1
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"ok": result.get("ok"), "audit_path": str(out_path), "error": result.get("error")}, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
