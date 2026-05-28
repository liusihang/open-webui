import types

import pytest

from open_webui.utils import middleware


@pytest.mark.asyncio
async def test_add_file_context_excludes_image_attachments(monkeypatch):
    chat = types.SimpleNamespace(
        chat={
            "history": {
                "currentId": "user-1",
                "messages": {
                    "user-1": {
                        "id": "user-1",
                        "parentId": None,
                        "childrenIds": [],
                        "role": "user",
                        "content": "这个GCD是什么",
                        "files": [
                            {
                                "type": "file",
                                "url": "image-file-id",
                                "content_type": "image/png",
                                "name": "image.png",
                            },
                            {
                                "type": "file",
                                "url": "pdf-file-id",
                                "content_type": "application/pdf",
                                "name": "spec.pdf",
                            },
                        ],
                    }
                },
            }
        }
    )

    async def get_chat_by_id_and_user_id(chat_id, user_id):
        return chat

    monkeypatch.setattr(
        middleware.Chats, "get_chat_by_id_and_user_id", get_chat_by_id_and_user_id
    )

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "这个GCD是什么"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,abc"},
                },
            ],
        }
    ]

    result = await middleware.add_file_context(
        messages, "chat-1", types.SimpleNamespace(id="user-1")
    )

    file_context = result[0]["content"][0]["text"]
    assert "pdf-file-id" in file_context
    assert "image-file-id" not in file_context
