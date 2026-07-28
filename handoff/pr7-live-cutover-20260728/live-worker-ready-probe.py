#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import socket
import sys
import time
from pathlib import Path


HOST = '127.0.0.1'
PORT = 8080
EXPECTED_WORKERS = 4


class Session:
    def __init__(self) -> None:
        self.socket = socket.create_connection((HOST, PORT), timeout=5)
        self.socket.settimeout(10)
        self.local_port = self.socket.getsockname()[1]

    def health(self) -> None:
        self.socket.sendall(
            b'GET /health HTTP/1.1\r\n'
            b'Host: localhost\r\n'
            b'Accept: application/json\r\n'
            b'Connection: keep-alive\r\n'
            b'Content-Length: 0\r\n\r\n'
        )
        data = b''
        while b'\r\n\r\n' not in data:
            chunk = self.socket.recv(65536)
            if not chunk:
                raise RuntimeError('connection closed before response headers')
            data += chunk
        headers, body = data.split(b'\r\n\r\n', 1)
        status = int(headers.split(b'\r\n', 1)[0].split()[1])
        if status != 200:
            raise RuntimeError(f'health returned HTTP {status}')
        content_length = 0
        for line in headers.split(b'\r\n')[1:]:
            if line.lower().startswith(b'content-length:'):
                content_length = int(line.split(b':', 1)[1].strip())
        while len(body) < content_length:
            body += self.socket.recv(65536)

    def close(self) -> None:
        self.socket.close()


def worker_pids() -> list[int]:
    pids = []
    for path in Path('/proc').glob('[0-9]*'):
        try:
            command = (path / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
        except OSError:
            continue
        if 'multiprocessing.spawn' in command and 'spawn_main' in command:
            pids.append(int(path.name))
    return sorted(pids)


def worker_ports(pids: list[int]) -> dict[int, set[int]]:
    result = {pid: set() for pid in pids}
    for pid in pids:
        sockets = {}
        for table in (Path(f'/proc/{pid}/net/tcp'), Path(f'/proc/{pid}/net/tcp6')):
            try:
                rows = table.read_text().splitlines()[1:]
            except OSError:
                continue
            for row in rows:
                fields = row.split()
                if len(fields) < 10 or fields[3] != '01':
                    continue
                local_port = int(fields[1].split(':', 1)[1], 16)
                remote_port = int(fields[2].split(':', 1)[1], 16)
                if local_port == PORT and remote_port:
                    sockets[fields[9]] = remote_port
        for fd in Path(f'/proc/{pid}/fd').glob('*'):
            try:
                link = os.readlink(fd)
            except OSError:
                continue
            if link.startswith('socket:['):
                remote_port = sockets.get(link[8:-1])
                if remote_port:
                    result[pid].add(remote_port)
    return result


def main() -> None:
    target_pid = int(sys.argv[1]) if len(sys.argv) > 1 else None
    pids = worker_pids()
    if len(pids) != EXPECTED_WORKERS:
        raise RuntimeError(f'expected four workers, got {pids}')
    if target_pid is not None and target_pid not in pids:
        raise RuntimeError(f'target worker {target_pid} is not in {pids}')

    sessions = []
    selected = {}
    try:
        for _ in range(8):
            for _ in range(16):
                session = Session()
                session.health()
                sessions.append(session)
            for _ in range(20):
                mapping = worker_ports(pids)
                selected = {}
                for pid, ports in mapping.items():
                    for session in sessions:
                        if session.local_port in ports:
                            selected[pid] = session
                            break
                coverage_reached = (
                    target_pid in selected
                    if target_pid is not None
                    else len(selected) == len(pids)
                )
                if coverage_reached:
                    break
                time.sleep(0.1)
            coverage_reached = (
                target_pid in selected
                if target_pid is not None
                else len(selected) == len(pids)
            )
            if coverage_reached:
                break

        ready = target_pid in selected if target_pid is not None else len(selected) == len(pids)
        if not ready:
            raise RuntimeError(
                f'target worker did not accept health requests: target={target_pid} mapping={worker_ports(pids)}'
            )
        if target_pid is not None:
            selected[target_pid].health()
        else:
            for session in selected.values():
                session.health()
        print(
            json.dumps(
                {
                    'ok': True,
                    'target_worker_pid': target_pid,
                    'worker_pids': pids,
                    'worker_session_ports': {
                        str(pid): selected[pid].local_port for pid in pids
                    },
                    'request_count_lower_bound': len(sessions) + len(selected),
                },
                separators=(',', ':'),
            )
        )
    finally:
        for session in sessions:
            session.close()


if __name__ == '__main__':
    main()
