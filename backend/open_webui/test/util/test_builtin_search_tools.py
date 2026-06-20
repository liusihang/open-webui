"""Unit tests for web_search_research built-in tool."""

import json
import types

import pytest
from open_webui.tools import builtin


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_request(*, models=None, engine='searxng', result_count=10):
    """Build a minimal fake Request for tool invocation."""
    models_dict = models or {'gpt-4': {'id': 'gpt-4', 'name': 'GPT-4'}}
    req = types.SimpleNamespace(
        app=types.SimpleNamespace(
            state=types.SimpleNamespace(
                MODELS=models_dict,
                config=types.SimpleNamespace(
                    WEB_SEARCH_ENGINE=engine,
                    WEB_SEARCH_RESULT_COUNT=result_count,
                ),
            ),
        ),
        state=types.SimpleNamespace(model={'id': 'gpt-4'}),
    )
    return req


def _make_search_result(link, title=None, snippet=None):
    """Fake a SearchResult-like object."""
    sr = types.SimpleNamespace(link=link, title=title, snippet=snippet)
    return sr


# ── tests ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_request_context_returns_error():
    """When __request__ is None we return an error dict."""
    raw = await builtin.web_search_research('test topic', __request__=None)
    result = json.loads(raw)
    assert 'error' in result
    assert result['error'] == 'Request context not available'


@pytest.mark.asyncio
async def test_query_generation_fails_falls_back_to_topic_search(monkeypatch):
    """When generate_queries raises, we search with the original topic."""
    req = _make_request()

    search_calls = []

    async def fake_search_web(request, engine, query, user):
        search_calls.append(query)
        return [_make_search_result('http://example.com/1', 'Title 1', 'Snippet 1')]

    monkeypatch.setattr(builtin, '_search_web', fake_search_web)

    # Keep generate_queries at the module where we lazy-import it — patch the
    # function on builtin's path (the import happens inside web_search_research).
    # Actually, builtin imports generate_queries INSIDE the function via
    #   from open_webui.routers.tasks import generate_queries
    # So we need to patch that module.
    import open_webui.routers.tasks as tasks_mod

    async def fake_generate_queries(*args, **kwargs):
        raise RuntimeError('task model unavailable')

    monkeypatch.setattr(tasks_mod, 'generate_queries', fake_generate_queries)

    raw = await builtin.web_search_research(
        'climate change effects',
        __request__=req,
        __user__={'id': 'u1', 'email': 'u@test.com', 'name': 'u', 'last_active_at': 0, 'updated_at': 0, 'created_at': 0},
    )
    result = json.loads(raw)
    assert len(result) == 1
    assert result[0]['link'] == 'http://example.com/1'
    assert result[0]['query'] == 'climate change effects'
    assert len(search_calls) == 1
    assert search_calls[0] == 'climate change effects'


@pytest.mark.asyncio
async def test_multi_query_parallel_search_and_dedup(monkeypatch):
    """generate_queries produces 3 queries; parallel search runs; results deduplicated."""
    req = _make_request()

    search_calls = []

    async def fake_search_web(request, engine, query, user):
        search_calls.append(query)
        if 'ocean' in query:
            return [
                _make_search_result('http://a.com/1', 'Ocean A', 'S A'),
                _make_search_result('http://b.com/1', 'Shared B', 'S B'),
            ]
        if 'forest' in query:
            return [
                _make_search_result('http://c.com/1', 'Forest C', 'S C'),
            ]
        if 'ice' in query:
            return [
                _make_search_result('http://b.com/1', 'Shared B', 'S B dup'),  # duplicate
                _make_search_result('http://d.com/1', 'Ice D', 'S D'),
            ]
        return []

    monkeypatch.setattr(builtin, '_search_web', fake_search_web)

    import open_webui.routers.tasks as tasks_mod

    async def fake_generate_queries(request, form_data, user):
        return {
            'choices': [
                {
                    'message': {
                        'content': json.dumps({'queries': [
                            'ocean acidification',
                            'forest deforestation',
                            'ice melting',
                        ]}),
                    }
                }
            ]
        }

    monkeypatch.setattr(tasks_mod, 'generate_queries', fake_generate_queries)

    raw = await builtin.web_search_research(
        'environmental problems',
        __request__=req,
        __user__={'id': 'u1', 'email': 'u@test.com', 'name': 'u', 'last_active_at': 0, 'updated_at': 0, 'created_at': 0},
    )
    result = json.loads(raw)

    links = [r['link'] for r in result]
    assert 'http://b.com/1' in links
    # Dedup: only one copy of http://b.com/1
    assert links.count('http://b.com/1') == 1
    assert len(result) == 4  # a.com/1, b.com/1, c.com/1, d.com/1

    # Each result carries its source query
    queries_seen = {r['query'] for r in result}
    assert queries_seen == {'ocean acidification', 'forest deforestation', 'ice melting'}

    assert len(search_calls) == 3


