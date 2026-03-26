import os
from types import SimpleNamespace

os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")

import open_webui.utils.layered_knowledge as layered_mod


def _fake_request():
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    OPEN_NOTEBOOK_BASE_URL="http://onb.local",
                    OPEN_NOTEBOOK_API_PASSWORD="secret",
                    OPEN_NOTEBOOK_TIMEOUT_SECS=12,
                    OPEN_NOTEBOOK_TRANSFORMATION_ABSTRACT="tr-abstract",
                    OPEN_NOTEBOOK_TRANSFORMATION_KEY_FINDINGS="tr-findings",
                    OPEN_NOTEBOOK_TRANSFORMATION_KEY_DATA="tr-data",
                )
            )
        )
    )


def test_get_layer_transformation_id_reads_config():
    request = _fake_request()
    assert layered_mod.get_layer_transformation_id(request, "abstract") == "tr-abstract"
    assert (
        layered_mod.get_layer_transformation_id(request, "key_findings")
        == "tr-abstract"
    )
    assert layered_mod.get_layer_transformation_id(request, "key_data") == "tr-abstract"
    assert layered_mod.get_layer_transformation_id(request, "unknown") is None


def test_sync_layers_for_file_triggers_remote_calls(monkeypatch):
    request = _fake_request()
    file_obj = SimpleNamespace(
        id="file-1",
        filename="demo.txt",
        meta={
            "open_notebook_source_id": "src-1",
            "open_notebook_source_content_hash": "hash-1",
        },
        data={"content": "demo"},
        hash="hash-1",
    )
    captured_requests = []
    captured_upserts = []

    def fake_request_json(method, url, password, timeout, payload=None):
        captured_requests.append((method, url, payload, timeout, password))
        if method == "GET":
            return [
                {
                    "id": "ins-1",
                    "insight_type": "abstract",
                    "content": "abstract content",
                },
                {
                    "id": "ins-2",
                    "insight_type": "key_data",
                    "content": "42",
                },
            ]
        return {"status": "pending"}

    def fake_upsert_layer(form_data, db=None):
        captured_upserts.append(form_data)
        return SimpleNamespace(**form_data.model_dump())

    monkeypatch.setattr(layered_mod, "_request_json", fake_request_json)
    monkeypatch.setattr(layered_mod.Files, "get_file_by_id", lambda *args, **kwargs: file_obj)
    monkeypatch.setattr(
        layered_mod.Files,
        "update_file_metadata_by_id",
        lambda *args, **kwargs: file_obj,
    )
    monkeypatch.setattr(
        layered_mod.KnowledgeLayers, "upsert_layer", fake_upsert_layer, raising=False
    )
    monkeypatch.setattr(
        layered_mod.KnowledgeLayers,
        "get_layers_by_file",
        lambda *args, **kwargs: [],
        raising=False,
    )

    layered_mod.sync_layers_for_file(request, "kb-1", "file-1")

    post_calls = [call for call in captured_requests if call[0] == "POST"]
    get_calls = [call for call in captured_requests if call[0] == "GET"]

    assert len(post_calls) == 1
    assert len(get_calls) == 1
    assert all(call[4] == "secret" for call in captured_requests)
    assert any(
        upsert.layer_type == "abstract" and upsert.status == "ready"
        for upsert in captured_upserts
    )
    assert not any(upsert.layer_type == "key_data" for upsert in captured_upserts)


def test_get_file_open_notebook_mapping_reads_source_keys():
    file_obj = SimpleNamespace(
        id="file-1",
        meta={
            "name": "demo",
            "open_notebook_source_id": "src-1",
            "open_notebook_source_ids": ["src-1", "src-2"],
        },
    )

    mapping = layered_mod.get_file_open_notebook_mapping(file_obj)
    assert mapping["open_notebook_source_id"] == "src-1"
    assert mapping["open_notebook_source_ids"] == ["src-1", "src-2"]


