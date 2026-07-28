#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from open_webui.models.users import Users
from open_webui.utils.auth import decode_token


async def inspect(path: Path) -> dict:
    decoded = decode_token(path.read_text().strip())
    if not decoded or not isinstance(decoded.get('id'), str):
        return {'decoded': False, 'user_exists': False, 'role': None}
    user_id = decoded['id']
    user = await Users.get_user_by_id(user_id)
    return {
        'decoded': True,
        'user_id_hash': hashlib.sha256(user_id.encode()).hexdigest()[:12],
        'user_exists': user is not None,
        'role': user.role if user else None,
    }


async def main() -> None:
    result = {
        'admin_token': await inspect(Path('/tmp/pr7-announcement-admin.token')),
        'user_token': await inspect(Path('/tmp/pr7-announcement-user.token')),
    }
    print(json.dumps(result, separators=(',', ':')))


if __name__ == '__main__':
    asyncio.run(main())
