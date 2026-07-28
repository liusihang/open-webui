import pytest
from open_webui.socket import main as socket_main
from open_webui.utils.redaction import PROMPT_REDACTION_REPLACEMENT


@pytest.mark.asyncio
async def test_files_event_appends_existing_message_files(monkeypatch):
    emitted = []
    upserts = []

    async def fake_emit(*args, **kwargs):
        emitted.append((args, kwargs))

    async def fake_get_message(chat_id, message_id):
        assert (chat_id, message_id) == ('chat-1', 'message-1')
        return {'files': [{'type': 'image', 'url': '/old.png'}]}

    async def fake_upsert(chat_id, message_id, payload, **kwargs):
        upserts.append((chat_id, message_id, payload, kwargs))

    monkeypatch.setattr(socket_main.sio, 'emit', fake_emit)
    monkeypatch.setattr(socket_main, 'WEBSOCKET_MANAGER', 'redis')
    monkeypatch.setattr(socket_main.Chats, 'get_message_by_id_and_message_id', fake_get_message)
    monkeypatch.setattr(socket_main.Chats, 'upsert_message_to_chat_by_id_and_message_id', fake_upsert)

    emitter = await socket_main.get_event_emitter({'user_id': 'user-1', 'chat_id': 'chat-1', 'message_id': 'message-1'})

    await emitter({'type': 'files', 'data': {'files': [{'type': 'image', 'url': '/new.png'}]}})

    assert emitted
    assert upserts == [
        (
            'chat-1',
            'message-1',
            {'files': [{'type': 'image', 'url': '/new.png'}, {'type': 'image', 'url': '/old.png'}]},
            {'touch': False},
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

    async def fake_upsert(chat_id, message_id, payload, **kwargs):
        upserts.append((chat_id, message_id, payload, kwargs))

    monkeypatch.setattr(socket_main.sio, 'emit', fake_emit)
    monkeypatch.setattr(socket_main, 'WEBSOCKET_MANAGER', 'redis')
    monkeypatch.setattr(socket_main.Chats, 'get_message_by_id_and_message_id', fake_get_message)
    monkeypatch.setattr(socket_main.Chats, 'upsert_message_to_chat_by_id_and_message_id', fake_upsert)

    emitter = await socket_main.get_event_emitter({'user_id': 'user-1', 'chat_id': 'chat-1', 'message_id': 'message-1'})

    await emitter({'type': 'chat:message:files', 'data': {'files': [{'type': 'image', 'url': '/new.png'}]}})

    assert emitted
    assert upserts == [
        ('chat-1', 'message-1', {'files': [{'type': 'image', 'url': '/new.png'}]}, {'touch': False})
    ]


@pytest.mark.asyncio
async def test_event_emitter_redacts_error_content_before_socket_and_chat_persistence(
    monkeypatch,
):
    secret = 'socket-product-error-secret'
    emitted = []
    upserts = []

    async def fake_emit(*args, **kwargs):
        emitted.append((args, kwargs))

    async def fake_get_message(chat_id, message_id):
        assert (chat_id, message_id) == ('chat-1', 'message-1')
        return {'content': 'existing '}

    async def fake_upsert(chat_id, message_id, payload):
        upserts.append((chat_id, message_id, payload))

    monkeypatch.setattr(socket_main.sio, 'emit', fake_emit)
    monkeypatch.setattr(socket_main.Chats, 'get_message_by_id_and_message_id', fake_get_message)
    monkeypatch.setattr(socket_main.Chats, 'upsert_message_to_chat_by_id_and_message_id', fake_upsert)

    emitter = await socket_main.get_event_emitter(
        {'user_id': 'user-1', 'chat_id': 'chat-1', 'message_id': 'message-1'},
        redaction_secrets=(secret,),
    )

    await emitter(
        {
            'type': 'message',
            'data': {'content': f'provider error exposed {secret}'},
        }
    )

    persisted_and_emitted = {'socket': emitted, 'chat': upserts}
    assert secret not in repr(persisted_and_emitted)
    assert PROMPT_REDACTION_REPLACEMENT in repr(persisted_and_emitted)


@pytest.mark.asyncio
async def test_event_call_redacts_request_secret_before_socket_call(monkeypatch):
    secret = 'socket-event-call-secret'
    calls = []

    async def fake_call(*args, **kwargs):
        calls.append((args, kwargs))
        return {'ok': True}

    monkeypatch.setitem(socket_main.SESSION_POOL, 'session-1', {'id': 'user-1'})
    monkeypatch.setattr(socket_main.sio, 'call', fake_call)

    event_call = await socket_main.get_event_call(
        {
            'user_id': 'user-1',
            'chat_id': 'chat-1',
            'message_id': 'message-1',
            'session_id': 'session-1',
        },
        redaction_secrets=(secret,),
    )

    result = await event_call({'type': 'request', 'data': {'error': secret}})

    assert result == {'ok': True}
    assert secret not in repr(calls)
    assert PROMPT_REDACTION_REPLACEMENT in repr(calls)
