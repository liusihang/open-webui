"""Rejected legacy sources must not be copied without an accepted mapping."""

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / 'tools/migration/v0120'
sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location('legacy_filter_tool', TOOLS / 'legacy_raw_reuse.py')
legacy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(legacy)


def fixture_plan(*, text_ok=True, image_ok=False):
    tables = {key: [] for key in legacy.copy.TABLE_KEYS}
    tables['document_chunk'] = [
        {
            'id': 'text-v',
            'collection_name': 'kb',
            'vector': json.dumps([1.0] + [0.0] * 2047),
            'vmetadata': {
                'embedding_config': {'engine': 'openai', 'model': legacy.MODEL},
                'embedding_config_hash': legacy.TEXT_METADATA_HASH if text_ok else 'unknown',
            },
        },
        {
            'id': 'image-v',
            'collection_name': 'kb',
            'vector': json.dumps([1.0] + [0.0] * 2047),
            'vmetadata': {
                'projection_config_hash': legacy.IMAGE_METADATA_HASH if image_ok else 'unknown',
                'modality': 'image',
                'vector_role': 'image_dense',
                'vector_space_id': 'old',
            },
        },
    ]
    tables['retrieval_chunk'] = [{'chunk_uid': 'chunk', 'metadata': {'vector_id': 'text-v'}}]
    tables['knowledge_evidence'] = [
        {
            'id': 'text-e',
            'evidence_ref': 'text-ref',
            'modality': 'text',
            'retrieval_chunk_uid': 'chunk',
            'asset_id': None,
            'file_id': 'text-file',
            'content_hash': 'text-hash',
        },
        {
            'id': 'image-e',
            'evidence_ref': 'image-ref',
            'modality': 'image',
            'retrieval_chunk_uid': None,
            'asset_id': 'asset',
            'file_id': 'image-file',
            'content_hash': 'image-hash',
        },
    ]
    tables['knowledge_evidence_asset'] = [{'id': 'asset'}]
    tables['retrieval_index_state'] = [{'file_id': 'text-file'}, {'file_id': 'image-file'}]
    bundle = {
        'source_closure': {'active_manifest_current_file_snapshot_closed': 1},
        'chunks': [{}],
        'captured_at': 1,
        'mappings': [{'evidence_ref': 'image-ref', 'vector_backend_id': 'image-v', 'vector_role': 'image_dense'}],
        'spaces': [{'id': 'old', 'embedding_model': legacy.MODEL}],
    }
    return bundle, {'tables': tables, 'isolated': {}, 'versions': {}}


@pytest.mark.parametrize('text_ok,image_ok', [(True, False), (False, True), (False, False), (True, True)])
def test_copy_only_rows_referenced_by_accepted_mapping(monkeypatch, text_ok, image_ok):
    bundle, raw = fixture_plan(text_ok=text_ok, image_ok=image_ok)
    original = copy.deepcopy(raw)
    monkeypatch.setattr(legacy.copy, 'build_plan', lambda _: copy.deepcopy(raw))
    plan = legacy.build_plan(bundle, 'e' * 64)
    tables = plan['tables']
    refs = {m['evidence_ref'] for m in tables['knowledge_evidence_embedding']}
    assert refs == ({'text-ref'} if text_ok else set()) | ({'image-ref'} if image_ok else set())
    assert {e['evidence_ref'] for e in tables['knowledge_evidence']} == refs
    assert {v['id'] for v in tables['document_chunk']} == (
        ({'text-v'} if text_ok else set()) | ({'image-v'} if image_ok else set())
    )
    assert len(tables['retrieval_chunk']) == int(text_ok)
    assert len(tables['knowledge_evidence_asset']) == int(image_ok)
    assert {s['file_id'] for s in tables['retrieval_index_state']} == (
        ({'text-file'} if text_ok else set()) | ({'image-file'} if image_ok else set())
    )
    assert len(tables['knowledge_vector_space']) == int(bool(refs))
    assert plan['legacy_counts'] == {'read_compatible': len(refs), 'source_rejected': 2 - len(refs)}
    assert raw == original