@pytest.mark.asyncio
async def test_empty_queries_falls_back_to_topic(monkeypatch):
    """Empty queries array → fall back to topic."""
    req = _make_request()

    search_calls = []

    async def fake_search_web(request, engine, query, user):
        search_calls.append(query)
        return [_make_search_result('http://x.com/1', 'X', 'SX')]

    monkeypatch.setattr(builtin, '_search_web', fake_search_web)

    import open_webui.routers.tasks as tasks_mod

    async def fake_generate_queries(request, form_data, user):
        return {
            'choices': [{'message': {'content': json.dumps({'queries': []})}}]
        }

    monkeypatch.setattr(tasks_mod, 'generate_queries', fake_generate_queries)

    raw = await builtin.web_search_research(
        'some topic',
        __request__=req,
        __user__={'id': 'u1', 'email': 'u@test.com', 'name': 'u', 'last_active_at': 0, 'updated_at': 0, 'created_at': 0},
    )
    result = json.loads(raw)
    assert len(result) == 1
    assert result[0]['link'] == 'http://x.com/1'
    assert result[0]['query'] == 'some topic'


@pytest.mark.asyncio
async def test_all_search_results_empty(monkeypatch):
    """All queries return nothing → result is empty list."""
    req = _make_request()

    async def fake_search_web(request, engine, query, user):
        return []

    monkeypatch.setattr(builtin, '_search_web', fake_search_web)

    import open_webui.routers.tasks as tasks_mod

    async def fake_generate_queries(request, form_data, user):
        return {
            'choices': [{'message': {'content': json.dumps({'queries': ['q1', 'q2']})}}]
        }

    monkeypatch.setattr(tasks_mod, 'generate_queries', fake_generate_queries)

    raw = await builtin.web_search_research(
        'ghost topic',
        __request__=req,
        __user__={'id': 'u1', 'email': 'u@test.com', 'name': 'u', 'last_active_at': 0, 'updated_at': 0, 'created_at': 0},
    )
    result = json.loads(raw)
    assert result == []


@pytest.mark.asyncio
async def test_count_limit_applied(monkeypatch):
    """count parameter trims overall results."""
    req = _make_request(result_count=10)

    async def fake_search_web(request, engine, query, user):
        return [
            _make_search_result(f'http://{query}.com/{i}', f'T-{i}', f'S-{i}')
            for i in range(5)
        ]

    monkeypatch.setattr(builtin, '_search_web', fake_search_web)

    import open_webui.routers.tasks as tasks_mod

    async def fake_generate_queries(request, form_data, user):
        return {
            'choices': [{'message': {'content': json.dumps({'queries': ['q1', 'q2']})}}]
        }

    monkeypatch.setattr(tasks_mod, 'generate_queries', fake_generate_queries)

    raw = await builtin.web_search_research(
        'cap test',
        count=3,
        __request__=req,
        __user__={'id': 'u1', 'email': 'u@test.com', 'name': 'u', 'last_active_at': 0, 'updated_at': 0, 'created_at': 0},
    )
    result = json.loads(raw)
    assert len(result) == 3
