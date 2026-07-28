import pytest

from open_webui.retrieval.web import openserp, searxng


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        return None

    async def json(self):
        return self.payload


class _FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _FakeResponse(self.payload)


@pytest.mark.asyncio
async def test_openserp_uses_mega_search_and_normalizes_results(monkeypatch):
    session = _FakeSession(
        {
            'results': [
                {'url': 'https://example.com/a', 'title': 'A', 'snippet': 'alpha'},
                {'url': 'https://example.com/b', 'title': 'B', 'snippet': 'beta'},
            ]
        }
    )

    async def fake_get_session():
        return session

    monkeypatch.setattr(openserp, 'get_session', fake_get_session)

    results = await openserp.search_openserp('http://openserp.local/', 'query', 1)

    assert session.calls == [
        (
            'http://openserp.local/mega/search',
            {'params': {'text': 'query', 'limit': 1}},
        )
    ]
    assert [result.model_dump() for result in results] == [
        {'link': 'https://example.com/a', 'title': 'A', 'snippet': 'alpha'}
    ]


@pytest.mark.asyncio
async def test_searxng_normalizes_legacy_url_and_sorts_by_score(monkeypatch):
    session = _FakeSession(
        {
            'results': [
                {'url': 'https://example.com/low', 'title': 'Low', 'content': 'low', 'score': 0.1},
                {'url': 'https://example.com/high', 'title': 'High', 'content': 'high', 'score': 0.9},
            ]
        }
    )

    async def fake_get_session():
        return session

    monkeypatch.setattr(searxng, 'get_session', fake_get_session)
    monkeypatch.setattr(searxng, '_get_ssl_context', lambda: False)

    results = await searxng.search_searxng(
        'https://searx.local/search?q=<query>',
        'query',
        1,
        language='en-US,',
        categories=['general', ',science'],
    )

    url, kwargs = session.calls[0]
    assert url == 'https://searx.local/search'
    assert kwargs['params']['language'] == 'en-US'
    assert kwargs['params']['categories'] == 'general,science'
    assert kwargs['ssl'] is False
    assert [result.model_dump() for result in results] == [
        {'link': 'https://example.com/high', 'title': 'High', 'snippet': 'high'}
    ]
