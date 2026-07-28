from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import os
import shlex
import socket
import time
import urllib.parse
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any


PORT = 8080
ADMIN_USER_ID = os.environ.get(
    "ADMIN_USER_ID",
    "b6826286-1251-4576-b3a0-e109ff085a61",
)
MODEL_ID = os.environ.get("MODEL_ID", "bifrostapi.Cliproxy/gpt-5.5")
WAIT_SECONDS = float(os.environ.get("WAIT_SECONDS", "300"))
CLEANUP_PATH = os.environ.get("CLEANUP_PATH")


def create_admin_token() -> str:
    from open_webui.utils.auth import create_token

    return create_token({"id": ADMIN_USER_ID}, expires_delta=timedelta(hours=2))


class Session:
    def __init__(self, token: str):
        self.sock = socket.create_connection(("127.0.0.1", PORT), timeout=30)
        self.sock.settimeout(180)
        self.local_port = self.sock.getsockname()[1]
        self.token = token
        self.worker_pid: int | None = None

    def close(self) -> None:
        self.sock.close()

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        idempotency_key: str | None = None,
    ) -> tuple[int, Any]:
        encoded = b"" if body is None else json.dumps(body).encode("utf-8")
        headers = [
            f"{method} {path} HTTP/1.1",
            "Host: localhost",
            f"Authorization: Bearer {self.token}",
            "Accept: application/json",
            "Connection: keep-alive",
            f"Content-Length: {len(encoded)}",
        ]
        if body is not None:
            headers.append("Content-Type: application/json")
        if idempotency_key:
            headers.append(f"X-Agent-Idempotency-Key: {idempotency_key}")
        request = ("\r\n".join(headers) + "\r\n\r\n").encode("utf-8") + encoded
        self.sock.sendall(request)

        data = b""
        while b"\r\n\r\n" not in data:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError(
                    "connection closed while reading headers "
                    f"method={method} path={path} worker_pid={self.worker_pid} "
                    f"local_port={self.local_port}"
                )
            data += chunk
        raw_headers, response_body = data.split(b"\r\n\r\n", 1)
        lines = raw_headers.split(b"\r\n")
        status = int(lines[0].split()[1])
        header_map: dict[bytes, bytes] = {}
        for line in lines[1:]:
            if b":" not in line:
                continue
            key, value = line.split(b":", 1)
            header_map[key.lower()] = value.strip().lower()

        if header_map.get(b"transfer-encoding") == b"chunked":
            chunks = bytearray()
            while True:
                while b"\r\n" not in response_body:
                    response_body += self.sock.recv(65536)
                line, response_body = response_body.split(b"\r\n", 1)
                size = int(line.split(b";", 1)[0], 16)
                if size == 0:
                    while len(response_body) < 2:
                        response_body += self.sock.recv(65536)
                    response_body = bytes(chunks)
                    break
                while len(response_body) < size + 2:
                    response_body += self.sock.recv(65536)
                chunks.extend(response_body[:size])
                response_body = response_body[size + 2 :]
        else:
            length = int(header_map.get(b"content-length", b"0"))
            while len(response_body) < length:
                response_body += self.sock.recv(65536)
            response_body = response_body[:length]

        if not response_body:
            return status, None
        try:
            return status, json.loads(response_body.decode("utf-8"))
        except json.JSONDecodeError:
            return status, response_body.decode("utf-8", errors="replace")


def worker_pids() -> list[int]:
    result = []
    for path in Path("/proc").glob("[0-9]*"):
        try:
            command = path.joinpath("cmdline").read_bytes().replace(b"\0", b" ").decode()
        except OSError:
            continue
        if "multiprocessing.spawn" in command and "spawn_main" in command:
            result.append(int(path.name))
    return sorted(result)


