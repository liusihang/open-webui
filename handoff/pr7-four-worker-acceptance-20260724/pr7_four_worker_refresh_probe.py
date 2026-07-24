from __future__ import annotations

import json
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path


RUN_ID = os.environ['RUN_ID']
PORT = 8080
ADMIN_USER_ID = os.environ.get('ADMIN_USER_ID', 'b6826286-1251-4576-b3a0-e109ff085a61')


class Session:
    def __init__(self, token: str):
        self.sock = socket.create_connection(('127.0.0.1', PORT), timeout=20)
        self.sock.settimeout(20)
        self.local_port = self.sock.getsockname()[1]
        self.token = token

    def close(self):
        self.sock.close()

    def get_events(self):
        path = f'/api/agent/runs/{RUN_ID}/events/list'
        request = (
            f'GET {path} HTTP/1.1\r\n'
            'Host: localhost\r\n'
            f'Authorization: Bearer {self.token}\r\n'
            'Accept: application/json\r\n'
            'Connection: keep-alive\r\n'
            'Content-Length: 0\r\n\r\n'
        ).encode()
        self.sock.sendall(request)
        data = b''
        while b'\r\n\r\n' not in data:
            data += self.sock.recv(65536)
        headers, body = data.split(b'\r\n\r\n', 1)
        header_map = {}
        for line in headers.split(b'\r\n')[1:]:
            if b':' in line:
                key, value = line.split(b':', 1)
                header_map[key.lower()] = value.strip().lower()
        if header_map.get(b'transfer-encoding') == b'chunked':
            chunks = bytearray()
            while True:
                while b'\r\n' not in body:
                    body += self.sock.recv(65536)
                line, body = body.split(b'\r\n', 1)
                size = int(line.split(b';', 1)[0], 16)
                if size == 0:
                    body = bytes(chunks)
                    break
                while len(body) < size + 2:
                    body += self.sock.recv(65536)
                chunks.extend(body[:size])
                body = body[size + 2:]
        else:
            length = int(header_map.get(b'content-length', b'0'))
            while len(body) < length:
                body += self.sock.recv(65536)
            body = body[:length]
        return json.loads(body.decode())


def token() -> str:
    from open_webui.utils.auth import create_token

    return create_token({'id': ADMIN_USER_ID}, expires_delta=timedelta(hours=2))


def worker_pids() -> list[int]:
    result = []
    for path in Path('/proc').glob('[0-9]*'):
        try:
            command = path.joinpath('cmdline').read_bytes().replace(b'\0', b' ').decode()
        except OSError:
            continue
        if 'multiprocessing.spawn' in command and 'spawn_main' in command:
            result.append(int(path.name))
    return sorted(result)


def worker_ports(pids: list[int]) -> dict[int, set[int]]:
    result = {pid: set() for pid in pids}
    for pid in pids:
        entries = {}
        try:
            lines = Path(f'/proc/{pid}/net/tcp').read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 10 or fields[3] != '01':
                continue
            local_port = int(fields[1].split(':', 1)[1], 16)
            if local_port == PORT:
                entries[fields[9]] = int(fields[2].split(':', 1)[1], 16)
        for fd in Path(f'/proc/{pid}/fd').glob('*'):
            try:
                link = os.readlink(fd)
            except OSError:
                continue
            if link.startswith('socket:[') and link[8:-1] in entries:
                result[pid].add(entries[link[8:-1]])
    return result


def pin(token_value: str, pids: list[int]) -> dict[int, Session]:
    sessions = []
    try:
        for _ in range(256):
            session = Session(token_value)
            sessions.append(session)
            session.get_events()
            mapping = worker_ports(pids)
            selected = {}
            for pid, ports in mapping.items():
                for candidate in sessions:
                    if candidate.local_port in ports:
                        selected[pid] = candidate
                        break
            if len(selected) == len(pids):
                for candidate in sessions:
                    if candidate not in selected.values():
                        candidate.close()
                return selected
        raise RuntimeError(f'worker coverage incomplete: {worker_ports(pids)}')
    except Exception:
        for session in sessions:
            try:
                session.close()
            except Exception:
                pass
        raise


def fingerprint(payload: dict) -> tuple:
    events = payload.get('events') or []
    return tuple(
        (
            event.get('seq'),
            event.get('event_type'),
            event.get('phase'),
            (event.get('payload') or {}).get('delta_index'),
        )
        for event in events
    )


def main() -> None:
    pids = worker_pids()
    sessions = pin(token(), pids)
    try:
        rounds = []
        for _ in range(5):
            with ThreadPoolExecutor(max_workers=len(sessions)) as executor:
                values = list(executor.map(lambda session: session.get_events(), sessions.values()))
            fingerprints = [fingerprint(value) for value in values]
            rounds.append(
                {
                    'worker_event_counts': [len(value.get('events') or []) for value in values],
                    'worker_final_delta_counts': [
                        sum(1 for event in (value.get('events') or []) if event.get('event_type') == 'final.delta')
                        for value in values
                    ],
                    'consistent': len(set(fingerprints)) == 1,
                }
            )
            time.sleep(0.1)
        print(json.dumps({'run_id': RUN_ID, 'worker_pids': pids, 'rounds': rounds}, indent=2))
    finally:
        for session in sessions.values():
            session.close()


if __name__ == '__main__':
    main()
