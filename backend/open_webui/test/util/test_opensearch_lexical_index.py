import os

os.environ.setdefault("WEBUI_SECRET_KEY", "test-secret")
os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")

from open_webui.retrieval.lexical.opensearch import OpenSearchLexicalClient


class FakeIndices:
    def __init__(self, *, fail_icu_once=False):
        self.created = []
        self.existing = set()
        self.alias_updates = []
        self.fail_icu_once = fail_icu_once

    def exists(self, index):
        return index in self.existing

    def create(self, index, body):
        self.created.append((index, body))
        if self.fail_icu_once:
            self.fail_icu_once = False
            raise RuntimeError("Unknown tokenizer [icu_tokenizer]")
        self.existing.add(index)

    def update_aliases(self, body):
        self.alias_updates.append(body)

    def refresh(self, index):
        self.refreshed = index


class FakeOpenSearch:
    def __init__(self, *, fail_icu_once=False, search_result=None):
        self.indices = FakeIndices(fail_icu_once=fail_icu_once)
        self.search_calls = []
        self.search_result = search_result or {"hits": {"hits": []}}

    def search(self, index, body):
        self.search_calls.append((index, body))
        return self.search_result


def test_build_index_body_includes_required_multifields_and_icu_fallback():
    client = OpenSearchLexicalClient(client=FakeOpenSearch())

    body = client.build_index_body(use_icu=True)
    props = body["mappings"]["properties"]
    analysis = body["settings"]["analysis"]

    for field in ("text", "title", "metadata_headings"):
        assert set(props[field]["fields"]) >= {"icu", "cjk", "en"}
        assert props[field]["fields"]["icu"]["analyzer"] == "lexical_icu"
        assert props[field]["fields"]["cjk"]["analyzer"] == "lexical_cjk"
        assert props[field]["fields"]["en"]["analyzer"] == "lexical_en"

    assert props["name"]["fields"]["ngram"]["analyzer"] == "lexical_ngram"
    assert props["source"]["fields"]["ngram"]["analyzer"] == "lexical_ngram"
    assert analysis["analyzer"]["lexical_icu"]["tokenizer"] == "icu_tokenizer"

    fallback_body = client.build_index_body(use_icu=False)
    fallback_props = fallback_body["mappings"]["properties"]
    fallback_analysis = fallback_body["settings"]["analysis"]

    assert "lexical_icu" not in fallback_analysis["analyzer"]
    assert fallback_props["text"]["fields"]["icu"]["analyzer"] == "lexical_cjk"
    assert fallback_props["title"]["fields"]["en"]["analyzer"] == "lexical_en"
    assert fallback_props["metadata_headings"]["fields"]["cjk"]["analyzer"] == "lexical_cjk"
    assert fallback_props["name"]["fields"]["ngram"]["analyzer"] == "lexical_ngram"


def test_ensure_index_creates_versioned_index_sets_alias_and_retries_without_icu():
    fake = FakeOpenSearch(fail_icu_once=True)
    client = OpenSearchLexicalClient(client=fake)

    index_name = client.ensure_index(version=2, use_icu=True)

    assert index_name == "retrieval_lexical_v2"
    assert [call[0] for call in fake.indices.created] == [
        "retrieval_lexical_v2",
        "retrieval_lexical_v2",
    ]
    assert fake.indices.created[0][1]["mappings"]["properties"]["text"]["fields"]["icu"][
        "analyzer"
    ] == "lexical_icu"
    assert fake.indices.created[1][1]["mappings"]["properties"]["text"]["fields"]["icu"][
        "analyzer"
    ] == "lexical_cjk"
    assert fake.indices.alias_updates == [
        {
            "actions": [
                {
                    "remove": {
                        "index": "*",
                        "alias": "retrieval_lexical_current",
                        "must_exist": False,
                    }
                },
                {
                    "add": {
                        "index": "retrieval_lexical_v2",
                        "alias": "retrieval_lexical_current",
                    }
                },
            ]
        }
    ]