def worker_ports(pids: list[int]) -> dict[int, set[int]]:
    result = {pid: set() for pid in pids}
    for pid in pids:
        entries: dict[str, int] = {}
        try:
            lines = Path(f"/proc/{pid}/net/tcp").read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 10 or fields[3] != "01":
                continue
            local_port = int(fields[1].split(":", 1)[1], 16)
            if local_port == PORT:
                entries[fields[9]] = int(fields[2].split(":", 1)[1], 16)
        for fd in Path(f"/proc/{pid}/fd").glob("*"):
            try:
                link = os.readlink(fd)
            except OSError:
                continue
            if link.startswith("socket:[") and link[8:-1] in entries:
                result[pid].add(entries[link[8:-1]])
    return result


def pin_sessions(token: str, pids: list[int]) -> dict[int, Session]:
    sessions: list[Session] = []
    try:
        for _ in range(256):
            session = Session(token)
            sessions.append(session)
            status, _ = session.request("GET", "/health")
            if status != 200:
                raise RuntimeError(f"health request failed with {status}")
            mapping = worker_ports(pids)
            selected: dict[int, Session] = {}
            for pid, ports in mapping.items():
                for candidate in sessions:
                    if candidate.local_port in ports:
                        selected[pid] = candidate
                        break
            if len(selected) == len(pids):
                expired: set[Session] = set()
                for candidate in selected.values():
                    try:
                        status, _ = candidate.request("GET", "/health")
                        if status != 200:
                            raise RuntimeError(f"final pinned health returned {status}")
                    except Exception:
                        candidate.close()
                        expired.add(candidate)
                if expired:
                    sessions = [candidate for candidate in sessions if candidate not in expired]
                    continue
                for pid, candidate in selected.items():
                    candidate.worker_pid = pid
                for candidate in sessions:
                    if candidate not in selected.values():
                        candidate.close()
                return dict(sorted(selected.items()))
        raise RuntimeError(f"worker coverage incomplete: {worker_ports(pids)}")
    except Exception:
        for session in sessions:
            try:
                session.close()
            except Exception:
                pass
        raise


def refresh_sessions(
    sessions: dict[int, Session],
    *,
    token: str,
    pids: list[int],
) -> tuple[dict[str, int], dict[str, int]]:
    before = {str(pid): session.local_port for pid, session in sessions.items()}
    replacement = pin_sessions(token, pids)
    for session in sessions.values():
        session.close()
    sessions.clear()
    sessions.update(replacement)
    after = {str(pid): session.local_port for pid, session in sessions.items()}
    if before == after:
        raise AssertionError("refresh unexpectedly reused every previous client port")
    return before, after


def tool_source() -> str:
    return '''
import os


class Tools:
    def protected_release_action(self, operation: str, marker: str) -> dict:
        """Safely simulate a protected write operation without touching the filesystem.

        :param operation: The exact destructive-classifier operation requested by the prompt.
        :param marker: The exact release-gate marker requested by the prompt.
        """
        return {
            "status": "ok",
            "simulated": True,
            "worker_pid": os.getpid(),
            "operation": operation,
            "marker": marker,
        }

    def release_gate_marker(self, answer: str) -> dict:
        """Confirm the accepted user-input answer after durable resume.

        :param answer: The exact answer returned by request_user_input.
        """
        return {
            "status": "ok",
            "worker_pid": os.getpid(),
            "answer": answer,
        }
'''.strip()


def expect_status(status: int, expected: set[int], payload: Any, label: str) -> Any:
    if status not in expected:
        raise AssertionError(f"{label} returned {status}: {payload!r}")
    return payload


def fetch_events(sessions: dict[int, Session], run_id: str) -> dict[int, list[dict[str, Any]]]:
    path = f"/api/agent/runs/{urllib.parse.quote(run_id, safe='')}/events/list"

    def fetch(item: tuple[int, Session]) -> tuple[int, list[dict[str, Any]]]:
        pid, session = item
        status, payload = session.request("GET", path)
        expect_status(status, {200}, payload, f"events via worker {pid}")
        return pid, list((payload or {}).get("events") or [])

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(sessions)) as executor:
        return dict(executor.map(fetch, sessions.items()))


