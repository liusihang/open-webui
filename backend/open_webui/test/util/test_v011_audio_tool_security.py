import asyncio
from types import SimpleNamespace

import pytest

from open_webui.routers import audio as audio_router
from open_webui.routers import tools as tools_router


@pytest.mark.asyncio
async def test_transcribe_preserves_chunk_order_when_later_chunk_finishes_first(monkeypatch):
    async def fake_transcription_handler(request, chunk_path, metadata, user):
        if chunk_path == 'chunk-1.mp3':
            await asyncio.sleep(0.01)
            return {'text': 'first'}
        return {'text': 'second'}

    monkeypatch.setattr(audio_router, 'BYPASS_PYDUB_PREPROCESSING', False)
    monkeypatch.setattr(audio_router, 'is_audio_conversion_required', lambda _path: False)
    monkeypatch.setattr(audio_router, 'compress_audio', lambda path: path)
    monkeypatch.setattr(audio_router, 'split_audio', lambda *_args: ['chunk-1.mp3', 'chunk-2.mp3'])
    monkeypatch.setattr(audio_router, 'transcription_handler', fake_transcription_handler)
    monkeypatch.setattr(audio_router.os.path, 'isfile', lambda _path: False)

    result = await audio_router.transcribe(SimpleNamespace(), 'recording.mp3')

    assert result == {'text': 'first second'}


@pytest.mark.asyncio
async def test_read_only_tool_response_does_not_expose_source(monkeypatch):
    tool = SimpleNamespace(
        id='tool-1',
        user_id='owner-1',
        model_dump=lambda: {
            'id': 'tool-1',
            'user_id': 'owner-1',
            'name': 'Sensitive tool',
            'content': 'API_KEY = "secret"',
            'specs': [{'name': 'run'}],
            'meta': {'description': 'test'},
            'access_grants': [],
            'updated_at': 2,
            'created_at': 1,
        },
    )
    access_checks = []

    async def fake_get_tool(_tool_id, db=None):
        return tool

    async def fake_has_access(**kwargs):
        access_checks.append(kwargs['permission'])
        return kwargs['permission'] == 'read'

    monkeypatch.setattr(tools_router.Tools, 'get_tool_by_id', fake_get_tool)
    monkeypatch.setattr(tools_router.AccessGrants, 'has_access', fake_has_access)

    response = await tools_router.get_tools_by_id(
        'tool-1',
        user=SimpleNamespace(id='reader-1', role='user'),
        db=object(),
    )

    assert response.write_access is False
    assert not hasattr(response, 'content')
    assert response.specs == [{'name': 'run'}]
    assert access_checks == ['read', 'write']
