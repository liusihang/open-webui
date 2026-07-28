from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

HOST = '127.0.0.1'
PORT = 8080
ADMIN_USER_ID = os.environ.get(
    'ADMIN_USER_ID',
    'b6826286-1251-4576-b3a0-e109ff085a61',
)
WAIT_SECONDS = float(os.environ.get('WAIT_SECONDS', '90'))


class Session:
    def __init__(self, token: str):
        self.sock = socket.create_connection((HOST, PORT), timeout=15)
        self.sock.settimeout(120)
        self.local_port = self.sock.getsockname()[1]
        self.token = token

    def close(self) -> None:
        self.sock.close()

    def request(  # noqa: C901
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        encoded = b'' if body is None else json.dumps(body).encode('utf-8')
        headers = [
            f'{method} {path} HTTP/1.1',
            'Host: localhost',
            f'Authorization: Bearer {self.token}',
            'Accept: application/json',
            'Connection: keep-alive',
            f'Content-Length: {len(encoded)}',
        ]
        if body is not None:
            headers.append('Content-Type: application/json')
        self.sock.sendall(('\r\n'.join(headers) + '\r\n\r\n').encode() + encoded)

        data = b''
        while b'\r\n\r\n' not in data:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError(f'connection closed while reading {method} {path}')
            data += chunk
        raw_headers, response_body = data.split(b'\r\n\r\n', 1)
        lines = raw_headers.split(b'\r\n')
        status = int(lines[0].split()[1])
        header_map: dict[bytes, bytes] = {}
        for line in lines[1:]:
            if b':' in line:
                key, value = line.split(b':', 1)
                header_map[key.lower()] = value.strip().lower()

        if header_map.get(b'transfer-encoding') == b'chunked':
            chunks = bytearray()
            while True:
                while b'\r\n' not in response_body:
                    response_body += self.sock.recv(65536)
                line, response_body = response_body.split(b'\r\n', 1)
                size = int(line.split(b';', 1)[0], 16)
                if size == 0:
                    response_body = bytes(chunks)
                    break
                while len(response_body) < size + 2:
                    response_body += self.sock.recv(65536)
                chunks.extend(response_body[:size])
                response_body = response_body[size + 2 :]
        else:
            length = int(header_map.get(b'content-length', b'0'))
            while len(response_body) < length:
                response_body += self.sock.recv(65536)
            response_body = response_body[:length]

        if not response_body:
            return status, None
        return status, json.loads(response_body.decode('utf-8'))


def create_token() -> str:
    from open_webui.utils.auth import create_token as issue_token

    return issue_token({'id': ADMIN_USER_ID}, expires_delta=dt.timedelta(hours=1))


def worker_pids() -> list[int]:
    result: list[int] = []
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
        sockets: dict[str, int] = {}
        for net_path in (Path(f'/proc/{pid}/net/tcp'), Path(f'/proc/{pid}/net/tcp6')):
            try:
                lines = net_path.read_text().splitlines()[1:]
            except OSError:
                continue
            for line in lines:
                fields = line.split()
                if len(fields) < 10 or fields[3] != '01':
                    continue
                local_port = int(fields[1].split(':', 1)[1], 16)
                remote_port = int(fields[2].split(':', 1)[1], 16)
                if local_port == PORT and remote_port:
                    sockets[fields[9]] = remote_port
        for fd in Path(f'/proc/{pid}/fd').glob('*'):
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            if target.startswith('socket:[') and target[8:-1] in sockets:
                result[pid].add(sockets[target[8:-1]])
    return result


def expect(
    session: Session,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    expected: tuple[int, ...] = (200,),
) -> Any:
    status, payload = session.request(method, path, body)
    if status not in expected:
        raise AssertionError(f'{method} {path} returned {status}')
    return payload


def pin_sessions(  # noqa: C901
    token: str, pids: list[int]
) -> tuple[list[Session], dict[int, Session]]:
    sessions: list[Session] = []
    try:
        for _ in range(512):
            session = Session(token)
            sessions.append(session)
            expect(session, 'GET', '/health')
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
                        expect(candidate, 'GET', '/health')
                    except Exception:
                        candidate.close()
                        expired.add(candidate)
                if expired:
                    sessions = [candidate for candidate in sessions if candidate not in expired]
                    continue
                retained = list(selected.values())
                for candidate in sessions:
                    if candidate not in retained:
                        candidate.close()
                return retained, dict(sorted(selected.items()))
        raise AssertionError(f'worker coverage incomplete: {worker_ports(pids)}')
    except Exception:
        for session in sessions:
            try:
                session.close()
            except Exception:
                pass
        raise


def public_agent(payload: Any) -> dict[str, Any]:
    profiles = payload['conversation_mode_profiles']
    if isinstance(profiles, dict):
        return profiles['agent']
    return next(profile for profile in profiles if profile['mode'] == 'agent')


def snapshot(session: Session) -> dict[str, Any]:
    private = expect(session, 'GET', '/api/v1/configs/conversation_mode_profiles/agent')
    public = public_agent(expect(session, 'GET', '/api/config'))
    prompt = private.get('system_prompt')
    prompt_text = prompt if isinstance(prompt, str) else ''
    return {
        'private_revision': private.get('revision_id'),
        'public_revision': public.get('current_revision_id'),
        'public_defaults': public.get('defaults'),
        'public_has_prompt': 'system_prompt' in json.dumps(public).lower(),
        'prompt_length': len(prompt_text),
        'prompt_sha256': hashlib.sha256(prompt_text.encode()).hexdigest(),
    }


def snapshots(sessions: dict[int, Session]) -> dict[int, dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=len(sessions)) as executor:
        futures = {pid: executor.submit(snapshot, session) for pid, session in sessions.items()}
        return {pid: future.result() for pid, future in futures.items()}


def wait_for_revision(
    sessions: dict[int, Session],
    revision_id: str,
) -> tuple[dict[int, dict[str, Any]], float]:
    started = time.monotonic()
    deadline = started + WAIT_SECONDS
    latest: dict[int, dict[str, Any]] = {}
    while time.monotonic() < deadline:
        latest = snapshots(sessions)
        if all(
            item['private_revision'] == revision_id
            and item['public_revision'] == revision_id
            and not item['public_has_prompt']
            for item in latest.values()
        ):
            return latest, round(time.monotonic() - started, 3)
        time.sleep(0.1)
    raise AssertionError(f'workers did not converge to {revision_id}: {latest}')


def main() -> int:
    pids = worker_pids()
    if len(pids) != 4:
        raise AssertionError(f'expected four worker PIDs, got {pids}')
    retained, pinned = pin_sessions(create_token(), pids)
    result: dict[str, Any] = {
        'ok': False,
        'worker_pids': pids,
        'worker_session_ports': {str(pid): session.local_port for pid, session in pinned.items()},
    }
    try:
        before = snapshots(pinned)
        before_values = list(before.values())
        if len({item['private_revision'] for item in before_values}) != 1:
            raise AssertionError(f'workers disagree before mutation: {before}')
        current_revision = before_values[0]['private_revision']
        current = expect(
            pinned[pids[0]],
            'GET',
            '/api/v1/configs/conversation_mode_profiles/agent',
        )
        profile = {
            'schema_version': 1,
            'system_prompt': current['system_prompt'],
            'defaults': {
                'terminal_id': None,
                'tool_ids': [],
                'skill_ids': [],
                'filter_ids': [],
                'feature_ids': ['web_search'],
            },
        }
        saved = expect(
            pinned[pids[0]],
            'POST',
            '/api/v1/configs/conversation_mode_profiles/agent/revisions',
            {'expected_current_revision_id': current_revision, 'profile': profile},
        )
        saved_revision = saved['revision_id']
        after_save, save_convergence = wait_for_revision(pinned, saved_revision)
        if any(item['public_defaults'] != profile['defaults'] for item in after_save.values()):
            raise AssertionError(f'workers disagree on saved defaults: {after_save}')

        restored = expect(
            pinned[pids[-1]],
            'POST',
            f'/api/v1/configs/conversation_mode_profiles/agent/revisions/{current_revision}/restore',
            {'expected_current_revision_id': saved_revision},
        )
        restored_revision = restored['revision_id']
        after_restore, restore_convergence = wait_for_revision(pinned, restored_revision)
        if any(item['public_defaults'] != before_values[0]['public_defaults'] for item in after_restore.values()):
            raise AssertionError(f'workers disagree on restored defaults: {after_restore}')
        if len({item['prompt_sha256'] for item in after_restore.values()}) != 1:
            raise AssertionError('workers disagree on private prompt hash after restore')

        result.update(
            {
                'ok': True,
                'before': before,
                'saved_revision': saved_revision,
                'save_convergence_seconds': save_convergence,
                'after_save': after_save,
                'restored_revision': restored_revision,
                'restore_convergence_seconds': restore_convergence,
                'after_restore': after_restore,
            }
        )
    finally:
        for session in retained:
            session.close()

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
