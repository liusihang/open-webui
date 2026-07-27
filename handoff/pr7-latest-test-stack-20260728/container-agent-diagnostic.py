#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import sys
import uuid

from open_webui.utils.auth import create_token

sys.path.insert(0, '/tmp')
import container_acceptance as acceptance  # type: ignore[import-not-found]  # noqa: E402

ADMIN_USER_ID = 'b6826286-1251-4576-b3a0-e109ff085a61'


def main() -> int:
    token = create_token({'id': ADMIN_USER_ID}, expires_delta=dt.timedelta(minutes=15))
    model = acceptance.choose_model(acceptance.request_json(token, '/api/models'))
    revisions = {
        mode: acceptance.request_json(
            token,
            f'/api/v1/configs/conversation_mode_profiles/{mode}',
        )['revision_id']
        for mode in ('chat', 'agent')
    }
    worker_evidence = acceptance.prove_workers(token, revisions, model['id'])
    result = acceptance.agent_smoke(
        token,
        model,
        revisions['agent'],
        uuid.uuid4().hex[:10],
    )
    print(
        json.dumps(
            {
                'model_id': model['id'],
                'worker_count': len(worker_evidence['container_worker_pids']),
                'agent': result,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
