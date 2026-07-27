#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import sys
from typing import Any

from open_webui.utils.auth import create_token

sys.path.insert(0, '/tmp')
import pr7_dual_mode_four_worker_probe as worker_probe  # noqa: E402

ADMIN_USER_ID = 'b6826286-1251-4576-b3a0-e109ff085a61'


def public_profiles(payload: Any) -> dict[str, dict[str, Any]]:
    profiles = payload.get('conversation_mode_profiles') if isinstance(payload, dict) else None
    if isinstance(profiles, dict):
        return {str(mode): item for mode, item in profiles.items() if isinstance(item, dict)}
    if isinstance(profiles, list):
        return {item['mode']: item for item in profiles if isinstance(item, dict) and isinstance(item.get('mode'), str)}
    return {}


def main() -> int:
    token = create_token({'id': ADMIN_USER_ID}, expires_delta=dt.timedelta(minutes=15))
    pids = worker_probe.worker_pids()
    retained, pinned = worker_probe.pin_sessions(token, pids)
    result: dict[str, Any] = {}
    try:
        for pid, session in pinned.items():
            public = public_profiles(worker_probe.expect(session, 'GET', '/api/config'))
            result[str(pid)] = {}
            for mode in ('chat', 'agent'):
                private = worker_probe.expect(
                    session,
                    'GET',
                    f'/api/v1/configs/conversation_mode_profiles/{mode}',
                )
                public_mode = public[mode]
                result[str(pid)][mode] = {
                    'private_revision': private.get('revision_id'),
                    'public_revision': public_mode.get('current_revision_id') or public_mode.get('revision_id'),
                    'private_defaults': private.get('defaults'),
                    'public_defaults': public_mode.get('defaults'),
                    'public_keys': sorted(public_mode),
                    'public_prompt_exposed': 'system_prompt' in json.dumps(public_mode).lower(),
                }
    finally:
        for session in retained:
            session.close()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
