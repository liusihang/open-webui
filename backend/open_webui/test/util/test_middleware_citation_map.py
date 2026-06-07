from open_webui.utils.middleware import build_citation_map_from_sources


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
