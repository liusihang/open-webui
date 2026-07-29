#!/usr/bin/env python3
"""Create or delete a temporary browser-only typography fixture on the test stack."""

from __future__ import annotations

import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

from open_webui.utils.auth import create_token


BASE_URL = 'http://127.0.0.1:8080'
ADMIN_USER_ID = 'b6826286-1251-4576-b3a0-e109ff085a61'
EMAIL = 'codex.typography.d3d05066b497@example.com'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(token: str, method: str, path: str, body: Any = None) -> Any:
	data = None if body is None else json.dumps(body).encode('utf-8')
	req = urllib.request.Request(
		f'{BASE_URL}{path}',
		data=data,
		method=method,
		headers={
			'Accept': 'application/json',
			'Content-Type': 'application/json',
			'Authorization': f'Bearer {token}',
		},
	)
	try:
		with OPENER.open(req, timeout=45) as response:
			raw = response.read()
			if response.status < 200 or response.status >= 300:
				raise RuntimeError(f'{method} {path} returned HTTP {response.status}')
			return json.loads(raw) if raw else None
	except urllib.error.HTTPError as exc:
		raw = exc.read().decode('utf-8', errors='replace')
		raise RuntimeError(f'{method} {path} returned HTTP {exc.code}: {raw[:500]}') from exc


def first_available_model_id(token: str) -> str:
	payload = request(token, 'GET', '/api/models')
	models = payload.get('data') if isinstance(payload, dict) else payload
	if not isinstance(models, list):
		raise RuntimeError('model catalog did not return a list')
	model_id = next(
		(item.get('id') for item in models if isinstance(item, dict) and item.get('id')),
		None,
	)
	if not isinstance(model_id, str):
		raise RuntimeError('model catalog has no usable model id')
	return model_id


def create_fixture(password: str) -> dict[str, Any]:
	admin_token = create_token({'id': ADMIN_USER_ID}, expires_delta=dt.timedelta(minutes=15))
	user = request(
		admin_token,
		'POST',
		'/api/v1/auths/add',
		{
			'name': 'Codex Typography E2E',
			'email': EMAIL,
			'password': password,
			'role': 'user',
			'profile_image_url': '/user.png',
		},
	)
	user_id = user['id']
	user_token = user['token']
	model_id = first_available_model_id(admin_token)
	now = int(time.time())
	user_message_id = 'typography-user'
	assistant_message_id = 'typography-assistant'
	user_message = {
		'id': user_message_id,
		'parentId': None,
		'childrenIds': [assistant_message_id],
		'role': 'user',
		'content': '请展示排版测试样例',
		'timestamp': now,
		'models': [model_id],
	}
	assistant_message = {
		'id': assistant_message_id,
		'parentId': user_message_id,
		'childrenIds': [],
		'role': 'assistant',
		'content': (
			'# ChatGPT 排版对照\n\n'
			'这是第一段正文，用于检查 **16px 字号**、24px 行高和强调文本。\n\n'
			'这是第二段正文，用于检查相邻段落之间的留白是否自然。\n\n'
			'## 二级标题\n\n'
			'- 第一条列表内容\n'
			'- 第二条列表内容，包含 `inline code`\n\n'
			'### 三级标题\n\n'
			'> 这是一段引用内容，用于检查引用排版。\n\n'
			'```javascript\nconst answer = "typography fixture";\n```'
		),
		'model': model_id,
		'timestamp': now,
		'done': True,
	}
	history = {
		'messages': {
			user_message_id: user_message,
			assistant_message_id: assistant_message,
		},
		'currentId': assistant_message_id,
	}
	chat = request(
		user_token,
		'POST',
		'/api/v1/chats/new',
		{
			'chat': {
				'id': 'typography-browser-fixture',
				'title': 'ChatGPT Typography E2E Fixture',
				'mode': 'chat',
				'models': [model_id],
				'params': {},
				'history': history,
				'messages': [user_message, assistant_message],
				'tags': [],
				'timestamp': now * 1000,
			},
			'folder_id': None,
			'variables': {},
		},
	)
	return {
		'ok': True,
		'user_id': user_id,
		'email': EMAIL,
		'chat_id': chat['id'],
		'chat_url': f'http://192.168.2.238:18085/c/{chat["id"]}',
	}


