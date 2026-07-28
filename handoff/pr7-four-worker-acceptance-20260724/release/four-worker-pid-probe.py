from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path


HOST = "127.0.0.1"
PORT = 8080
EXPECTED_WORKERS = 4


class Session:
    def __init__(self) -> None:
        self.sock = socket.create_connection((HOST, PORT), timeout=10)
        self.sock.settimeout(30)
        self.local_port = self.sock.getsockname()[1]

    def health(self) -> None:
        self.sock.sendall(
            b"GET /health HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Accept: application/json\r\n"
            b"Connection: keep-alive\r\n"
            b"Content-Length: 0\r\n\r\n"
        )
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("connection closed before health response headers")
            data += chunk
        headers, body = data.split(b"\r\n\r\n", 1)
        status = int(headers.split(b"\r\n", 1)[0].split()[1])
        if status != 200:
            raise RuntimeError(f"health returned HTTP {status}")
        content_length = 0
        for line in headers.split(b"\r\n")[1:]:
            if line.lower().startswith(b"content-length:"):
                content_length = int(line.split(b":", 1)[1].strip())
        while len(body) < content_length:
            body += self.sock.recv(65536)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def worker_pids() -> list[int]:
    pids: list[int] = []
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
        sockets: dict[str, int] = {}
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
                    sockets[fields[9]] = remote_port
        for fd in Path(f"/proc/{pid}/fd").glob("*"):
            try:
                link = os.readlink(fd)
            except OSError:
                continue
            if link.startswith("socket:["):
                remote_port = sockets.get(link[8:-1])
                if remote_port:
                    result[pid].add(remote_port)
    return result


def main() -> int:
    pids = worker_pids()
    if len(pids) != EXPECTED_WORKERS:
        raise AssertionError(f"expected {EXPECTED_WORKERS} worker PIDs, got {pids}")

    sessions: list[Session] = []
    selected: dict[int, Session] = {}
    try:
        for _ in range(12):
            for _ in range(64):
                session = Session()
                session.health()
                sessions.append(session)
            for _ in range(20):
                mapping = worker_socket_map(pids)
                selected = {}
                for pid, ports in mapping.items():
                    for session in sessions:
                        if session.local_port in ports:
                            selected[pid] = session
                            break
                if len(selected) == len(pids):
                    break
                time.sleep(0.1)
            if len(selected) == len(pids):
                break
        if len(selected) != len(pids):
            raise AssertionError(f"failed to pin a keep-alive connection to every worker: {worker_socket_map(pids)}")
        for session in selected.values():
            session.health()
        print(
            json.dumps(
                {
                    "ok": True,
                    "worker_pids": pids,
                    "worker_session_ports": {
                        str(pid): selected[pid].local_port for pid in pids
                    },
                    "request_count_lower_bound": len(sessions) + len(selected),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    finally:
        for session in sessions:
            session.close()


if __name__ == "__main__":
    raise SystemExit(main())
