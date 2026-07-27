#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import sys
import time

from open_webui.utils.auth import create_token

sys.path.insert(0, '/tmp')
import pr7_dual_mode_four_worker_probe as worker_probe  # noqa: E402

ADMIN_USER_ID = 'b6826286-1251-4576-b3a0-e109ff085a61'
TARGET_IDS = {
    'bifrostapi.Cliproxy/gpt-5.5',
    'bifrostapi.lucen/gpt-5.5',
}


def matching_ids(payload) -> list[str]:
    data = payload.get('data') if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    return sorted(item['id'] for item in data if isinstance(item, dict) and item.get('id') in TARGET_IDS)


def main() -> int:
    token = create_token({'id': ADMIN_USER_ID}, expires_delta=dt.timedelta(minutes=15))
    pids = worker_probe.worker_pids()
    retained, pinned = worker_probe.pin_sessions(token, pids)
    rounds = []
    try:
        for index in range(3):
            current = {}
            for pid, session in pinned.items():
                payload = worker_probe.expect(session, 'GET', '/api/models?refresh=true')
                current[str(pid)] = matching_ids(payload)
            rounds.append({'round': index + 1, 'workers': current})
            time.sleep(0.5)
    finally:
        for session in retained:
            session.close()
    converged = all(set(ids) == TARGET_IDS for round_item in rounds for ids in round_item['workers'].values())
    print(
        json.dumps(
            {
                'worker_pids': pids,
                'rounds': rounds,
                'converged': converged,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if converged else 1


if __name__ == '__main__':
    raise SystemExit(main())