def test_bulk_upsert_uses_chunk_uid_as_id_and_extracts_metadata_fields():
    fake = FakeOpenSearch()
    captured_actions = []

    def fake_bulk(client, actions):
        captured_actions.extend(actions)
        return len(actions), []

    client = OpenSearchLexicalClient(client=fake, bulk_helper=fake_bulk)

    count = client.bulk_upsert(
        [
            {
                "chunk_uid": "chunk_1",
                "collection_id": "collection-1",
                "knowledge_id": "knowledge-1",
                "collection_name": "Collection One",
                "file_id": "file-1",
                "file_version": 3,
                "chunk_version": 2,
                "content_hash": "hash-a",
                "chunker_config_hash": "chunker-a",
                "is_active": True,
                "text": "alpha beta",
                "metadata": {
                    "title": "Document Title",
                    "name": "document.pdf",
                    "source": "manual upload",
                    "headings": ["Intro", "Details"],
                    "extra": "kept",
                },
            }
        ]
    )

    assert count == 1
    assert captured_actions == [
        {
            "_op_type": "index",
            "_index": "retrieval_lexical_current",
            "_id": "chunk_1",
            "_source": {
                "chunk_uid": "chunk_1",
                "collection_id": "collection-1",
                "knowledge_id": "knowledge-1",
                "collection_name": "Collection One",
                "file_id": "file-1",
                "file_version": 3,
                "chunk_version": 2,
                "content_hash": "hash-a",
                "chunker_config_hash": "chunker-a",
                "is_active": True,
                "text": "alpha beta",
                "title": "Document Title",
                "name": "document.pdf",
                "source": "manual upload",
                "metadata_headings": ["Intro", "Details"],
                "metadata": {
                    "title": "Document Title",
                    "name": "document.pdf",
                    "source": "manual upload",
                    "headings": ["Intro", "Details"],
                    "extra": "kept",
                },
            },
        }
    ]
    assert fake.indices.refreshed == "retrieval_lexical_current"


def test_bulk_upsert_prefers_sqlalchemy_metadata_attribute_name():
    class ChunkObject:
        metadata = object()
        chunk_uid = "chunk_object"
        collection_id = "collection-1"
        knowledge_id = "knowledge-1"
        collection_name = "Collection One"
        file_id = "file-1"
        file_version = 1
        chunk_version = 1
        content_hash = "hash-a"
        chunker_config_hash = "chunker-a"
        is_active = True
        text = "alpha beta"
        metadata_ = {
            "title": "Object Title",
            "name": "object.pdf",
            "source": "object upload",
            "headings": ["Object Heading"],
        }

    captured_actions = []

    def fake_bulk(client, actions):
        captured_actions.extend(actions)
        return len(actions), []

    client = OpenSearchLexicalClient(client=FakeOpenSearch(), bulk_helper=fake_bulk)

    assert client.bulk_upsert([ChunkObject()]) == 1
    source = captured_actions[0]["_source"]
    assert source["title"] == "Object Title"
    assert source["name"] == "object.pdf"
    assert source["source"] == "object upload"
    assert source["metadata_headings"] == ["Object Heading"]
    assert source["metadata"] == ChunkObject.metadata_


def test_search_filters_weighted_fields_and_returns_hit_metadata_only():
    fake = FakeOpenSearch(
        search_result={
            "hits": {
                "hits": [
                    {
                        "_id": "chunk_1",
                        "_score": 7.5,
                        "_source": {
                            "chunk_uid": "chunk_1",
                            "metadata": {"title": "Document Title"},
                            "text": "should not be returned",
                        },
                    }
                ]
            }
        }
    )
    client = OpenSearchLexicalClient(client=fake)

    hits = client.search(
        "病毒 capsid",
        collection_ids=["collection-1"],
        knowledge_ids=["knowledge-1"],
        file_ids=["file-1"],
        k=5,
    )

    assert hits == [client.hit_type(chunk_uid="chunk_1", score=7.5, metadata={"title": "Document Title"})]

    index, body = fake.search_calls[0]
    assert index == "retrieval_lexical_current"
    assert body["size"] == 5
    assert body["_source"] == ["chunk_uid", "metadata"]

    bool_query = body["query"]["bool"]
    fields = bool_query["must"][0]["multi_match"]["fields"]
    assert "text.icu^4" in fields
    assert "title.icu^5" in fields
    assert "metadata_headings.cjk^2" in fields
    assert "name.ngram^1.5" in fields
    assert "source.ngram^1.2" in fields

    assert bool_query["filter"] == [
        {"term": {"is_active": True}},
        {"terms": {"collection_id": ["collection-1"]}},
        {"terms": {"knowledge_id": ["knowledge-1"]}},
        {"terms": {"file_id": ["file-1"]}},
    ]
