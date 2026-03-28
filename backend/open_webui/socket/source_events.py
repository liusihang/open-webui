from __future__ import annotations


def merge_persisted_sources(existing_sources: list | None, data: dict | None) -> list:
    merged = list(existing_sources or [])

    if not isinstance(data, dict):
        return merged

    batched_sources = data.get("sources")
    if isinstance(batched_sources, list):
        for source in batched_sources:
            if isinstance(source, dict):
                merged.append(source)
        return merged

    merged.append(data)
    return merged
