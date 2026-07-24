from __future__ import annotations

import json
import os
import socket
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path


BASE_URL = "127.0.0.1"
PORT = 8080
ADMIN_USER_ID = os.environ.get("ADMIN_USER_ID", "b6826286-1251-4576-b3a0-e109ff085a61")


class HttpError(RuntimeError):
    pass


class Session:
    def __init__(self, token: str):
        self.sock = socket.create_connection((BASE_URL, PORT), timeout=10)
        self.sock.settimeout(20)
        self.token = token
        self.local_port = self.sock.getsockname()[1]

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass

    def request(self, method: str, path: str, body: object | None = None) -> tuple[int, object]:
        raw_body = b"" if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = [
            f"{method} {path} HTTP/1.1",
            "Host: localhost",
            f"Authorization: Bearer {self.token}",
            "Accept: application/json",
            "Connection: keep-alive",
        ]
        if body is not None:
            headers += ["Content-Type: application/json", f"Content-Length: {len(raw_body)}"]
        else:
            headers += ["Content-Length: 0"]
        self.sock.sendall(("\r\n".join(headers) + "\r\n\r\n").encode("ascii") + raw_body)

        data = b""
        while b"\r\n\r\n" not in data:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise HttpError(f"connection closed before headers for {method} {path}")
            data += chunk
        header_bytes, body_bytes = data.split(b"\r\n\r\n", 1)
        header_lines = header_bytes.split(b"\r\n")
        status = int(header_lines[0].split()[1])
        header_map = {}
        for line in header_lines[1:]:
            if b":" in line:
                key, value = line.split(b":", 1)
                header_map[key.strip().lower()] = value.strip().lower()

        if header_map.get(b"transfer-encoding") == b"chunked":
            body_bytes = self._read_chunked(body_bytes)
        else:
            length = int(header_map.get(b"content-length", b"0"))
            while len(body_bytes) < length:
                chunk = self.sock.recv(65536)
                if not chunk:
                    raise HttpError(f"connection closed before body for {method} {path}")
                body_bytes += chunk
            body_bytes = body_bytes[:length]

        if not body_bytes:
            payload: object = None
        else:
            try:
                payload = json.loads(body_bytes.decode("utf-8"))
            except Exception:
                payload = body_bytes.decode("utf-8", errors="replace")
        return status, payload

    def _read_chunked(self, initial: bytes) -> bytes:
        data = initial
        result = bytearray()
        while True:
            while b"\r\n" not in data:
                data += self.sock.recv(65536)
            line, data = data.split(b"\r\n", 1)
            size = int(line.split(b";", 1)[0], 16)
            if size == 0:
                return bytes(result)
            while len(data) < size + 2:
                data += self.sock.recv(65536)
            result.extend(data[:size])
            data = data[size + 2 :]


def token() -> str:
    from open_webui.utils.auth import create_token

    return create_token({"id": ADMIN_USER_ID}, expires_delta=timedelta(hours=2))


