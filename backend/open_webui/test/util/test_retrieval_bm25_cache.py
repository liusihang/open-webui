import importlib


def _load_cache_module():
    return importlib.import_module("open_webui.retrieval.bm25_cache")


def test_cache_reuses_builder_result_for_same_collection_key() -> None:
    mod = _load_cache_module()
    cache = mod.BM25RetrieverCache()
    calls = []

    def builder():
        calls.append("build")
        return {"name": "retriever"}, 3

    retriever_1, hit_1 = cache.get_or_create("kb-1", False, builder)
    retriever_2, hit_2 = cache.get_or_create("kb-1", False, builder)

    assert hit_1 is False
    assert hit_2 is True
    assert retriever_1 is retriever_2
    assert calls == ["build"]


def test_cache_separates_entries_by_enriched_text_flag() -> None:
    mod = _load_cache_module()
    cache = mod.BM25RetrieverCache()
    calls = []

    def builder_plain():
        calls.append("plain")
        return {"name": "plain"}, 2

    def builder_enriched():
        calls.append("enriched")
        return {"name": "enriched"}, 2

    plain, plain_hit = cache.get_or_create("kb-1", False, builder_plain)
    enriched, enriched_hit = cache.get_or_create("kb-1", True, builder_enriched)

    assert plain_hit is False
    assert enriched_hit is False
    assert plain != enriched
    assert calls == ["plain", "enriched"]


def test_collection_invalidation_removes_matching_entries() -> None:
    mod = _load_cache_module()
    cache = mod.BM25RetrieverCache()

    cache.get_or_create("kb-1", False, lambda: ({"name": "plain"}, 1))
    cache.get_or_create("kb-1", True, lambda: ({"name": "enriched"}, 1))
    cache.get_or_create("kb-2", False, lambda: ({"name": "other"}, 1))

    cache.invalidate("kb-1")

    assert cache.size() == 1
    retriever, hit = cache.get_or_create("kb-2", False, lambda: ({"name": "new"}, 1))
    assert hit is True
    assert retriever == {"name": "other"}


def test_clear_removes_all_entries() -> None:
    mod = _load_cache_module()
    cache = mod.BM25RetrieverCache()

    cache.get_or_create("kb-1", False, lambda: ({"name": "plain"}, 1))
    cache.get_or_create("kb-2", False, lambda: ({"name": "other"}, 1))

    cache.clear()

    assert cache.size() == 0
