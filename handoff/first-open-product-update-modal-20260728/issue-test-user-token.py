#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from open_webui.utils.auth import create_token

USER_ID_PATH = Path(os.environ.get('PR7_TEST_USER_ID_PATH', '/tmp/pr7-announcement-user.id'))
TOKEN_PATH = Path(os.environ.get('PR7_TEST_TOKEN_PATH', '/tmp/pr7-announcement-user.token'))


def main() -> None:
    user_id = USER_ID_PATH.read_text().strip()
    if not user_id:
        raise RuntimeError('ordinary test user id is empty')
    TOKEN_PATH.write_text(create_token({'id': user_id}, expires_delta=dt.timedelta(hours=2)))
    os.chmod(TOKEN_PATH, 0o600)


if __name__ == '__main__':
    main()
