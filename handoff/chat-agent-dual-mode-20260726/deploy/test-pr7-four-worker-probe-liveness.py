#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


MODULE_PATH = (
    Path(sys.argv[1])
    if len(sys.argv) > 1
    else Path(__file__).with_name('pr7_dual_mode_four_worker_probe.py')
)
SPEC = importlib.util.spec_from_file_location('worker_probe_under_test', MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError('could not load worker probe')
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class FakeSession:
    instances: list['FakeSession'] = []

    def __init__(self, token: str):
        del token
        self.local_port = 1000 + len(self.instances) + 1
        self.request_count = 0
        self.closed = False
        self.instances.append(self)

    def request(self, method: str, path: str, body=None):
        del method, path, body
        self.request_count += 1
        if self.local_port == 1001 and self.request_count == 2:
            self.closed = True
            raise ConnectionError('simulated expired keep-alive')
        if self.closed:
            raise ConnectionError('session is closed')
        return 200, {'status': True}

    def close(self):
        self.closed = True


def fake_worker_ports(pids: list[int]) -> dict[int, set[int]]:
    mapping = {pid: set() for pid in pids}
    for session in FakeSession.instances:
        if not session.closed:
            pid = pids[(session.local_port - 1001) % len(pids)]
            mapping[pid].add(session.local_port)
    return mapping


probe.Session = FakeSession
probe.worker_ports = fake_worker_ports

pin_result = probe.pin_sessions('token', [11, 12, 13, 14])
if isinstance(pin_result, tuple):
    retained, pinned = pin_result
else:
    pinned = pin_result
    retained = list(pinned.values())
try:
    for session in pinned.values():
        status, _ = session.request('GET', '/health')
        if status != 200:
            raise AssertionError(f'bad final health status: {status}')
finally:
    for session in retained:
        session.close()

print('four_worker_probe_final_liveness=passed')
