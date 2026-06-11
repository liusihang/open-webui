import json
import types

import pytest

from open_webui.retrieval.evidence import (
    EvidenceToolError,
    collect_allowlisted_query_image_refs,
    normalize_query_knowledge_evidence_args,
    resolve_query_image_refs,
)
from open_webui.retrieval import evidence as evidence_mod
from open_webui.tools.builtin import query_knowledge_evidence
from open_webui.utils.tools import get_builtin_tools


class _FakeRequest:
    def __init__(self) -> None:
        self.app = types.SimpleNamespace(state=types.SimpleNamespace(config=types.SimpleNamespace()))


def test_normalize_query_knowledge_evidence_args_coerces_legacy_aliases_and_scalars() -> None:
    normalized = normalize_query_knowledge_evidence_args(
        evidence_refs='["ke:1", "ke:2"]',
        query_text='what is this?',
        visual_query='red sample destination box',
        query_image_refs='["chat:file:abc"]',
        knowledge_ids='["kb-legacy"]',
        collection_ids=None,
        modalities='["image"]',
        count='12',
        top_k=None,
        rerank='false',
        include_images='true',
    )

    assert normalized.evidence_refs == ['ke:1', 'ke:2']
    assert normalized.query_text == 'what is this?'
    assert normalized.visual_query == 'red sample destination box'
    assert normalized.query_image_refs == ['chat:file:abc']
    assert normalized.knowledge_ids == ['kb-legacy']
    assert normalized.collection_ids == ['kb-legacy']
    assert normalized.modalities == ['image']
    assert normalized.count == 12
    assert normalized.top_k == 12
    assert normalized.rerank is False
    assert normalized.include_images is True


def test_normalize_query_knowledge_evidence_args_uses_question_alias_for_query_text() -> None:
    normalized = normalize_query_knowledge_evidence_args(
        question='where should the red sample go?',
        visual_query='red-labeled destination box',
        knowledge_ids=['kb-1'],
    )

    assert normalized.query_text == 'where should the red sample go?'
    assert normalized.visual_query == 'red-labeled destination box'
    assert normalized.to_payload()['visual_query'] == 'red-labeled destination box'


def test_collect_allowlisted_query_image_refs_uses_metadata_file_context() -> None:
    refs = collect_allowlisted_query_image_refs(
        {
            'files': [
                {'id': 'chat:file:abc', 'type': 'image', 'url': '/api/v1/files/chat-file-abc/content'},
                {'id': 'https://example.com/image.png', 'type': 'image'},
                {'file_id': '/tmp/image.png', 'type': 'image'},
                {'image_url': {'url': 'data:image/png;base64,AAAA'}, 'type': 'image'},
                {'file_id': 'chat:file:def', 'type': 'image', 'image_url': {'url': 'chat:file:def'}},
                {'id': 'note-1', 'type': 'note'},
            ]
        }
    )

    assert refs == [
        'chat:file:abc',
        'chat:file:def',
    ]


@pytest.mark.parametrize(
    'query_image_ref',
    [
        'data:image/png;base64,AAAA',
        'https://example.com/image.png',
        '/tmp/image.png',
        'file://localhost/tmp/image.png',
        './relative/image.png',
    ],
)
def test_resolve_query_image_refs_rejects_non_allowlisted_refs(query_image_ref: str) -> None:
    with pytest.raises(EvidenceToolError) as exc_info:
        resolve_query_image_refs([query_image_ref], allowed_refs={'chat:file:allowed'})

    assert exc_info.value.code == 'forbidden_image_ref'


@pytest.mark.asyncio
async def test_query_knowledge_evidence_returns_compact_evidence_not_found_payload_for_exact_refs(monkeypatch) -> None:
    async def fake_get_evidence_by_ref(ref, db=None):
        return None

    monkeypatch.setattr(evidence_mod.KnowledgeEvidences, "get_evidence_by_ref", fake_get_evidence_by_ref)

    result = await query_knowledge_evidence(
        evidence_refs=['ke:missing'],
        knowledge_ids=['kb-1'],
        __request__=_FakeRequest(),
        __user__={'id': 'user-1', 'role': 'user'},
        __metadata__={
            'files': [],
            'effective_knowledge_scope': [{'id': 'kb-1', 'type': 'knowledge'}],
        },
    )

    payload = json.loads(result)

    assert payload['ok'] is False
    assert payload['error']['code'] == 'evidence_not_found'
    assert payload['results'] == []
    assert payload['model_only_files'] == []
    assert payload['query']['evidence_refs'] == ['ke:missing']