def worker_pids() -> list[int]:
    pids = []
    for path in Path("/proc").glob("[0-9]*"):
        try:
            command = (path / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if "multiprocessing.spawn" in command and "spawn_main" in command:
            pids.append(int(path.name))
    return sorted(pids)


def worker_socket_map(pids: list[int]) -> dict[int, set[int]]:
    result: dict[int, set[int]] = {pid: set() for pid in pids}
    for pid in pids:
        entries: dict[str, int] = {}
        for proc_net in (Path(f"/proc/{pid}/net/tcp"), Path(f"/proc/{pid}/net/tcp6")):
            try:
                lines = proc_net.read_text().splitlines()[1:]
            except OSError:
                continue
            for line in lines:
                fields = line.split()
                if len(fields) < 10 or fields[3] != "01":
                    continue
                local_port = int(fields[1].split(":", 1)[1], 16)
                remote_port = int(fields[2].split(":", 1)[1], 16)
                if local_port == PORT and remote_port:
                    entries[fields[9]] = remote_port
        for fd in (Path(f"/proc/{pid}/fd")).glob("*"):
            try:
                link = os.readlink(fd)
            except OSError:
                continue
            if link.startswith("socket:["):
                remote_port = entries.get(link[8:-1])
                if remote_port:
                    result[pid].add(remote_port)
    return result


def assign_sessions(sessions: list[Session], pids: list[int]) -> dict[int, Session]:
    for _ in range(20):
        mapping = worker_socket_map(pids)
        selected: dict[int, Session] = {}
        for pid, ports in mapping.items():
            for session in sessions:
                if session.local_port in ports:
                    selected[pid] = session
                    break
        if len(selected) == len(pids):
            return selected
        time.sleep(0.1)
    raise AssertionError(f"could not pin one keep-alive session per worker: {worker_socket_map(pids)}")


def pin_sessions(auth_token: str, pids: list[int]) -> tuple[list[Session], dict[int, Session]]:
    sessions = []
    last_error = None
    for _batch in range(12):
        for _ in range(64):
            session = Session(auth_token)
            sessions.append(session)
            expect(session, "GET", "/health")
        try:
            pinned = assign_sessions(sessions, pids)
            for session in sessions:
                if session not in pinned.values():
                    session.close()
            return list(pinned.values()), pinned
        except AssertionError as exc:
            last_error = exc
    for session in sessions:
        session.close()
    raise AssertionError(f"failed to cover all workers after {len(sessions)} sessions: {last_error}")


def expect(session: Session, method: str, path: str, body: object | None = None, status: int = 200) -> object:
    got, payload = session.request(method, path, body)
    if got != status:
        detail = payload if isinstance(payload, (str, dict, list)) else repr(payload)
        raise HttpError(f"{method} {path} returned {got}, expected {status}: {str(detail)[:400]}")
    return payload


def schema_default(payload: object, field: str = "version") -> object:
    if not isinstance(payload, dict):
        return None
    return ((payload.get("properties") or {}).get(field) or {}).get("default")


def model_ids(payload: object) -> set[str]:
    items = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return set()
    return {str(item.get("id")) for item in items if isinstance(item, dict) and item.get("id")}


def function_content(version: str) -> str:
    return f'''\nfrom pydantic import BaseModel\n\nclass Pipe:\n    class Valves(BaseModel):\n        version: str = {version!r}\n\n    def __init__(self):\n        self.valves = self.Valves()\n\n    async def pipe(self, body: dict):\n        return "cache-probe-function-{version}"\n'''.strip()


def tool_content(version: str) -> str:
    return f'''\nfrom pydantic import BaseModel\n\nclass Tools:\n    class Valves(BaseModel):\n        version: str = {version!r}\n\n    def __init__(self):\n        self.valves = self.Valves()\n\n    def cache_probe_tool(self, marker: str) -> dict:\n        return {{"marker": marker, "version": {version!r}}}\n'''.strip()


def all_values(sessions_by_pid: dict[int, Session], method: str, path: str) -> dict[int, object]:
    with ThreadPoolExecutor(max_workers=len(sessions_by_pid)) as executor:
        futures = {
            pid: executor.submit(expect, session, method, path)
            for pid, session in sessions_by_pid.items()
        }
        return {pid: future.result() for pid, future in futures.items()}


def wait_until(label: str, sessions_by_pid: dict[int, Session], check, timeout: float = 20.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = all_values(sessions_by_pid, "GET", "/api/v1/configs/connections") if label == "config" else None
        if label == "config":
            if all(check(value) for value in last.values()):
                return last
        else:
            with ThreadPoolExecutor(max_workers=len(sessions_by_pid)) as executor:
                futures = {pid: executor.submit(check, session) for pid, session in sessions_by_pid.items()}
                values = {pid: future.result() for pid, future in futures.items()}
            if all(value[0] for value in values.values()):
                return {pid: value[1] for pid, value in values.items()}
            last = values
        time.sleep(0.25)
    raise AssertionError(f"timeout waiting for {label}: {last}")


def main() -> int:
    result = {
        "ok": False,
        "worker_pids": [],
        "worker_session_ports": {},
        "checks": {},
    }
    sessions: list[Session] = []
    function_id = f"acceptance_cache_function_{uuid.uuid4().hex[:10]}"
    tool_id = f"acceptance_cache_tool_{uuid.uuid4().hex[:10]}"
    function_created = False
    function_active = False
    tool_created = False
    original_connections = None
    try:
        pids = worker_pids()
        result["worker_pids"] = pids
        if len(pids) != 4:
            raise AssertionError(f"expected 4 worker pids, got {pids}")
        auth_token = token()
        sessions, pinned = pin_sessions(auth_token, pids)
        result["worker_session_ports"] = {str(pid): session.local_port for pid, session in pinned.items()}

        # Global config cache: toggle one safe boolean and restore it in finally.
        before_config = all_values(pinned, "GET", "/api/v1/configs/connections")
        original_connections = next(iter(before_config.values()))
        toggle_value = not bool(original_connections["ENABLE_BASE_MODELS_CACHE"])
        updated = expect(
            pinned[pids[0]],
            "POST",
            "/api/v1/configs/connections",
            {
                "ENABLE_DIRECT_CONNECTIONS": bool(original_connections["ENABLE_DIRECT_CONNECTIONS"]),
                "ENABLE_BASE_MODELS_CACHE": toggle_value,
            },
        )
        deadline = time.monotonic() + 20
        after_config = {}
        while time.monotonic() < deadline:
            after_config = all_values(pinned, "GET", "/api/v1/configs/connections")
            if all(bool(value["ENABLE_BASE_MODELS_CACHE"]) == toggle_value for value in after_config.values()):
                break
            time.sleep(0.25)
        else:
            raise AssertionError(f"config did not converge: {after_config}")
        result["checks"]["config_toggle_all_workers"] = {
            "before": before_config,
            "updated": updated,
            "after": after_config,
            "consistent": len({json.dumps(value, sort_keys=True) for value in after_config.values()}) == 1,
        }

        for session in sessions:
            session.close()
        sessions, pinned = pin_sessions(auth_token, pids)

        # Function create -> model cache appearance -> module/content schema update -> delete.
        function_body = {
            "id": function_id,
            "name": "Acceptance cache function",
            "content": function_content("v1"),
            "meta": {"description": "temporary four-worker cache probe"},
        }
        expect(pinned[pids[0]], "POST", "/api/v1/functions/create", function_body)
        function_created = True
        expect(pinned[pids[0]], "POST", f"/api/v1/functions/id/{function_id}/toggle")
        function_active = True

        for session in sessions:
            session.close()
        sessions, pinned = pin_sessions(auth_token, pids)

        def function_model_check(session: Session):
            status, payload = session.request("GET", "/api/models")
            return status == 200 and function_id in model_ids(payload), model_ids(payload)

        model_after_create = wait_until("function model appears", pinned, function_model_check)
        initial_specs = all_values(pinned, "GET", f"/api/v1/functions/id/{function_id}/valves/spec")
        if any(schema_default(value) != "v1" for value in initial_specs.values()):
            raise AssertionError(f"initial function schemas inconsistent: {initial_specs}")
        expect(
            pinned[pids[0]],
            "POST",
            f"/api/v1/functions/id/{function_id}/update",
            {**function_body, "content": function_content("v2")},
        )

        for session in sessions:
            session.close()
        sessions, pinned = pin_sessions(auth_token, pids)

        def function_schema_check(session: Session):
            status, payload = session.request("GET", f"/api/v1/functions/id/{function_id}/valves/spec")
            return status == 200 and schema_default(payload) == "v2", schema_default(payload)

        function_after_update = wait_until("function module/content cache", pinned, function_schema_check)
        expect(pinned[pids[0]], "DELETE", f"/api/v1/functions/id/{function_id}/delete")
        function_created = False
        function_active = False

        for session in sessions:
            session.close()
        sessions, pinned = pin_sessions(auth_token, pids)

        def function_model_gone(session: Session):
            status, payload = session.request("GET", "/api/models")
            return status == 200 and function_id not in model_ids(payload), model_ids(payload)

        model_after_delete = wait_until("function model disappears", pinned, function_model_gone)
        result["checks"]["function_and_model_cache"] = {
            "model_after_create": {str(pid): sorted(value) for pid, value in model_after_create.items()},
            "initial_schema_defaults": {str(pid): schema_default(value) for pid, value in initial_specs.items()},
            "schema_after_update": {str(pid): value for pid, value in function_after_update.items()},
            "model_after_delete": {str(pid): sorted(value) for pid, value in model_after_delete.items()},
        }

        # Tool create -> module/content schema update -> delete.
        tool_body = {
            "id": tool_id,
            "name": "Acceptance cache tool",
            "content": tool_content("t1"),
            "meta": {"description": "temporary four-worker cache probe"},
            "access_grants": [],
        }
        expect(pinned[pids[0]], "POST", "/api/v1/tools/create", tool_body)
        tool_created = True

        for session in sessions:
            session.close()
        sessions, pinned = pin_sessions(auth_token, pids)
        initial_tool_specs = all_values(pinned, "GET", f"/api/v1/tools/id/{tool_id}/valves/spec")
        if any(schema_default(value) != "t1" for value in initial_tool_specs.values()):
            raise AssertionError(f"initial tool schemas inconsistent: {initial_tool_specs}")
        expect(
            pinned[pids[0]],
            "POST",
            f"/api/v1/tools/id/{tool_id}/update",
            {**tool_body, "content": tool_content("t2")},
        )

        for session in sessions:
            session.close()
        sessions, pinned = pin_sessions(auth_token, pids)

        def tool_schema_check(session: Session):
            status, payload = session.request("GET", f"/api/v1/tools/id/{tool_id}/valves/spec")
            return status == 200 and schema_default(payload) == "t2", schema_default(payload)

        tool_after_update = wait_until("tool module/content cache", pinned, tool_schema_check)
        expect(pinned[pids[0]], "DELETE", f"/api/v1/tools/id/{tool_id}/delete")
        tool_created = False

        for session in sessions:
            session.close()
        sessions, pinned = pin_sessions(auth_token, pids)

        def tool_gone(session: Session):
            status, payload = session.request("GET", f"/api/v1/tools/id/{tool_id}/valves/spec")
            return status == 404, status

        tool_after_delete = wait_until("tool deletion cache", pinned, tool_gone)
        result["checks"]["tool_cache"] = {
            "initial_schema_defaults": {str(pid): schema_default(value) for pid, value in initial_tool_specs.items()},
            "schema_after_update": {str(pid): value for pid, value in tool_after_update.items()},
            "after_delete_status": {str(pid): value for pid, value in tool_after_delete.items()},
        }
        result["ok"] = True
    except Exception as exc:
        result["error"] = repr(exc)
        result["traceback_tail"] = traceback.format_exc().splitlines()[-8:]
    finally:
        cleanup_session = None
        try:
            if sessions:
                auth_token = locals().get("auth_token")
                cleanup_session = Session(auth_token) if auth_token else sessions[0]
                if tool_created:
                    cleanup_session.request("DELETE", f"/api/v1/tools/id/{tool_id}/delete")
                if function_created:
                    if function_active:
                        cleanup_session.request("POST", f"/api/v1/functions/id/{function_id}/toggle")
                    cleanup_session.request("DELETE", f"/api/v1/functions/id/{function_id}/delete")
                if original_connections is not None:
                    cleanup_session.request(
                        "POST",
                        "/api/v1/configs/connections",
                        {
                            "ENABLE_DIRECT_CONNECTIONS": bool(original_connections["ENABLE_DIRECT_CONNECTIONS"]),
                            "ENABLE_BASE_MODELS_CACHE": bool(original_connections["ENABLE_BASE_MODELS_CACHE"]),
                        },
                    )
        except Exception as cleanup_exc:
            result["cleanup_error"] = repr(cleanup_exc)
            result["ok"] = False
        if cleanup_session is not None and cleanup_session not in sessions:
            cleanup_session.close()
        for session in sessions:
            session.close()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
