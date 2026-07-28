from open_webui.utils.middleware import build_citation_map_from_sources, get_source_context


def test_build_citation_map_from_sources_uses_unique_evidence_refs_in_source_order():
    sources = [
        {
            'source': {'id': 'ke:kb-1:file-a:text_chunk:1:aaa'},
            'document': ['First evidence'],
            'metadata': [{'evidence_ref': 'ke:kb-1:file-a:text_chunk:1:aaa'}],
        },
        {
            'source': {'id': 'ke:kb-1:file-b:standalone_image:1:bbb'},
            'document': ['Second evidence'],
            'metadata': [{'evidence_ref': 'ke:kb-1:file-b:standalone_image:1:bbb'}],
        },
        {
            'source': {'id': 'ke:kb-1:file-a:text_chunk:1:aaa'},
            'document': ['Duplicate evidence'],
            'metadata': [{'evidence_ref': 'ke:kb-1:file-a:text_chunk:1:aaa'}],
        },
    ]

    assert build_citation_map_from_sources(sources) == {
        '1': 'ke:kb-1:file-a:text_chunk:1:aaa',
        '2': 'ke:kb-1:file-b:standalone_image:1:bbb',
    }


def test_get_source_context_uses_evidence_ref_for_evidence_level_numbering():
    sources = [
        {
            'source': {
                'id': 'ke:kb-1:file-a:text_chunk:1:aaa',
                'name': 'paper.pdf',
                'type': 'evidence',
                'evidence_ref': 'ke:kb-1:file-a:text_chunk:1:aaa',
            },
            'document': ['First evidence'],
            'metadata': [
                {
                    'source': 'paper.pdf',
                    'evidence_ref': 'ke:kb-1:file-a:text_chunk:1:aaa',
                    'modality': 'text',
                }
            ],
        },
        {
            'source': {
                'id': 'ke:kb-1:file-a:text_chunk:2:bbb',
                'name': 'paper.pdf',
                'type': 'evidence',
                'evidence_ref': 'ke:kb-1:file-a:text_chunk:2:bbb',
            },
            'document': ['Second evidence from the same file'],
            'metadata': [
                {
                    'source': 'paper.pdf',
                    'evidence_ref': 'ke:kb-1:file-a:text_chunk:2:bbb',
                    'modality': 'text',
                }
            ],
        },
    ]

    context = get_source_context(sources)

    assert '<source id="1"' in context
    assert '<source id="2"' in context
    assert 'evidence-ref="ke:kb-1:file-a:text_chunk:1:aaa"' in context
    assert 'evidence-ref="ke:kb-1:file-a:text_chunk:2:bbb"' in context