def test_save_file_open_notebook_mapping_preserves_other_meta(monkeypatch):
    file_obj = SimpleNamespace(
        id="file-1",
        meta={"name": "keep-me", "existing": "yes"},
    )
    captured = {}

    def fake_get_file_by_id(file_id, db=None):
        assert file_id == "file-1"
        return file_obj

    def fake_update_file_metadata_by_id(file_id, meta, db=None):
        captured["file_id"] = file_id
        captured["meta"] = meta
        file_obj.meta = {**file_obj.meta, **meta}
        return file_obj

    monkeypatch.setattr(layered_mod.Files, "get_file_by_id", fake_get_file_by_id)
    monkeypatch.setattr(
        layered_mod.Files,
        "update_file_metadata_by_id",
        fake_update_file_metadata_by_id,
    )

    result = layered_mod.save_file_open_notebook_mapping(
        "file-1",
        source_id="src-3",
        source_ids=["src-3", "src-4"],
        sync_status="ready",
        last_synced_at=123456,
        is_large_file=True,
        part_count=2,
    )

    assert captured["file_id"] == "file-1"
    assert captured["meta"]["open_notebook_source_id"] == "src-3"
    assert captured["meta"]["open_notebook_source_ids"] == ["src-3", "src-4"]
    assert captured["meta"]["open_notebook_sync_status"] == "ready"
    assert captured["meta"]["open_notebook_last_synced_at"] == 123456
    assert captured["meta"]["open_notebook_is_large_file"] is True
    assert captured["meta"]["open_notebook_part_count"] == 2
    assert file_obj.meta["name"] == "keep-me"
    assert file_obj.meta["existing"] == "yes"
    assert result["open_notebook_source_id"] == "src-3"


def test_sync_layers_for_file_creates_and_reuses_mapped_source_id(monkeypatch):
    request = _fake_request()
    file_obj = SimpleNamespace(
        id="file-1",
        filename="demo.txt",
        meta={},
        data={"content": "hello world"},
    )
    captured_requests = []
    captured_upserts = []

    def fake_get_file_by_id(file_id, db=None):
        assert file_id == "file-1"
        return file_obj

    def fake_update_file_metadata_by_id(file_id, meta, db=None):
        assert file_id == "file-1"
        file_obj.meta = {**(file_obj.meta or {}), **meta}
        return file_obj

    def fake_request_json(method, url, password, timeout, payload=None):
        captured_requests.append((method, url, payload))
        if method == "POST" and url.endswith("/api/sources"):
            return {"id": "src-created"}
        if method == "GET" and url.endswith("/api/sources/src-created/insights"):
            return [
                {
                    "id": "ins-1",
                    "insight_type": "abstract",
                    "content": "abstract",
                }
            ]
        return {"status": "pending"}

    def fake_upsert_layer(form_data, db=None):
        captured_upserts.append(form_data)
        return SimpleNamespace(**form_data.model_dump())

    monkeypatch.setattr(layered_mod.Files, "get_file_by_id", fake_get_file_by_id)
    monkeypatch.setattr(
        layered_mod.Files,
        "update_file_metadata_by_id",
        fake_update_file_metadata_by_id,
    )
    monkeypatch.setattr(layered_mod, "_request_json", fake_request_json)
    monkeypatch.setattr(
        layered_mod.KnowledgeLayers, "upsert_layer", fake_upsert_layer, raising=False
    )
    monkeypatch.setattr(
        layered_mod.KnowledgeLayers,
        "get_layers_by_file",
        lambda *args, **kwargs: [],
        raising=False,
    )

    layered_mod.sync_layers_for_file(request, "kb-1", "file-1")
    assert file_obj.meta["open_notebook_source_id"] == "src-created"
    assert any(
        method == "POST" and url.endswith("/api/sources")
        for method, url, _ in captured_requests
    )
    assert any(
        method == "POST" and "/api/sources/src-created/insights" in url
        for method, url, _ in captured_requests
    )
    assert all("/api/sources/file-1/insights" not in url for _, url, _ in captured_requests)
    assert any(
        upsert.layer_type == "abstract" and upsert.status == "ready"
        for upsert in captured_upserts
    )

    captured_requests.clear()
    layered_mod.sync_layers_for_file(request, "kb-1", "file-1")
    assert not any(
        method == "POST" and url.endswith("/api/sources")
        for method, url, _ in captured_requests
    )