def delete_fixture(user_id: str) -> dict[str, Any]:
	admin_token = create_token({'id': ADMIN_USER_ID}, expires_delta=dt.timedelta(minutes=15))
	deleted = request(admin_token, 'DELETE', f'/api/v1/users/{user_id}')
	if deleted is not True:
		raise RuntimeError('temporary typography user deletion did not return true')
	return {'ok': True, 'user_id': user_id, 'deleted': True}


def share_fixture(user_id: str, chat_id: str) -> dict[str, Any]:
	user_token = create_token({'id': user_id}, expires_delta=dt.timedelta(minutes=15))
	chat = request(user_token, 'POST', f'/api/v1/chats/{chat_id}/share')
	share_id = chat.get('share_id') if isinstance(chat, dict) else None
	if not isinstance(share_id, str) or not share_id:
		raise RuntimeError('temporary typography chat share did not return a share id')
	return {
		'ok': True,
		'user_id': user_id,
		'chat_id': chat_id,
		'share_id': share_id,
		'share_url': f'http://192.168.2.238:18085/s/{share_id}',
	}


def repair_fixture(user_id: str, chat_id: str) -> dict[str, Any]:
	user_token = create_token({'id': user_id}, expires_delta=dt.timedelta(minutes=15))
	admin_token = create_token({'id': ADMIN_USER_ID}, expires_delta=dt.timedelta(minutes=15))
	model_id = first_available_model_id(admin_token)
	detail = request(user_token, 'GET', f'/api/v1/chats/{chat_id}')
	chat_content = detail.get('chat') if isinstance(detail, dict) else None
	if not isinstance(chat_content, dict):
		raise RuntimeError('temporary typography chat detail is missing its chat document')
	chat_content['models'] = [model_id]
	history = chat_content.get('history')
	if not isinstance(history, dict) or not isinstance(history.get('messages'), dict):
		raise RuntimeError('temporary typography chat history is malformed')
	for message in history['messages'].values():
		if not isinstance(message, dict):
			continue
		if message.get('role') == 'user':
			message['models'] = [model_id]
		elif message.get('role') == 'assistant':
			message['model'] = model_id
	updated = request(user_token, 'POST', f'/api/v1/chats/{chat_id}', {'chat': chat_content})
	shared = request(user_token, 'POST', f'/api/v1/chats/{chat_id}/share')
	share_id = shared.get('share_id') if isinstance(shared, dict) else None
	if not isinstance(share_id, str) or not share_id:
		raise RuntimeError('repaired typography chat share did not return a share id')
	return {
		'ok': True,
		'user_id': user_id,
		'chat_id': updated.get('id') if isinstance(updated, dict) else chat_id,
		'model_id': model_id,
		'share_id': share_id,
		'share_url': f'http://192.168.2.238:18085/s/{share_id}',
	}


def main() -> int:
	if len(sys.argv) < 2:
		raise SystemExit(
			'usage: browser-fixture.py create PASSWORD | share USER_ID CHAT_ID | repair USER_ID CHAT_ID | cleanup USER_ID'
		)
	if sys.argv[1] == 'create' and len(sys.argv) == 3:
		result = create_fixture(sys.argv[2])
	elif sys.argv[1] == 'share' and len(sys.argv) == 4:
		result = share_fixture(sys.argv[2], sys.argv[3])
	elif sys.argv[1] == 'repair' and len(sys.argv) == 4:
		result = repair_fixture(sys.argv[2], sys.argv[3])
	elif sys.argv[1] == 'cleanup' and len(sys.argv) == 3:
		result = delete_fixture(sys.argv[2])
	else:
		raise SystemExit(
			'usage: browser-fixture.py create PASSWORD | share USER_ID CHAT_ID | repair USER_ID CHAT_ID | cleanup USER_ID'
		)
	json.dump(result, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
	sys.stdout.write('\n')
	return 0


if __name__ == '__main__':
	try:
		raise SystemExit(main())
	except RuntimeError as exc:
		print(str(exc), file=sys.stderr)
		raise SystemExit(1) from exc
