#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import socket
import time
from pathlib import Path

HOST = '127.0.0.1'
PORT = 8080
EXPECTED_WORKERS = 4
TOKEN_PATH = Path('/tmp/pr7-announcement-admin.token')
EXPECTED_CONFIG_PATH = Path('/tmp/pr7-announcement-expected-config.json')


def expected_announcement() -> dict:
    config = json.loads(EXPECTED_CONFIG_PATH.read_text())
    return {
        'enabled': config['ANNOUNCEMENT_MODAL_ENABLED'],
        'key': config['ANNOUNCEMENT_MODAL_KEY'],
        'title': config['ANNOUNCEMENT_MODAL_TITLE'],
        'content': config['ANNOUNCEMENT_MODAL_CONTENT'],
    }


class Session:
    def __init__(self, token: str) -> None:
        self.socket = socket.create_connection((HOST, PORT), timeout=5)
        self.socket.settimeout(10)
        self.local_port = self.socket.getsockname()[1]
        self.token = token

    def app_config(self) -> dict:
        request = (
            'GET /api/config HTTP/1.1\r\n'
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
        if status != 200:
            raise RuntimeError(f'/api/config returned HTTP {status}')
        content_length = next(
            int(line.split(b':', 1)[1].strip())
            for line in headers.split(b'\r\n')[1:]
            if line.lower().startswith(b'content-length:')
        )
        while len(body) < content_length:
            body += self.socket.recv(65536)
        return json.loads(body[:content_length])

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


def worker_ports(pids: list[int]) -> dict[int, set[int]]:  # noqa: C901
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
    expected = expected_announcement()
    pids = worker_pids()
    if len(pids) != EXPECTED_WORKERS:
        raise RuntimeError(f'expected four workers, got {pids}')

    sessions: list[Session] = []
    responses: dict[int, dict] = {}
    selected_ports: dict[int, int] = {}
    try:
        for _ in range(8):
            for _ in range(16):
                session = Session(token)
                config = session.app_config()
                sessions.append(session)
                for _ in range(20):
                    mapping = worker_ports(pids)
                    matched_pid = next(
                        (pid for pid, ports in mapping.items() if session.local_port in ports),
                        None,
                    )
                    if matched_pid is not None:
                        announcement = config.get('ui', {}).get('announcement_modal')
                        if announcement != expected:
                            raise RuntimeError(f'worker {matched_pid} returned inconsistent announcement')
                        responses[matched_pid] = announcement
                        selected_ports[matched_pid] = session.local_port
                        break
                    time.sleep(0.05)
            if len(responses) == EXPECTED_WORKERS:
                break

        if len(responses) != EXPECTED_WORKERS:
            raise RuntimeError(f'only covered workers {sorted(responses)} of {pids}')
        announcement_sha = hashlib.sha256(
            json.dumps(expected, sort_keys=True, separators=(',', ':')).encode()
        ).hexdigest()
        print(
            json.dumps(
                {
                    'ok': True,
                    'worker_pids': pids,
                    'worker_session_ports': selected_ports,
                    'announcement_sha256': announcement_sha,
                    'request_count_lower_bound': len(sessions),
                },
                separators=(',', ':'),
            )
        )
    finally:
        for session in sessions:
            session.close()


if __name__ == '__main__':
    main()
