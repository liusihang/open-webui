#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path

HOST = '127.0.0.1'
PORT = 8080
EXPECTED_WORKERS = 4
TOKEN_PATH = Path('/tmp/pr7-announcement-admin.token')


class Session:
    def __init__(self, token: str) -> None:
        self.socket = socket.create_connection((HOST, PORT), timeout=5)
        self.socket.settimeout(10)
        self.local_port = self.socket.getsockname()[1]
        self.token = token

    def request(self, path: str) -> int:
        request = (
            f'GET {path} HTTP/1.1\r\n'
            'Host: localhost\r\n'
            'Accept: application/json\r\n'
            f'Authorization: Bearer {self.token}\r\n'
            'Connection: keep-alive\r\n'
            'Content-Length: 0\r\n\r\n'
        ).encode()
        self.socket.sendall(request)
        data = b''
        while b'\r\n\r\n' not in data:
            data += self.socket.recv(65536)
        headers, body = data.split(b'\r\n\r\n', 1)
        status = int(headers.split(b'\r\n', 1)[0].split()[1])
        content_length = next(
            (
                int(line.split(b':', 1)[1].strip())
                for line in headers.split(b'\r\n')[1:]
                if line.lower().startswith(b'content-length:')
            ),
            0,
        )
        while len(body) < content_length:
            body += self.socket.recv(65536)
        return status

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
    token = TOKEN_PATH.read_text().strip()
    pids = worker_pids()
    if len(pids) != EXPECTED_WORKERS:
        raise RuntimeError(f'expected four workers, got {pids}')

    sessions: list[Session] = []
    observations: dict[int, list[dict]] = {pid: [] for pid in pids}
    try:
        for _ in range(12):
            for _ in range(16):
                session = Session(token)
                auth_status = session.request('/api/v1/auths/')
                config_status = session.request('/api/config')
                sessions.append(session)
                for _ in range(20):
                    mapping = worker_ports(pids)
                    matched_pid = next(
                        (pid for pid, ports in mapping.items() if session.local_port in ports),
                        None,
                    )
                    if matched_pid is not None:
                        observations[matched_pid].append(
                            {'auth': auth_status, 'config': config_status}
                        )
                        break
                    time.sleep(0.05)
            if all(observations.values()):
                break

        if not all(observations.values()):
            raise RuntimeError(f'worker coverage incomplete: {observations}')
        summary = {
            str(pid): {
                'requests': len(rows),
                'auth_statuses': sorted({row['auth'] for row in rows}),
                'config_statuses': sorted({row['config'] for row in rows}),
            }
            for pid, rows in observations.items()
        }
        print(json.dumps({'worker_auth_matrix': summary}, separators=(',', ':')))
    finally:
        for session in sessions:
            session.close()


if __name__ == '__main__':
    main()
