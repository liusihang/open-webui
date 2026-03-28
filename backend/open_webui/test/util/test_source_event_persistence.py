from open_webui.socket.source_events import merge_persisted_sources


def test_merge_persisted_sources_flattens_batched_source_payloads():
    existing = [{"source": {"id": "seed", "name": "Seed"}}]
    batched_payload = {
        "sources": [
            {
                "source": {"id": "https://example.com", "name": "Example"},
                "document": ["Example\nsnippet"],
                "metadata": [
                    {
                        "source": "https://example.com",
                        "name": "Example",
                        "url": "https://example.com",
                    }
                ],
            }
        ]
    }

    merged = merge_persisted_sources(existing, batched_payload)

    assert merged == [
        {"source": {"id": "seed", "name": "Seed"}},
        {
            "source": {"id": "https://example.com", "name": "Example"},
            "document": ["Example\nsnippet"],
            "metadata": [
                {
                    "source": "https://example.com",
                    "name": "Example",
                    "url": "https://example.com",
                }
            ],
        },
    ]
