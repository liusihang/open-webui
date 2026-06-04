import pytest

from open_webui.socket import main as socket_main


@pytest.mark.asyncio
async def test_files_event_appends_existing_message_files(monkeypatch):
    emitted = []
    upserts = []

    async def fake_emit(*args, **kwargs):
        emitted.append((args, kwargs))

    async def fake_get_message(chat_id, message_id):
        assert (chat_id, message_id) == ('chat-1', 'message-1')
        return {'files': [{'type': 'image', 'url': '/old.png'}]}

    async def fake_upsert(chat_id, message_id, payload):
        upserts.append((chat_id, message_id, payload))

    monkeypatch.setattr(socket_main.sio, 'emit', fake_emit)
    monkeypatch.setattr(socket_main.Chats, 'get_message_by_id_and_message_id', fake_get_message)
    monkeypatch.setattr(socket_main.Chats, 'upsert_message_to_chat_by_id_and_message_id', fake_upsert)

    emitter = await socket_main.get_event_emitter(
        {'user_id': 'user-1', 'chat_id': 'chat-1', 'message_id': 'message-1'}
    )

    await emitter({'type': 'files', 'data': {'files': [{'type': 'image', 'url': '/new.png'}]}})

    assert emitted
    assert upserts == [
        (
            'chat-1',
            'message-1',
            {'files': [{'type': 'image', 'url': '/new.png'}, {'type': 'image', 'url': '/old.png'}]},
        )
    ]


@pytest.mark.asyncio
async def test_chat_message_files_event_replaces_message_files(monkeypatch):
    emitted = []
    upserts = []

    async def fake_emit(*args, **kwargs):
        emitted.append((args, kwargs))

    async def fake_get_message(chat_id, message_id):
        assert (chat_id, message_id) == ('chat-1', 'message-1')
        return {'files': [{'type': 'image', 'url': '/old.png'}]}

    async def fake_upsert(chat_id, message_id, payload):
        upserts.append((chat_id, message_id, payload))

    monkeypatch.setattr(socket_main.sio, 'emit', fake_emit)
    monkeypatch.setattr(socket_main.Chats, 'get_message_by_id_and_message_id', fake_get_message)
    monkeypatch.setattr(socket_main.Chats, 'upsert_message_to_chat_by_id_and_message_id', fake_upsert)

    emitter = await socket_main.get_event_emitter(
        {'user_id': 'user-1', 'chat_id': 'chat-1', 'message_id': 'message-1'}
    )

    await emitter({'type': 'chat:message:files', 'data': {'files': [{'type': 'image', 'url': '/new.png'}]}})

    assert emitted
    assert upserts == [('chat-1', 'message-1', {'files': [{'type': 'image', 'url': '/new.png'}]})]
