#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def pid1_secret() -> str:
    for item in Path('/proc/1/environ').read_bytes().split(b'\0'):
        if item.startswith(b'WEBUI_SECRET_KEY='):
            return item.split(b'=', 1)[1].decode()
    raise RuntimeError('PID 1 has no WEBUI_SECRET_KEY')


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def main() -> None:
    exec_secret = os.environ.get('WEBUI_SECRET_KEY', '')
    master_secret = pid1_secret()
    print(
        json.dumps(
            {
                'exec_secret_length': len(exec_secret),
                'exec_secret_sha256_prefix': digest(exec_secret),
                'master_secret_length': len(master_secret),
                'master_secret_sha256_prefix': digest(master_secret),
                'same_secret': exec_secret == master_secret,
            },
            separators=(',', ':'),
        )
    )


if __name__ == '__main__':
    main()
