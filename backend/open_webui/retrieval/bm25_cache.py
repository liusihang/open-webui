from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable


@dataclass(slots=True)
class BM25CacheEntry:
    payload: Any
    doc_count: int


class BM25RetrieverCache:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, bool], BM25CacheEntry] = {}
        self._lock = Lock()

    def get_or_create(
        self,
        collection_name: str,
        enable_enriched_texts: bool,
        builder: Callable[[], tuple[Any, int]],
    ) -> tuple[Any, bool]:
        key = (collection_name, enable_enriched_texts)
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                return entry.payload, True

        payload, doc_count = builder()

        with self._lock:
            self._entries[key] = BM25CacheEntry(payload=payload, doc_count=doc_count)

        return payload, False

    def invalidate(
        self, collection_name: str, enable_enriched_texts: bool | None = None
    ) -> None:
        with self._lock:
            if enable_enriched_texts is None:
                keys = [
                    key for key in self._entries.keys() if key[0] == collection_name
                ]
                for key in keys:
                    self._entries.pop(key, None)
            else:
                self._entries.pop((collection_name, enable_enriched_texts), None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._entries)


BM25_RETRIEVER_CACHE = BM25RetrieverCache()


def invalidate_bm25_cache(
    collection_name: str, enable_enriched_texts: bool | None = None
) -> None:
    BM25_RETRIEVER_CACHE.invalidate(collection_name, enable_enriched_texts)


def invalidate_all_bm25_cache() -> None:
    BM25_RETRIEVER_CACHE.clear()