def fetch_run_states(sessions: dict[int, Session], run_id: str) -> dict[int, str]:
    path = f"/api/agent/runs/{urllib.parse.quote(run_id, safe='')}"

    def fetch(item: tuple[int, Session]) -> tuple[int, str]:
        pid, session = item
        status, payload = session.request("GET", path)
        expect_status(status, {200}, payload, f"run state via worker {pid}")
        return pid, str((payload or {}).get("state"))

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(sessions)) as executor:
        return dict(executor.map(fetch, sessions.items()))


def event_fingerprint(events: list[dict[str, Any]]) -> str:
    return json.dumps(events, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def wait_for_events(
    sessions: dict[int, Session],
    run_id: str,
    *,
    target_types: set[str],
    terminal_types: set[str] | None = None,
) -> tuple[dict[int, list[dict[str, Any]]], int]:
    deadline = time.monotonic() + WAIT_SECONDS
    consistent_rounds = 0
    terminal_types = terminal_types or {"run.failed", "run.cancelled", "run.budget_exceeded"}
    while time.monotonic() < deadline:
        snapshots = fetch_events(sessions, run_id)
        fingerprints = {event_fingerprint(events) for events in snapshots.values()}
        if len(fingerprints) == 1:
            consistent_rounds += 1
        by_pid = {
            pid: {str(event.get("event_type")) for event in events}
            for pid, events in snapshots.items()
        }
        if all(target_types.issubset(types) for types in by_pid.values()):
            if len(fingerprints) != 1:
                raise AssertionError(f"workers disagree at target {target_types}: {by_pid}")
            return snapshots, consistent_rounds
        if any(types & terminal_types for types in by_pid.values()):
            raise AssertionError(f"run {run_id} reached terminal failure before {target_types}: {by_pid}")
        time.sleep(0.5)
    raise TimeoutError(f"timed out waiting for {target_types} on run {run_id}")


def event_types(events: list[dict[str, Any]]) -> list[str]:
    return [str(event.get("event_type")) for event in events]


def event_by_type(events: list[dict[str, Any]], event_type: str) -> dict[str, Any]:
    for event in events:
        if event.get("event_type") == event_type:
            return event
    raise AssertionError(f"event {event_type} is missing")


def final_text(events: list[dict[str, Any]]) -> str:
    return "".join(
        str((event.get("payload") or {}).get("delta") or "")
        for event in events
        if event.get("event_type") == "final.delta"
    )


def start_agent_run(
    session: Session,
    *,
    prompt: str,
    tool_id: str,
) -> str:
    status, profile = session.request(
        "GET",
        "/api/v1/configs/conversation_mode_profiles/agent",
    )
    expect_status(status, {200}, profile, "read Agent mode profile")
    revision_id = (profile or {}).get("revision_id")
    if not isinstance(revision_id, str) or not revision_id:
        raise AssertionError("Agent mode profile revision is missing")
    user_message_id = f"msg-user-{uuid.uuid4().hex}"
    assistant_message_id = f"msg-assistant-{uuid.uuid4().hex}"
    body = {
        "model": MODEL_ID,
        "chat_mode": "agent",
        "mode_profile_revision_id": revision_id,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "params": {"function_calling": "native", "temperature": 0},
        "features": {},
        "variables": {},
        "session_id": f"release-gate-{uuid.uuid4().hex}",
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
    status, payload = session.request("POST", "/api/chat/completions", body)
    expect_status(status, {200}, payload, "start agent run")
    run_id = (payload or {}).get("agent_run_id")
    if not (payload or {}).get("status") or not isinstance(run_id, str):
        raise AssertionError(f"chat did not create an Agent run: {payload!r}")
    return run_id


def create_tool(session: Session, tool_id: str) -> None:
    status, payload = session.request(
        "POST",
        "/api/v1/tools/create",
        {
            "id": tool_id,
            "name": "PR7 Four Worker Interaction Release Gate",
            "content": tool_source(),
            "meta": {
                "description": "Temporary no-side-effect approval and user-input fixture."
            },
            "access_grants": [],
        },
    )
    expect_status(status, {200}, payload, "create temporary interaction tool")


def create_cleanup_trigger_tool(session: Session, tool_id: str) -> None:
    status, payload = session.request(
        "POST",
        "/api/v1/tools/create",
        {
            "id": tool_id,
            "name": "PR7 Cleanup Agent Mode Trigger",
            "content": '''
class Tools:
    def release_gate_context(self, marker: str) -> dict:
        """Return a marker only; this tool cannot inspect, edit, or delete files.

        :param marker: A release-gate marker.
        """
        return {"status": "context-only", "marker": marker}
'''.strip(),
            "meta": {
                "description": "Temporary harmless Agent Mode trigger; not a filesystem tool."
            },
            "access_grants": [],
        },
    )
    expect_status(status, {200}, payload, "create temporary cleanup trigger")


def delete_tool(session: Session, tool_id: str) -> None:
    status, payload = session.request(
        "DELETE",
        f"/api/v1/tools/id/{urllib.parse.quote(tool_id, safe='')}/delete",
    )
    expect_status(status, {200}, payload, "delete temporary interaction tool")


def approval_decision(
    session: Session,
    *,
    run_id: str,
    approval_id: str,
    decision: str,
    idempotency_key: str,
) -> tuple[int, Any]:
    path = (
        f"/api/agent/runs/{urllib.parse.quote(run_id, safe='')}/approvals/"
        f"{urllib.parse.quote(approval_id, safe='')}/decision"
    )
    return session.request(
        "POST",
        path,
        {
            "run_id": run_id,
            "approval_id": approval_id,
            "decision": decision,
            "idempotency_key": idempotency_key,
        },
        idempotency_key=idempotency_key,
    )


def user_input_decision(
    session: Session,
    *,
    run_id: str,
    user_input_id: str,
    status_value: str,
    content: Any,
    idempotency_key: str,
) -> tuple[int, Any]:
    path = (
        f"/api/agent/runs/{urllib.parse.quote(run_id, safe='')}/user-input/"
        f"{urllib.parse.quote(user_input_id, safe='')}"
    )
    body = {
        "run_id": run_id,
        "user_input_id": user_input_id,
        "status": status_value,
        "idempotency_key": idempotency_key,
    }
    if content is not None:
        body["content"] = content
    return session.request(
        "POST",
        path,
        body,
        idempotency_key=idempotency_key,
    )


def run_approval_case(
    sessions: dict[int, Session],
    *,
    token: str,
    pids: list[int],
    tool_id: str,
    decision: str,
    start_pid: int,
    decision_pid: int,
    duplicate_pid: int,
    refresh_before_decision: bool = False,
) -> dict[str, Any]:
    case_ports_before, case_ports_after = refresh_sessions(
        sessions,
        token=token,
        pids=pids,
    )
    marker = f"APPROVAL-{decision.upper()}-{uuid.uuid4().hex[:10]}"
    prompt = (
        f"Release gate marker {marker}. Before any final answer, call protected_release_action exactly once "
        f"with operation write and marker {marker}. This fixture only simulates a protected action. "
        "Do not call request_user_input or any other tool. Wait for the approval decision. "
        + (
            f"After approval and tool success, give a final answer containing {marker}."
            if decision == "approved"
            else f"If approval is rejected, do not attempt another tool; give a final answer containing {marker} and the word rejected."
        )
    )
    run_id = start_agent_run(sessions[start_pid], prompt=prompt, tool_id=tool_id)
    waiting, waiting_rounds = wait_for_events(
        sessions,
        run_id,
        target_types={"approval.requested"},
    )
    events = waiting[start_pid]
    states = fetch_run_states(sessions, run_id)
    if set(states.values()) != {"waiting_approval"}:
        raise AssertionError(f"approval state mismatch: {states}")
    types = event_types(events)
    if any(value in types for value in {"tool.completed", "final.started", "run.completed"}):
        raise AssertionError(f"approval run advanced before decision: {types}")
    approval_event = event_by_type(events, "approval.requested")
    approval_id = (approval_event.get("payload") or {}).get("approval_id")
    if not isinstance(approval_id, str):
        raise AssertionError("approval.requested is missing approval_id")

    refresh_evidence = None
    if refresh_before_decision:
        before_ports, after_ports = refresh_sessions(sessions, token=token, pids=pids)
        refreshed = fetch_events(sessions, run_id)
        refreshed_fingerprints = {event_fingerprint(value) for value in refreshed.values()}
        refreshed_states = fetch_run_states(sessions, run_id)
        if len(refreshed_fingerprints) != 1 or set(refreshed_states.values()) != {
            "waiting_approval"
        }:
            raise AssertionError(
                f"approval refresh did not recover waiting state: {refreshed_states}"
            )
        refresh_evidence = {
            "before_local_ports": before_ports,
            "after_local_ports": after_ports,
            "states": refreshed_states,
            "event_count": len(next(iter(refreshed.values()))),
        }

    key = f"release-gate:approval:{decision}:{uuid.uuid4().hex}"
    status, response = approval_decision(
        sessions[decision_pid],
        run_id=run_id,
        approval_id=approval_id,
        decision=decision,
        idempotency_key=key,
    )
    expect_status(status, {200, 202}, response, f"{decision} approval")

    if decision == "approved":
        completed, completed_rounds = wait_for_events(
            sessions,
            run_id,
            target_types={"approval.completed", "tool.completed", "run.completed"},
        )
        terminal_events = completed[start_pid]
        text = final_text(terminal_events)
        if marker not in text:
            raise AssertionError(f"approved run final text is missing marker: {text!r}")
        expected_terminal = "run.completed"
    else:
        completed, completed_rounds = wait_for_events(
            sessions,
            run_id,
            target_types={"approval.completed", "run.completed"},
        )
        terminal_events = completed[start_pid]
        terminal_types = event_types(terminal_events)
        if "tool.completed" in terminal_types or "run.failed" in terminal_types:
            raise AssertionError(f"rejected approval executed or failed: {terminal_types}")
        text = final_text(terminal_events)
        if marker not in text or "rejected" not in text.lower():
            raise AssertionError(f"rejected approval final text is incomplete: {text!r}")
        expected_terminal = "run.completed"

    duplicate_status, duplicate_response = approval_decision(
        sessions[duplicate_pid],
        run_id=run_id,
        approval_id=approval_id,
        decision=decision,
        idempotency_key=key,
    )
    expect_status(duplicate_status, {200}, duplicate_response, "duplicate approval decision")
    if (duplicate_response or {}).get("execution_status") != "historical_completed":
        raise AssertionError(f"approval duplicate was not historical: {duplicate_response!r}")

    return {
        "marker": marker,
        "run_id": run_id,
        "approval_id": approval_id,
        "decision": decision,
        "start_worker_pid": start_pid,
        "decision_worker_pid": decision_pid,
        "duplicate_worker_pid": duplicate_pid,
        "initial_decision_status": status,
        "duplicate_status": duplicate_status,
        "duplicate_execution_status": (duplicate_response or {}).get("execution_status"),
        "waiting_states": states,
        "case_start_ports": {
            "before": case_ports_before,
            "after": case_ports_after,
        },
        "waiting_consistent_rounds": waiting_rounds,
        "completed_consistent_rounds": completed_rounds,
        "refresh_evidence": refresh_evidence,
        "event_types": event_types(terminal_events),
        "final_text": text,
        "expected_terminal": expected_terminal,
    }


def run_user_input_case(
    sessions: dict[int, Session],
    *,
    token: str,
    pids: list[int],
    tool_id: str,
    status_value: str,
    start_pid: int,
    decision_pid: int,
    duplicate_pid: int,
    refresh_before_decision: bool = False,
) -> dict[str, Any]:
    case_ports_before, case_ports_after = refresh_sessions(
        sessions,
        token=token,
        pids=pids,
    )
    marker = f"USER-INPUT-{status_value.upper()}-{uuid.uuid4().hex[:10]}"
    answer = f"ANSWER-{uuid.uuid4().hex[:10]}"
    prompt = (
        f"Release gate marker {marker}. Your first action must be one request_user_input call. "
        f"Ask exactly: Provide the release-gate answer for {marker}. "
        "Use requested_schema as an object with one required string property named answer, "
        "allow_cancel true, and timeout_seconds 300. Do not call another tool before the result. "
        + (
            "After an accepted result, call release_gate_marker exactly once with the returned answer, "
            f"then give a final answer containing {marker} and that answer."
            if status_value == "accepted"
            else f"If the result is {status_value}, do not call another tool; give a final answer containing {marker} and the word {status_value}."
        )
    )
    run_id = start_agent_run(sessions[start_pid], prompt=prompt, tool_id=tool_id)
    waiting, waiting_rounds = wait_for_events(
        sessions,
        run_id,
        target_types={"user_input.requested"},
    )
    events = waiting[start_pid]
    states = fetch_run_states(sessions, run_id)
    if set(states.values()) != {"waiting_user_input"}:
        raise AssertionError(f"user-input state mismatch: {states}")
    types = event_types(events)
    if any(value in types for value in {"final.started", "run.completed", "run.failed"}):
        raise AssertionError(f"user-input run advanced before response: {types}")
    input_event = event_by_type(events, "user_input.requested")
    user_input_id = (input_event.get("payload") or {}).get("user_input_id")
    if not isinstance(user_input_id, str):
        raise AssertionError("user_input.requested is missing user_input_id")

    refresh_evidence = None
    if refresh_before_decision:
        before_ports, after_ports = refresh_sessions(sessions, token=token, pids=pids)
        refreshed = fetch_events(sessions, run_id)
        refreshed_fingerprints = {event_fingerprint(value) for value in refreshed.values()}
        refreshed_states = fetch_run_states(sessions, run_id)
        if len(refreshed_fingerprints) != 1 or set(refreshed_states.values()) != {
            "waiting_user_input"
        }:
            raise AssertionError(
                f"user-input refresh did not recover waiting state: {refreshed_states}"
            )
        refresh_evidence = {
            "before_local_ports": before_ports,
            "after_local_ports": after_ports,
            "states": refreshed_states,
            "event_count": len(next(iter(refreshed.values()))),
        }

    key = f"release-gate:user-input:{status_value}:{uuid.uuid4().hex}"
    content = {"answer": answer} if status_value == "accepted" else None
    status, response = user_input_decision(
        sessions[decision_pid],
        run_id=run_id,
        user_input_id=user_input_id,
        status_value=status_value,
        content=content,
        idempotency_key=key,
    )
    expect_status(status, {200, 202}, response, f"{status_value} user input")

    terminal_event = {
        "accepted": "user_input.completed",
        "declined": "user_input.declined",
        "cancelled": "user_input.cancelled",
        "timeout": "user_input.expired",
    }[status_value]
    completed, completed_rounds = wait_for_events(
        sessions,
        run_id,
        target_types={terminal_event, "run.completed"},
    )
    terminal_events = completed[start_pid]
    text = final_text(terminal_events)
    if marker not in text:
        raise AssertionError(f"user-input final text is missing marker: {text!r}")
    if status_value == "accepted" and answer not in text:
        raise AssertionError(f"accepted answer is missing from final text: {text!r}")
    if status_value != "accepted" and status_value not in text.lower():
        raise AssertionError(f"{status_value} outcome is missing from final text: {text!r}")

    duplicate_status, duplicate_response = user_input_decision(
        sessions[duplicate_pid],
        run_id=run_id,
        user_input_id=user_input_id,
        status_value=status_value,
        content=content,
        idempotency_key=key,
    )
    expect_status(duplicate_status, {200}, duplicate_response, "duplicate user-input decision")
    if (duplicate_response or {}).get("execution_status") != "historical_completed":
        raise AssertionError(f"user-input duplicate was not historical: {duplicate_response!r}")

    return {
        "marker": marker,
        "answer": answer if status_value == "accepted" else None,
        "run_id": run_id,
        "user_input_id": user_input_id,
        "status": status_value,
        "start_worker_pid": start_pid,
        "decision_worker_pid": decision_pid,
        "duplicate_worker_pid": duplicate_pid,
        "initial_decision_status": status,
        "duplicate_status": duplicate_status,
        "duplicate_execution_status": (duplicate_response or {}).get("execution_status"),
        "waiting_states": states,
        "case_start_ports": {
            "before": case_ports_before,
            "after": case_ports_after,
        },
        "waiting_consistent_rounds": waiting_rounds,
        "completed_consistent_rounds": completed_rounds,
        "refresh_evidence": refresh_evidence,
        "event_types": event_types(terminal_events),
        "final_text": text,
    }


def run_terminal_cleanup() -> int:
    if not CLEANUP_PATH:
        raise AssertionError("CLEANUP_PATH is required")
    started = time.monotonic()
    pids = worker_pids()
    if len(pids) != 4:
        raise AssertionError(f"expected exactly four worker PIDs, got {pids}")
    token = create_admin_token()
    sessions = pin_sessions(token, pids)
    tool_id = f"pr7_cleanup_gate_{uuid.uuid4().hex[:12]}"
    marker = f"CLEANUP-{uuid.uuid4().hex[:10]}"
    result: dict[str, Any] = {
        "mode": "terminal_cleanup",
        "cleanup_path": CLEANUP_PATH,
        "marker": marker,
        "worker_pids": pids,
        "tool_id": tool_id,
    }
    created = False
    return_code = 1
    try:
        create_cleanup_trigger_tool(sessions[pids[0]], tool_id)
        created = True
        cleanup_command = f"rm -- {shlex.quote(CLEANUP_PATH)}"
        prompt = (
            f"Cleanup marker {marker}. Call run_command exactly once with command {cleanup_command!r}. "
            "The local release_gate_context tool cannot access files and must not be called. "
            "Do not call any other tool. Wait for approval. After successful deletion, "
            f"give a final answer containing {marker} and the exact path."
        )
        run_id = start_agent_run(sessions[pids[0]], prompt=prompt, tool_id=tool_id)
        result["run_id"] = run_id
        waiting, waiting_rounds = wait_for_events(
            sessions,
            run_id,
            target_types={"approval.requested"},
            terminal_types={
                "run.completed",
                "run.failed",
                "run.cancelled",
                "run.budget_exceeded",
            },
        )
        events = waiting[pids[0]]
        approval_event = event_by_type(events, "approval.requested")
        approval_payload = approval_event.get("payload") or {}
        approval_id = approval_payload.get("approval_id")
        requested_tool_id = str(approval_payload.get("tool_id") or "")
        if not isinstance(approval_id, str):
            raise AssertionError("cleanup approval is missing approval_id")
        if not requested_tool_id.startswith("tool:terminal:") or not requested_tool_id.endswith(
            ":run_command"
        ):
            raise AssertionError(f"cleanup selected unexpected tool: {requested_tool_id}")
        key = f"release-gate:cleanup:{uuid.uuid4().hex}"
        status, response = approval_decision(
            sessions[pids[2]],
            run_id=run_id,
            approval_id=approval_id,
            decision="approved",
            idempotency_key=key,
        )
        expect_status(status, {200, 202}, response, "cleanup approval")
        completed, completed_rounds = wait_for_events(
            sessions,
            run_id,
            target_types={"approval.completed", "tool.completed", "run.completed"},
        )
        terminal_events = completed[pids[0]]
        tool_event = event_by_type(terminal_events, "tool.completed")
        text = final_text(terminal_events)
        if marker not in text or CLEANUP_PATH not in text:
            raise AssertionError(f"cleanup final text is incomplete: {text!r}")
        result.update(
            {
                "ok": True,
                "approval_id": approval_id,
                "requested_tool_id": requested_tool_id,
                "decision_worker_pid": pids[2],
                "waiting_consistent_rounds": waiting_rounds,
                "completed_consistent_rounds": completed_rounds,
                "event_types": event_types(terminal_events),
                "tool_result": tool_event.get("payload"),
                "final_text": text,
            }
        )
        return_code = 0
    except Exception as exc:
        result["ok"] = False
        result["error"] = repr(exc)
    finally:
        if created:
            try:
                delete_tool(sessions[pids[0]], tool_id)
                result["tool_deleted"] = True
            except Exception as exc:
                result["tool_deleted"] = False
                result["tool_delete_error"] = repr(exc)
                result["ok"] = False
                return_code = 1
        for session in sessions.values():
            try:
                session.close()
            except Exception:
                pass
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return return_code


def main() -> int:
    if CLEANUP_PATH:
        return run_terminal_cleanup()
    started_at = dt.datetime.now(dt.timezone.utc).isoformat()
    started = time.monotonic()
    pids = worker_pids()
    if len(pids) != 4:
        raise AssertionError(f"expected exactly four worker PIDs, got {pids}")
    token = create_admin_token()
    sessions = pin_sessions(token, pids)
    tool_id = f"pr7_interaction_gate_{uuid.uuid4().hex[:12]}"
    result: dict[str, Any] = {
        "started_at": started_at,
        "model_id": MODEL_ID,
        "worker_pids": pids,
        "session_local_ports": {
            str(pid): session.local_port for pid, session in sessions.items()
        },
        "tool_id": tool_id,
    }
    created = False
    try:
        first, second, third, fourth = pids
        create_tool(sessions[first], tool_id)
        created = True
        result["approval_approved"] = run_approval_case(
            sessions,
            token=token,
            pids=pids,
            tool_id=tool_id,
            decision="approved",
            start_pid=first,
            decision_pid=third,
            duplicate_pid=fourth,
            refresh_before_decision=True,
        )
        result["approval_rejected"] = run_approval_case(
            sessions,
            token=token,
            pids=pids,
            tool_id=tool_id,
            decision="rejected",
            start_pid=second,
            decision_pid=fourth,
            duplicate_pid=first,
        )
        result["user_input_accepted"] = run_user_input_case(
            sessions,
            token=token,
            pids=pids,
            tool_id=tool_id,
            status_value="accepted",
            start_pid=third,
            decision_pid=first,
            duplicate_pid=second,
            refresh_before_decision=True,
        )
        result["user_input_cancelled"] = run_user_input_case(
            sessions,
            token=token,
            pids=pids,
            tool_id=tool_id,
            status_value="cancelled",
            start_pid=fourth,
            decision_pid=second,
            duplicate_pid=third,
        )
        result["ok"] = True
        return_code = 0
    except Exception as exc:
        result["ok"] = False
        result["error"] = repr(exc)
        return_code = 1
    finally:
        if created:
            try:
                delete_tool(sessions[pids[0]], tool_id)
                result["tool_deleted"] = True
            except Exception as exc:
                result["tool_deleted"] = False
                result["tool_delete_error"] = repr(exc)
                result["ok"] = False
                return_code = 1
        else:
            result["tool_deleted"] = None
        for session in sessions.values():
            try:
                session.close()
            except Exception:
                pass
        result["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
