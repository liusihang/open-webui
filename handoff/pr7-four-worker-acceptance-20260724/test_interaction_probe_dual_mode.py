#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).with_name('pr7_four_worker_interaction_probe.py')
SPEC = importlib.util.spec_from_file_location('interaction_probe_under_test', MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError('could not load interaction probe')
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class FakeSession:
    def __init__(self):
        self.requests = []

    def request(self, method, path, body=None, **kwargs):
        self.requests.append((method, path, body, kwargs))
        if method == 'GET' and path == '/api/v1/configs/conversation_mode_profiles/agent':
            return 200, {'revision_id': 'agent-revision'}
        if method == 'POST' and path == '/api/chat/completions':
            if body.get('chat_mode') != 'agent':
                raise AssertionError('chat_mode is not agent')
            if body.get('mode_profile_revision_id') != 'agent-revision':
                raise AssertionError('agent profile revision is not bound')
            return 200, {'status': True, 'agent_run_id': 'run-1'}
        raise AssertionError(f'unexpected request: {method} {path}')


session = FakeSession()
run_id = probe.start_agent_run(session, prompt='probe', tool_id='tool-1')
if run_id != 'run-1':
    raise AssertionError(f'unexpected run id: {run_id}')

print('interaction_probe_dual_mode_binding=passed')
