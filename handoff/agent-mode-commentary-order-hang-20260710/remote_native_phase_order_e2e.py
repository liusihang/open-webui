from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any


BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:18085").rstrip("/")
BIFROST_URL = os.environ.get("BIFROST_URL", "http://127.0.0.1:18080").rstrip("/")
ADMIN_USER_ID = os.environ.get(
    "ADMIN_USER_ID",
    "b6826286-1251-4576-b3a0-e109ff085a61",
)
MODEL_ID = os.environ.get("MODEL_ID", "bifrostapi.Cliproxy/gpt-5.5")
OUT_DIR = pathlib.Path(
    os.environ.get(
        "OUT_DIR",
        "/home/aiserver/staging/openwebui-pr7-eea11194ed-test",
    )
)
MAX_BIFROST_DETAILS = 3


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


def bifrost_json(path: str, timeout: int = 30) -> Any:
    request = urllib.request.Request(BIFROST_URL + path, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


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


def shell_output(args: list[str]) -> str:
    return subprocess.check_output(args, text=True).strip()


def tool_source(marker: str, token: str) -> str:
    return f'''
class Tools:
    def native_phase_step_one(self, marker: str) -> dict:
        """Start the native phase ordering fixture and return a secret continuation token.

        :param marker: The exact public E2E marker from the user request.
        """
        if marker != {marker!r}:
            return {{"status": "error", "reason": "marker mismatch"}}
        return {{"status": "ok", "marker": {marker!r}, "token": {token!r}, "step": 1}}

    def native_phase_step_two(self, token: str) -> dict:
        """Complete the native phase ordering fixture with the token returned by step one.

        :param token: The exact secret continuation token returned by native_phase_step_one.
        """
        if token != {token!r}:
            return {{"status": "error", "reason": "token mismatch", "step": 2}}
        return {{"status": "ok", "marker": {marker!r}, "step": 2}}
'''.strip()


def event_label(event: dict[str, Any]) -> str:
    event_type = event.get("event_type")
    payload = event.get("payload") or {}
    if event_type == "text.delta":
        return f"text.delta:{payload.get('block_kind')}:{payload.get('model_call_id')}"
    return str(event_type)


def text_from_item(item: dict[str, Any]) -> str:
    content = item.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            value = part.get("text") or part.get("output_text")
            if isinstance(value, str):
                parts.append(value)
        return " ".join(parts)
    if isinstance(content, str):
        return content
    output = item.get("output")
    if isinstance(output, str):
        return output
    arguments = item.get("arguments")
    if isinstance(arguments, str):
        return arguments
    return ""


def item_summary(item: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "index": index,
        "type": item.get("type"),
        "role": item.get("role"),
        "phase": item.get("phase"),
        "name": item.get("name"),
        "call_id": item.get("call_id"),
        "text": text_from_item(item)[:240],
    }


def bifrost_log_ids(limit: int = 5) -> list[str]:
    params = urllib.parse.urlencode(
        {
            "period": "1h",
            "limit": limit,
            "offset": 0,
            "sort_by": "timestamp",
            "order": "desc",
        }
    )
    listing = bifrost_json(f"/api/logs?{params}", timeout=30)
    return [
        entry["id"]
        for entry in listing.get("logs") or []
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    ]


def validate_bifrost_order(marker: str, before_ids: set[str]) -> dict[str, Any]:
    after_ids = bifrost_log_ids()
    new_ids = [log_id for log_id in after_ids if log_id not in before_ids]
    inspected: list[str] = []
    selected: dict[str, Any] | None = None

    for log_id in new_ids[:MAX_BIFROST_DETAILS]:
        inspected.append(log_id)
        detail = bifrost_json(f"/api/logs/{urllib.parse.quote(log_id)}", timeout=30)
        history = detail.get("responses_input_history")
        if not isinstance(history, list):
            continue
        if marker not in json.dumps(history, ensure_ascii=False):
            continue
        if sum(
            isinstance(item, dict) and item.get("type") == "function_call_output"
            for item in history
        ) < 2:
            continue
        selected = detail
        selected.setdefault("id", log_id)
        break

    if selected is None:
        raise AssertionError(
            "no exact newly-created Bifrost final-round record found; "
            f"new_ids={new_ids!r}, inspected={inspected!r}"
        )

    history = selected["responses_input_history"]
    indexed = [
        (index, item)
        for index, item in enumerate(history)
        if isinstance(item, dict)
    ]
    user_indices = [
        index
        for index, item in indexed
        if item.get("type") == "message"
        and item.get("role") == "user"
        and marker in text_from_item(item)
    ]
    commentary = [
        (index, item)
        for index, item in indexed
        if item.get("type") == "message"
        and item.get("role") == "assistant"
        and item.get("phase") == "commentary"
    ]
    calls = [
        (index, item)
        for index, item in indexed
        if item.get("type") == "function_call"
        and item.get("name") in {"native_phase_step_one", "native_phase_step_two"}
    ]
    outputs = [
        (index, item)
        for index, item in indexed
        if item.get("type") == "function_call_output"
    ]

    if not user_indices:
        raise AssertionError("exact marker user message missing from Bifrost history")
    if len(commentary) < 2:
        raise AssertionError(f"expected two commentary messages, got {len(commentary)}")
    if [item.get("name") for _, item in calls] != [
        "native_phase_step_one",
        "native_phase_step_two",
    ]:
        raise AssertionError(f"unexpected fixture calls: {calls!r}")

    output_by_call_id = {
        item.get("call_id"): index
        for index, item in outputs
        if isinstance(item.get("call_id"), str)
    }
    call_one_index, call_one = calls[0]
    call_two_index, call_two = calls[1]
    output_one_index = output_by_call_id.get(call_one.get("call_id"))
    output_two_index = output_by_call_id.get(call_two.get("call_id"))
    if output_one_index is None or output_two_index is None:
        raise AssertionError("fixture call/output correlation is incomplete")

    commentary_before_one = [index for index, _ in commentary if index < call_one_index]
    commentary_between = [
        index
        for index, _ in commentary
        if output_one_index < index < call_two_index
    ]
    if not commentary_before_one or not commentary_between:
        raise AssertionError(
            "commentary is not interleaved between tool rounds: "
            f"commentary={[index for index, _ in commentary]}, "
            f"calls={[index for index, _ in calls]}, "
            f"outputs={[index for index, _ in outputs]}"
        )
    if not (
        user_indices[0]
        < commentary_before_one[-1]
        < call_one_index
        < output_one_index
        < commentary_between[0]
        < call_two_index
        < output_two_index
    ):
        raise AssertionError("native phase transaction order is invalid")

    return {
        "log_id": selected.get("id"),
        "object": selected.get("object"),
        "provider": selected.get("provider"),
        "model": selected.get("model"),
        "timestamp": selected.get("timestamp"),
        "new_log_ids": new_ids,
        "detail_ids_inspected": inspected,
        "detail_fetch_count": len(inspected),
        "history_summary": [item_summary(item, index) for index, item in indexed],
        "verified_indices": {
            "user": user_indices[0],
            "commentary_one": commentary_before_one[-1],
            "call_one": call_one_index,
            "output_one": output_one_index,
            "commentary_two": commentary_between[0],
            "call_two": call_two_index,
            "output_two": output_two_index,
        },
    }


def validate_events(events: list[dict[str, Any]], marker: str) -> dict[str, Any]:
    typed = [
        (index, event, event.get("payload") or {})
        for index, event in enumerate(events)
    ]
    failure_types = {"run.failed", "run.cancelled", "run.budget_exceeded"}
    failures = [event for _, event, _ in typed if event.get("event_type") in failure_types]
    if failures:
        raise AssertionError(f"run ended unsuccessfully: {failures!r}")

    commentary = [
        (index, event, payload)
        for index, event, payload in typed
        if event.get("event_type") == "text.delta"
        and payload.get("block_kind") == "assistant_note"
        and payload.get("source") == "model"
        and payload.get("response_phase") == "commentary"
    ]
    requested = [
        (index, event, payload)
        for index, event, payload in typed
        if event.get("event_type") == "tool.requested"
    ]
    completed = [
        (index, event, payload)
        for index, event, payload in typed
        if event.get("event_type") == "tool.completed"
    ]
    final_started = [
        index
        for index, event, _ in typed
        if event.get("event_type") == "final.started"
    ]
    final_deltas = [
        (index, event, payload)
        for index, event, payload in typed
        if event.get("event_type") == "final.delta"
    ]
    run_completed = [
        index
        for index, event, _ in typed
        if event.get("event_type") == "run.completed"
    ]

    commentary_rounds: dict[str, dict[str, Any]] = {}
    for index, _, payload in commentary:
        model_call_id = payload.get("model_call_id")
        if not isinstance(model_call_id, str) or not model_call_id:
            raise AssertionError("model commentary is missing model_call_id")
        round_summary = commentary_rounds.setdefault(
            model_call_id,
            {"first_index": index, "last_index": index, "deltas": []},
        )
        round_summary["last_index"] = index
        round_summary["deltas"].append(str(payload.get("delta") or ""))

    ordered_rounds = list(commentary_rounds.items())
    if len(ordered_rounds) < 2:
        raise AssertionError(
            f"expected model commentary from two rounds: {list(commentary_rounds)!r}"
        )
    if len(requested) != 2 or len(completed) != 2:
        raise AssertionError(
            f"expected two tool transactions, requested={len(requested)}, completed={len(completed)}"
        )
    if len(final_started) != 1 or not run_completed:
        raise AssertionError("final.started/run.completed lifecycle is incomplete")
    if len(final_deltas) < 2:
        raise AssertionError(
            f"expected genuinely incremental final streaming, got {len(final_deltas)} final.delta event(s)"
        )

    first_round_id, first_round = ordered_rounds[0]
    second_round_id, second_round = ordered_rounds[1]
    if not "".join(first_round["deltas"]).strip() or not "".join(
        second_round["deltas"]
    ).strip():
        raise AssertionError("model commentary text is empty in one or more tool rounds")
    if not (
        first_round["first_index"]
        <= first_round["last_index"]
        < requested[0][0]
        < completed[0][0]
        < second_round["first_index"]
        <= second_round["last_index"]
        < requested[1][0]
        < completed[1][0]
        < final_started[0]
        < final_deltas[0][0]
        < run_completed[-1]
    ):
        raise AssertionError("public Agent event ordering is invalid")
    if any(index > final_started[0] for index, _, _ in commentary):
        raise AssertionError("commentary was emitted after final.started")

    delta_indices = [payload.get("delta_index") for _, _, payload in final_deltas]
    if all(isinstance(value, int) for value in delta_indices) and delta_indices != list(
        range(len(delta_indices))
    ):
        raise AssertionError(f"final.delta indexes are not contiguous: {delta_indices!r}")

    final_text = "".join(str(payload.get("delta") or "") for _, _, payload in final_deltas)
    if marker not in final_text:
        raise AssertionError(f"final answer did not include marker {marker}: {final_text!r}")

    return {
        "event_labels": [event_label(event) for event in events],
        "commentary_model_call_ids": [first_round_id, second_round_id],
        "commentary_text": [
            "".join(first_round["deltas"]),
            "".join(second_round["deltas"]),
        ],
        "final_delta_count": len(final_deltas),
        "final_delta_indices": delta_indices,
        "final_text": final_text,
    }


def redact_secret(value: Any, secret: str) -> Any:
    if isinstance(value, str):
        return value.replace(secret, "[REDACTED_SECRET_TOKEN]")
    if isinstance(value, list):
        return [redact_secret(item, secret) for item in value]
    if isinstance(value, tuple):
        return [redact_secret(item, secret) for item in value]
    if isinstance(value, dict):
        return {key: redact_secret(item, secret) for key, item in value.items()}
    return value


def main() -> int:
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    marker = f"NATIVE-PHASE-{timestamp}-{uuid.uuid4().hex[:8]}"
    secret_token = f"token-{uuid.uuid4().hex}"
    out_path = OUT_DIR / f"e2e-agentmode-native-phase-{timestamp}.json"
    tool_id = f"e2e_native_phase_{timestamp.replace('-', '_')}".lower()
    summary: dict[str, Any] = {
        "timestamp": timestamp,
        "marker": marker,
        "base_url": BASE_URL,
        "bifrost_url": BIFROST_URL,
        "model_id": MODEL_ID,
        "tool_id": tool_id,
        "max_bifrost_detail_fetches": MAX_BIFROST_DETAILS,
        "audit_path": str(out_path),
    }

    started = time.monotonic()
    result_code = 1
    tool_created = False
    try:
        models = request_json("GET", "/api/models?refresh=true", timeout=60)
        model_items = (models.get("data") if isinstance(models, dict) else models) or []
        model_ids = {item.get("id") for item in model_items if isinstance(item, dict)}
        if MODEL_ID not in model_ids:
            raise AssertionError(f"exact acceptance model is unavailable: {MODEL_ID}")

        before_ids = set(bifrost_log_ids())
        summary["bifrost_before_ids"] = sorted(before_ids)
        tool = request_json(
            "POST",
            "/api/v1/tools/create",
            {
                "id": tool_id,
                "name": "E2E Native Phase Ordering Fixture",
                "content": tool_source(marker, secret_token),
                "meta": {
                    "description": "Temporary sequential Agent Mode native phase fixture."
                },
                "access_grants": [],
            },
            timeout=60,
        )
        tool_created = True
        summary["tool_specs"] = tool.get("specs")

        user_message_id = f"msg-user-{uuid.uuid4().hex}"
        assistant_message_id = f"msg-assistant-{uuid.uuid4().hex}"
        prompt = (
            f"Native phase E2E marker {marker}. "
            "Before every tool call, write one short sentence explaining only the next action. "
            "First call native_phase_step_one with the exact marker. "
            "Then, only after reading its result, call native_phase_step_two with the secret token it returned. "
            "Do not guess or reuse a token before step one returns. "
            "After both tools succeed, give a final answer of at least five complete sentences. "
            "The final answer must include the exact marker, confirm both numbered steps, and contain no more tool calls."
        )
        chat_body = {
            "model": MODEL_ID,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "params": {"function_calling": "native", "temperature": 0},
            "features": {},
            "variables": {},
            "session_id": f"agent-e2e-native-phase-session-{uuid.uuid4().hex}",
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
        chat = request_json("POST", "/api/chat/completions", chat_body, timeout=180)
        summary["chat_response"] = chat
        if not chat or not chat.get("status") or not chat.get("agent_run_id"):
            raise AssertionError(f"chat did not start Agent Mode run: {chat}")
        run_id = chat["agent_run_id"]
        summary["run_id"] = run_id

        events: list[dict[str, Any]] = []
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            payload = request_json("GET", f"/api/agent/runs/{run_id}/events/list", timeout=30)
            events = payload.get("events") or []
            types = [event.get("event_type") for event in events]
            if "run.completed" in types:
                break
            if any(
                event_type in {"run.failed", "run.cancelled", "run.budget_exceeded"}
                for event_type in types
            ):
                raise AssertionError("run ended unsuccessfully: " + json.dumps(events, ensure_ascii=False))
            time.sleep(1)
        else:
            raise TimeoutError("timed out waiting for run.completed")

        summary["events"] = events
        summary["event_validation"] = validate_events(events, marker)
        safe_run_id = run_id.replace("'", "''")
        summary["db_events"] = psql_json(
            "select coalesce(json_agg(row_to_json(e) order by e.seq), '[]'::json) "
            "from (select seq,event_type,phase,payload from agent_run_event "
            f"where run_id = '{safe_run_id}' order by seq) e;\n"
        )
        summary["bifrost_order"] = validate_bifrost_order(marker, before_ids)
        summary["runtime_anchor_after"] = shell_output(
            [
                "docker",
                "inspect",
                "openwebui-pr7-agentscope-runtime",
                "--format",
                "{{.Config.Image}} {{.Image}} {{.State.Health.Status}} {{.RestartCount}} {{.State.StartedAt}}",
            ]
        )
        summary["isolated_webui_anchor_after"] = shell_output(
            [
                "docker",
                "inspect",
                "open-webui-pr7",
                "--format",
                "{{.Config.Image}} {{.Image}} {{.State.Health.Status}} {{.RestartCount}} {{.State.StartedAt}}",
            ]
        )
        summary["live_anchor_after"] = shell_output(
            [
                "docker",
                "inspect",
                "open-webui",
                "--format",
                "{{.Config.Image}} {{.Image}} {{.State.Health.Status}} {{.RestartCount}} {{.State.StartedAt}}",
            ]
        )
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
                request_json("DELETE", f"/api/v1/tools/id/{tool_id}/delete", timeout=30)
                summary["tool_deleted"] = True
            except Exception as exc:
                summary["tool_deleted"] = False
                summary["tool_delete_error"] = repr(exc)
                summary["ok"] = False
                result_code = 1
        else:
            summary["tool_deleted"] = None
        summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
        safe_summary = redact_secret(summary, secret_token)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(safe_summary, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        compact = {
            "ok": safe_summary.get("ok"),
            "run_id": safe_summary.get("run_id"),
            "audit_path": safe_summary.get("audit_path"),
            "elapsed_seconds": safe_summary.get("elapsed_seconds"),
            "tool_deleted": safe_summary.get("tool_deleted"),
            "bifrost_log_id": (safe_summary.get("bifrost_order") or {}).get("log_id"),
            "final_delta_count": (safe_summary.get("event_validation") or {}).get(
                "final_delta_count"
            ),
            "error": safe_summary.get("error"),
        }
        print(json.dumps(compact, indent=2, ensure_ascii=False))

    return result_code


if __name__ == "__main__":
    raise SystemExit(main())
