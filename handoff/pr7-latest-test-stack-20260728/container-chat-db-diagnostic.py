#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
from typing import Any

from open_webui.models.chats import Chats

ADMIN_USER_ID = 'b6826286-1251-4576-b3a0-e109ff085a61'
USER_MESSAGE_ID = '1fa44200-38e6-42cb-995a-dc4e33fa85b6'


def text_length(message: dict[str, Any]) -> int:
    content = message.get('content')
    if isinstance(content, str):
        return len(content)
    output = message.get('output')
    return len(json.dumps(output, ensure_ascii=False)) if output is not None else 0


async def inspect() -> dict[str, Any]:
    response = await Chats.get_chats_by_user_id(ADMIN_USER_ID, limit=50)
    for chat in response.items:
        messages = (chat.chat.get('history') or {}).get('messages') or {}
        user_message = messages.get(USER_MESSAGE_ID)
        if not isinstance(user_message, dict):
            continue
        child_ids = user_message.get('childrenIds') or []
        assistants = []
        for child_id in child_ids:
            current = await Chats.get_message_by_id_and_message_id(chat.id, child_id)
            fallback = messages.get(child_id)
            if isinstance(current, dict):
                assistants.append(current)
            elif isinstance(fallback, dict):
                assistants.append(fallback)
        return {
            'found': True,
            'chat_id': chat.id,
            'mode': chat.chat.get('mode'),
            'mode_profile_revision_id': chat.mode_profile_revision_id,
            'assistant_count': len(assistants),
            'assistant_done': [item.get('done') for item in assistants],
            'assistant_has_error': [bool(item.get('error')) for item in assistants],
            'assistant_text_lengths': [text_length(item) for item in assistants],
        }
    return {'found': False}


def main() -> int:
    result = asyncio.run(inspect())
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get('found') else 1


if __name__ == '__main__':
    raise SystemExit(main())
