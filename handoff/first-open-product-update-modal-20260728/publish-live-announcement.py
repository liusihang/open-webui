#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

BASE_URL = 'http://127.0.0.1'
PRIVATE_DIR = Path('/home/aiserver/staging/pr7-announcement-hotpatch-6ba5c1398/live/private')
TOKEN_PATH = PRIVATE_DIR / 'admin.token'
CONTENT_PATH = Path('/tmp/announcement-content.md')
BEFORE_PATH = PRIVATE_DIR / 'admin-config.before-publish.json'
AFTER_PATH = PRIVATE_DIR / 'admin-config.after-publish.json'
ANNOUNCEMENT_FIELDS = (
    'ANNOUNCEMENT_MODAL_ENABLED',
    'ANNOUNCEMENT_MODAL_KEY',
    'ANNOUNCEMENT_MODAL_TITLE',
    'ANNOUNCEMENT_MODAL_CONTENT',
)


def request(method: str, body: dict | None = None) -> dict:
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    http_request = urllib.request.Request(
        f'{BASE_URL}/api/v1/auths/admin/config',
        data=payload,
        headers={
            'Authorization': f'Bearer {TOKEN_PATH.read_text().strip()}',
            'Content-Type': 'application/json',
        },
        method=method,
    )
    with urllib.request.urlopen(http_request, timeout=20) as response:
        return json.load(response)


def announcement_from_admin_config(config: dict) -> dict:
    return {
        'enabled': config['ANNOUNCEMENT_MODAL_ENABLED'],
        'key': config['ANNOUNCEMENT_MODAL_KEY'],
        'title': config['ANNOUNCEMENT_MODAL_TITLE'],
        'content': config['ANNOUNCEMENT_MODAL_CONTENT'],
    }


def announcement_sha(announcement: dict) -> str:
    encoded = json.dumps(announcement, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(encoded).hexdigest()


def payload_from_markdown() -> dict:
    source = CONTENT_PATH.read_text()
    key_match = re.search(r'^- Key: `([^`]+)`$', source, re.MULTILINE)
    title_match = re.search(r'^- Title: `([^`]+)`$', source, re.MULTILINE)
    marker = '## Markdown content\n'
    if key_match is None or title_match is None or marker not in source:
        raise RuntimeError('announcement content file is malformed')
    content = source.split(marker, 1)[1].strip()
    if not content:
        raise RuntimeError('announcement Markdown content is empty')
    return {
        'ANNOUNCEMENT_MODAL_ENABLED': True,
        'ANNOUNCEMENT_MODAL_KEY': key_match.group(1),
        'ANNOUNCEMENT_MODAL_TITLE': title_match.group(1),
        'ANNOUNCEMENT_MODAL_CONTENT': content,
    }


def write_private(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')))
    os.chmod(path, 0o600)


def publish() -> None:
    if BEFORE_PATH.exists() or AFTER_PATH.exists():
        raise RuntimeError('live announcement snapshot already exists')
    before = request('GET')
    payload = payload_from_markdown()
    write_private(BEFORE_PATH, before)
    expected = {**before, **payload}
    request('POST', expected)
    after = request('GET')
    if any(after.get(field) != expected[field] for field in ANNOUNCEMENT_FIELDS):
        raise RuntimeError('published announcement readback mismatch')
    write_private(AFTER_PATH, after)
    print(
        json.dumps(
            {
                'announcement_published': True,
                'previous_announcement_sha256': announcement_sha(
                    announcement_from_admin_config(before)
                ),
                'announcement_sha256': announcement_sha(
                    announcement_from_admin_config(after)
                ),
                'announcement_key': after['ANNOUNCEMENT_MODAL_KEY'],
            },
            separators=(',', ':'),
        )
    )


def restore() -> None:
    if not BEFORE_PATH.exists():
        raise RuntimeError('live announcement rollback snapshot is missing')
    before = json.loads(BEFORE_PATH.read_text())
    request('POST', before)
    restored = request('GET')
    if any(restored.get(field) != before[field] for field in ANNOUNCEMENT_FIELDS):
        raise RuntimeError('announcement rollback readback mismatch')
    print(
        json.dumps(
            {
                'announcement_restored': True,
                'announcement_sha256': announcement_sha(
                    announcement_from_admin_config(restored)
                ),
            },
            separators=(',', ':'),
        )
    )


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else ''
    if action == 'publish':
        publish()
        return
    if action == 'restore':
        restore()
        return
    raise RuntimeError('usage: publish-live-announcement.py publish|restore')


if __name__ == '__main__':
    main()
