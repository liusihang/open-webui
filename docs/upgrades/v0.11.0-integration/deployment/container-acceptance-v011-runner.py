#!/usr/bin/env python3
"""Run the existing isolated acceptance with concurrent pinned-worker reads.

The original probe pins one keep-alive socket to each Uvicorn worker, then
refreshes the model catalog on those sockets sequentially. A refresh may exceed
Uvicorn's idle keep-alive window for the other three sockets. This runner keeps
the same assertions but observes all four already-pinned workers concurrently.
"""

from __future__ import annotations

import importlib.util
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ORIGINAL = Path('/tmp/container-acceptance.py')
spec = importlib.util.spec_from_file_location('container_acceptance_v011', ORIGINAL)
if spec is None or spec.loader is None:
    raise RuntimeError(f'cannot load {ORIGINAL}')
acceptance = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = acceptance
spec.loader.exec_module(acceptance)


def _observe_worker(
    pid: int,
    session: Any,
    expected_revisions: dict[str, str],
    model_id: str,
) -> tuple[str, dict[str, Any]]:
    models = acceptance.worker_probe.expect(session, 'GET', '/api/models?refresh=true')
    model_rows = models.get('data') if isinstance(models, dict) else None
    model_ids = {
        item.get('id')
        for item in model_rows or []
        if isinstance(item, dict) and isinstance(item.get('id'), str)
    }
    if model_id not in model_ids:
        raise RuntimeError(f'model {model_id} missing on worker {pid}')

    config = acceptance.worker_probe.expect(session, 'GET', '/api/config')
    public = acceptance.public_profiles(config)
    mode_observations: dict[str, Any] = {}
    for mode in ('chat', 'agent'):
        private = acceptance.worker_probe.expect(
            session,
            'GET',
            f'/api/v1/configs/conversation_mode_profiles/{mode}',
        )
        public_mode = public[mode]
        private_revision = private.get('revision_id')
        public_revision = public_mode.get('current_revision_id') or public_mode.get('revision_id')
        if (
            private_revision != expected_revisions[mode]
            or public_revision != expected_revisions[mode]
        ):
            raise RuntimeError(f'{mode} revision mismatch on worker {pid}')
        if private.get('defaults') != acceptance.DESIRED_PROFILES[mode]['defaults']:
            raise RuntimeError(f'{mode} private defaults mismatch on worker {pid}')
        expected_public_defaults = {
            key: value
            for key, value in acceptance.DESIRED_PROFILES[mode]['defaults'].items()
            if value != 'inherit'
        }
        if public_mode.get('defaults') != expected_public_defaults:
            raise RuntimeError(f'{mode} public defaults mismatch on worker {pid}')
        if 'system_prompt' in acceptance.json.dumps(public_mode).lower():
            raise RuntimeError(f'{mode} public prompt exposure on worker {pid}')
        mode_observations[mode] = {
            'revision_id': private_revision,
            'defaults_match': True,
            'public_prompt_exposed': False,
        }
    return (
        str(pid),
        {
            'session_port': session.local_port,
            'model_present': True,
            'modes': mode_observations,
        },
    )


def prove_workers(
    token: str,
    expected_revisions: dict[str, str],
    model_id: str,
) -> dict[str, Any]:
    pids = acceptance.worker_probe.worker_pids()
    if len(pids) != 4:
        raise RuntimeError(f'expected four container worker PIDs, got {pids}')
    retained, pinned = acceptance.worker_probe.pin_sessions(token, pids)
    observations: dict[str, Any] = {}
    try:
        with ThreadPoolExecutor(max_workers=len(pinned)) as executor:
            futures = [
                executor.submit(
                    _observe_worker,
                    pid,
                    session,
                    expected_revisions,
                    model_id,
                )
                for pid, session in pinned.items()
            ]
            for future in futures:
                pid, observation = future.result()
                observations[pid] = observation
    finally:
        for session in retained:
            session.close()
    return {
        'container_worker_pids': pids,
        'observations': dict(sorted(observations.items())),
    }


acceptance.prove_workers = prove_workers

if __name__ == '__main__':
    try:
        raise SystemExit(acceptance.main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