def test_sync_layers_for_large_file_creates_chunk_sources_and_chunk_rows(monkeypatch):
    request = _fake_request()
    file_obj = SimpleNamespace(
        id="file-1",
        filename="large.txt",
        meta={},
        data={"content": "very large content"},
    )
    captured_requests = []
    captured_upserts = []
    source_create_count = {"count": 0}

    def fake_get_file_by_id(file_id, db=None):
        assert file_id == "file-1"
        return file_obj

    def fake_update_file_metadata_by_id(file_id, meta, db=None):
        file_obj.meta = {**(file_obj.meta or {}), **meta}
        return file_obj

    def fake_plan_text_chunks(text, max_tokens=24000, min_tail_tokens=1000):
        return [
            {"content": "chunk-1", "token_count": 15000, "part_index": 1, "part_total": 2},
            {"content": "chunk-2", "token_count": 14000, "part_index": 2, "part_total": 2},
        ]

    def fake_request_json(method, url, password, timeout, payload=None):
        captured_requests.append((method, url, payload))
        if method == "POST" and url.endswith("/api/sources"):
            source_create_count["count"] += 1
            return {"id": f"src-{source_create_count['count']}"}
        if method == "GET" and url.endswith("/api/sources/src-1/insights"):
            return [{"id": "ins-1", "insight_type": "abstract", "content": "abs-1"}]
        if method == "GET" and url.endswith("/api/sources/src-2/insights"):
            return [{"id": "ins-2", "insight_type": "abstract", "content": "abs-2"}]
        return {"status": "pending"}

    def fake_upsert_layer(form_data, db=None):
        captured_upserts.append(form_data)
        return SimpleNamespace(**form_data.model_dump())

    monkeypatch.setattr(layered_mod.Files, "get_file_by_id", fake_get_file_by_id)
    monkeypatch.setattr(
        layered_mod.Files,
        "update_file_metadata_by_id",
        fake_update_file_metadata_by_id,
    )
    monkeypatch.setattr(layered_mod, "plan_text_chunks", fake_plan_text_chunks)
    monkeypatch.setattr(layered_mod, "_request_json", fake_request_json)
    monkeypatch.setattr(
        layered_mod.KnowledgeLayers, "upsert_layer", fake_upsert_layer, raising=False
    )
    monkeypatch.setattr(
        layered_mod.KnowledgeLayers,
        "get_layers_by_file",
        lambda *args, **kwargs: [],
        raising=False,
    )

    layered_mod.sync_layers_for_file(request, "kb-1", "file-1")

    assert source_create_count["count"] == 2
    assert file_obj.meta["open_notebook_source_ids"] == ["src-1", "src-2"]
    assert file_obj.meta["open_notebook_is_large_file"] is True
    assert file_obj.meta["open_notebook_part_count"] == 2

    abstract_ready = [
        upsert
        for upsert in captured_upserts
        if upsert.layer_type == "abstract" and upsert.status == "ready"
    ]
    assert len(abstract_ready) == 2
    assert abstract_ready[0].part_index == 1
    assert abstract_ready[0].part_total == 2
    assert abstract_ready[0].display_title == "Abstract 1/2"
    assert abstract_ready[0].source_ref_id == "src-1"
    assert abstract_ready[1].part_index == 2
    assert abstract_ready[1].part_total == 2
    assert abstract_ready[1].display_title == "Abstract 2/2"
    assert abstract_ready[1].source_ref_id == "src-2"


def test_sync_layers_for_file_recreates_sources_when_content_hash_changes(monkeypatch):
    request = _fake_request()
    file_obj = SimpleNamespace(
        id="file-1",
        filename="demo.txt",
        meta={
            "open_notebook_source_id": "src-old",
            "open_notebook_source_ids": ["src-old"],
            "open_notebook_source_content_hash": "old-hash",
        },
        data={"content": "new content"},
    )
    captured_requests = []

    def fake_get_file_by_id(file_id, db=None):
        return file_obj

    def fake_update_file_metadata_by_id(file_id, meta, db=None):
        file_obj.meta = {**(file_obj.meta or {}), **meta}
        return file_obj

    def fake_request_json(method, url, password, timeout, payload=None):
        captured_requests.append((method, url, payload))
        if method == "POST" and url.endswith("/api/sources"):
            return {"id": "src-new"}
        if method == "GET" and url.endswith("/api/sources/src-new/insights"):
            return [{"id": "ins-1", "insight_type": "abstract", "content": "updated"}]
        return {"status": "pending"}

    monkeypatch.setattr(layered_mod.Files, "get_file_by_id", fake_get_file_by_id)
    monkeypatch.setattr(
        layered_mod.Files,
        "update_file_metadata_by_id",
        fake_update_file_metadata_by_id,
    )
    monkeypatch.setattr(layered_mod, "_request_json", fake_request_json)
    monkeypatch.setattr(
        layered_mod.KnowledgeLayers, "upsert_layer", lambda *args, **kwargs: SimpleNamespace(), raising=False
    )
    monkeypatch.setattr(
        layered_mod.KnowledgeLayers,
        "delete_layers_by_file",
        lambda *args, **kwargs: 0,
        raising=False,
    )
    monkeypatch.setattr(
        layered_mod.KnowledgeLayers,
        "get_layers_by_file",
        lambda *args, **kwargs: [],
        raising=False,
    )

    layered_mod.sync_layers_for_file(request, "kb-1", "file-1")

    assert any(method == "POST" and url.endswith("/api/sources") for method, url, _ in captured_requests)
    assert any("/api/sources/src-new/insights" in url for method, url, _ in captured_requests if method == "POST")
    assert all("/api/sources/src-old/insights" not in url for _, url, _ in captured_requests)
    assert file_obj.meta["open_notebook_source_id"] == "src-new"