@pytest.mark.asyncio
async def test_query_knowledge_evidence_returns_vector_space_unavailable_for_text_only_query() -> None:
    result = await query_knowledge_evidence(
        query_text='what is here?',
        knowledge_ids=['kb-1'],
        count='3',
        __request__=_FakeRequest(),
        __user__={'id': 'user-1', 'role': 'user'},
        __metadata__={'files': []},
    )

    payload = json.loads(result)

    assert payload['ok'] is False
    assert payload['error']['code'] == 'vector_space_unavailable'
    assert payload['query']['collection_ids'] == []
    assert payload['query']['knowledge_ids'] == []
    assert payload['query']['top_k'] == 3
    assert payload['query']['count'] == 3


@pytest.mark.asyncio
async def test_query_knowledge_evidence_returns_unsupported_image_query_for_allowlisted_image_refs() -> None:
    result = await query_knowledge_evidence(
        query_image_refs=['chat:file:abc'],
        __request__=_FakeRequest(),
        __user__={'id': 'user-1', 'role': 'user'},
        __metadata__={
            'files': [
                {'id': 'chat:file:abc', 'type': 'image', 'url': 'chat:file:abc'},
                {'id': 'https://example.com/image.png', 'type': 'image'},
                {'file_id': '/tmp/image.png', 'type': 'image'},
                {'image_url': {'url': 'data:image/png;base64,AAAA'}, 'type': 'image'},
            ]
        },
    )

    payload = json.loads(result)

    assert payload['ok'] is False
    assert payload['error']['code'] == 'vector_space_unavailable'
    assert payload['query']['query_image_refs'] == ['chat:file:abc']


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'query_image_ref',
    [
        'https://example.com/image.png',
        '/tmp/image.png',
        'data:image/png;base64,AAAA',
    ],
)
async def test_query_knowledge_evidence_rejects_raw_url_path_and_data_refs_even_when_present_in_metadata(
    query_image_ref: str,
) -> None:
    result = await query_knowledge_evidence(
        query_image_refs=[query_image_ref],
        __request__=_FakeRequest(),
        __user__={'id': 'user-1', 'role': 'user'},
        __metadata__={
            'files': [
                {'id': 'chat:file:abc', 'type': 'image'},
                {'id': 'https://example.com/image.png', 'type': 'image'},
                {'file_id': '/tmp/image.png', 'type': 'image'},
                {'image_url': {'url': 'data:image/png;base64,AAAA'}, 'type': 'image'},
            ]
        },
    )

    payload = json.loads(result)

    assert payload['ok'] is False
    assert payload['error']['code'] == 'forbidden_image_ref'
    assert payload['query']['query_image_refs'] == [query_image_ref]


@pytest.mark.asyncio
async def test_get_builtin_tools_keeps_legacy_query_knowledge_files_and_adds_evidence_tool_for_evidence_enabled_scope() -> None:
    request = _FakeRequest()
    model = {
        'info': {
            'meta': {
                'capabilities': {'builtin_tools': True},
                'builtinTools': {'knowledge': True},
                'knowledge': [
                    {'id': 'kb-legacy', 'type': 'collection'},
                    {'id': 'kb-evidence', 'type': 'collection', 'evidence_enabled': True},
                ],
            }
        }
    }

    legacy_tools = await get_builtin_tools(
        request,
        extra_params={'__user__': {'id': 'user-1', 'role': 'user'}},
        model={
            'info': {
                'meta': {
                    'capabilities': {'builtin_tools': True},
                    'builtinTools': {'knowledge': True},
                    'knowledge': [{'id': 'kb-legacy', 'type': 'collection'}],
                }
            }
        },
    )
    tools_dict = await get_builtin_tools(
        request,
        extra_params={'__user__': {'id': 'user-1', 'role': 'user'}},
        model=model,
    )

    assert 'query_knowledge_files' in legacy_tools
    assert 'query_knowledge_evidence' not in legacy_tools
    assert 'query_knowledge_files' in tools_dict
    assert 'query_knowledge_evidence' in tools_dict
    evidence_spec = tools_dict['query_knowledge_evidence']['spec']
    assert 'visual_query' in evidence_spec['parameters']['properties']
    assert 'specific visual target' in evidence_spec['description']
