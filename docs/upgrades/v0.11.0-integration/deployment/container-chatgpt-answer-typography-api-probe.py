#!/usr/bin/env python3
"""Read-only persisted-chat probe for the isolated v0.11 typography hotpatch."""

from __future__ import annotations

import datetime as dt
import json
import sys
import urllib.error
import urllib.request
from typing import Any

from open_webui.utils.auth import create_token


BASE_URL = 'http://127.0.0.1:8080'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(token: str, path: str) -> Any:
	req = urllib.request.Request(
		f'{BASE_URL}{path}',
		headers={
			'Accept': 'application/json',
			'Authorization': f'Bearer {token}',
		},
	)
	try:
		with OPENER.open(req, timeout=45) as response:
			if response.status != 200:
				raise RuntimeError(f'GET {path} returned HTTP {response.status}')
			return json.loads(response.read())
	except urllib.error.HTTPError as exc:
		raise RuntimeError(f'GET {path} returned HTTP {exc.code}') from exc


def rows(payload: Any) -> list[dict[str, Any]]:
	if isinstance(payload, list):
		return [item for item in payload if isinstance(item, dict)]
	if isinstance(payload, dict):
		for key in ('items', 'data'):
			value = payload.get(key)
			if isinstance(value, list):
				return [item for item in value if isinstance(item, dict)]
	return []


def main() -> int:
	if len(sys.argv) != 2:
		raise SystemExit('usage: container-chatgpt-answer-typography-api-probe.py ADMIN_USER_ID')

	token = create_token({'id': sys.argv[1]}, expires_delta=dt.timedelta(minutes=15))
	chat_rows = rows(
		request(token, '/api/v1/chats/?page=1&include_pinned=true&include_folders=true')
	)
	chat_id = next(
		(item.get('id') for item in chat_rows if isinstance(item.get('id'), str)),
		None,
	)
	if not chat_id:
		raise RuntimeError('authenticated chat list has no readable chat id')

	detail = request(token, f'/api/v1/chats/{chat_id}')
	if detail.get('id') != chat_id:
		raise RuntimeError('persisted chat detail id does not match the requested chat')
	chat = detail.get('chat')
	if not isinstance(chat, dict):
		raise RuntimeError('persisted chat detail is missing its chat document')
	history = chat.get('history')
	if not isinstance(history, dict):
		raise RuntimeError('persisted chat detail is missing its history document')
	messages = history.get('messages')
	if not isinstance(messages, dict):
		raise RuntimeError('persisted chat history messages are not a mapping')

	result = {
		'ok': True,
		'chat_id': chat_id,
		'chat_list_count': len(chat_rows),
		'message_count': len(messages),
		'current_id': history.get('currentId'),
		'mode': chat.get('mode'),
	}
	json.dump(result, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
	sys.stdout.write('\n')
	return 0


if __name__ == '__main__':
	try:
		raise SystemExit(main())
	except RuntimeError as exc:
		print(str(exc), file=sys.stderr)
		raise SystemExit(1) from exc