def test_regenerate_layers_for_chunked_file_preserves_mapping_and_targets_all_parts(monkeypatch):
    request = _fake_request()
    file_obj = SimpleNamespace(
        id="file-1",
        filename="large.txt",
        meta={
            "open_notebook_source_ids": ["src-1", "src-2"],
            "open_notebook_source_id": "src-1",
            "open_notebook_is_large_file": True,
            "open_notebook_part_count": 2,
            "open_notebook_source_content_hash": "hash-large",
        },
        data={"content": "large content"},
        hash="hash-large",
    )
    captured_requests = []
    captured_meta_updates = []
    deleted_layer_calls = []
    captured_upserts = []

    def fake_get_file_by_id(file_id, db=None):
        return file_obj

    def fake_update_file_metadata_by_id(file_id, meta, db=None):
        captured_meta_updates.append(meta)
        file_obj.meta = {**(file_obj.meta or {}), **meta}
        return file_obj

    def fake_plan_text_chunks(text, max_tokens=24000, min_tail_tokens=1000):
        return [
            {"content": "chunk-1", "token_count": 15000, "part_index": 1, "part_total": 2},
            {"content": "chunk-2", "token_count": 14000, "part_index": 2, "part_total": 2},
        ]

    def fake_request_json(method, url, password, timeout, payload=None):
        captured_requests.append((method, url, payload))
        if method == "GET" and url.endswith("/api/sources/src-1/insights"):
            return [{"id": "ins-1", "insight_type": "abstract", "content": "part-1"}]
        if method == "GET" and url.endswith("/api/sources/src-2/insights"):
            return [{"id": "ins-2", "insight_type": "abstract", "content": "part-2"}]
        return {"status": "pending"}

    def fake_delete_layers_by_file(knowledge_id, file_id, layer_types=None, db=None):
        deleted_layer_calls.append((knowledge_id, file_id, layer_types))
        return 0

    def fake_upsert_layer(form_data, db=None):
        captured_upserts.append(form_data)
        return SimpleNamespace(**form_data.model_dump())

    monkeypatch.setattr(layered_mod.Files, "get_file_by_id", fake_get_file_by_id)
    monkeypatch.setattr(
        layered_mod.Files,
        "update_file_metadata_by_id",
        fake_update_file_metadata_by_id,
    )
    monkeypatch.setattr(layered_mod, "plan_text_chunks", fake_plan_text_chunks)
    monkeypatch.setattr(layered_mod, "_request_json", fake_request_json)
    monkeypatch.setattr(
        layered_mod.KnowledgeLayers,
        "delete_layers_by_file",
        fake_delete_layers_by_file,
        raising=False,
    )
    monkeypatch.setattr(
        layered_mod.KnowledgeLayers, "upsert_layer", fake_upsert_layer, raising=False
    )
    monkeypatch.setattr(
        layered_mod.KnowledgeLayers,
        "get_layers_by_file",
        lambda *args, **kwargs: [],
        raising=False,
    )

    layered_mod.regenerate_layers_for_file(
        request,
        "kb-1",
        "file-1",
        layer_types=["abstract"],
        force=False,
    )

    post_urls = [url for method, url, _ in captured_requests if method == "POST"]
    assert any("/api/sources/src-1/insights" in url for url in post_urls)
    assert any("/api/sources/src-2/insights" in url for url in post_urls)
    assert deleted_layer_calls == [("kb-1", "file-1", ["abstract"])]
    assert file_obj.meta["open_notebook_source_ids"] == ["src-1", "src-2"]
    assert file_obj.meta["open_notebook_is_large_file"] is True
    ready_parts = [
        upsert
        for upsert in captured_upserts
        if upsert.layer_type == "abstract" and upsert.status == "ready"
    ]
    assert len(ready_parts) == 2
    assert {part.part_index for part in ready_parts} == {1, 2}
