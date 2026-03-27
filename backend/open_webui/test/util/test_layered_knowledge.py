import os
from types import SimpleNamespace

os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")

import open_webui.utils.layered_knowledge as layered_mod


def _fake_request():
    return SimpleNamespace(
        state=SimpleNamespace(),
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    TASK_MODEL="task-model",
                    TASK_MODEL_EXTERNAL="",
                    DEFAULT_MODELS="task-model",
                    OPEN_NOTEBOOK_BASE_URL="http://onb.local",
                    OPEN_NOTEBOOK_API_PASSWORD="secret",
                    OPEN_NOTEBOOK_TIMEOUT_SECS=12,
                    OPEN_NOTEBOOK_TRANSFORMATION_ABSTRACT="tr-abstract",
                    OPEN_NOTEBOOK_TRANSFORMATION_KEY_FINDINGS="tr-findings",
                    OPEN_NOTEBOOK_TRANSFORMATION_KEY_DATA="tr-data",
                ),
                MODELS={
                    "task-model": {
                        "id": "task-model",
                        "owned_by": "openai",
                        "connection_type": "local",
                        "info": {"params": {"max_tokens": 256}},
                    }
                },
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


def test_get_layer_generation_model_id_falls_back_deterministically_without_hidden_config():
    request = SimpleNamespace(
        state=SimpleNamespace(),
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    TASK_MODEL="",
                    TASK_MODEL_EXTERNAL="",
                    DEFAULT_MODELS="",
                ),
                MODELS={
                    "z-model": {
                        "id": "z-model",
                        "owned_by": "openai",
                        "connection_type": "local",
                    },
                    "arena": {
                        "id": "arena",
                        "owned_by": "arena",
                        "connection_type": "local",
                    },
                    "a-model": {
                        "id": "a-model",
                        "owned_by": "openai",
                        "connection_type": "local",
                    },
                },
            )
        ),
    )

    assert layered_mod._get_layer_generation_model_id(request, "abstract") == "a-model"


def test_sync_layers_for_file_triggers_remote_calls(monkeypatch):
    request = _fake_request()
    file_obj = SimpleNamespace(
        id="file-1",
        filename="demo.txt",
        meta={},
        data={"content": "demo"},
        hash="hash-1",
    )
    captured_payloads = []
    captured_upserts = []

    async def fake_generate_chat_completion(request, form_data, user, **kwargs):
        captured_payloads.append(form_data)
        return {
            "choices": [
                {
                    "message": {
                        "content": "abstract content",
                    }
                }
            ]
        }

    def fake_upsert_layer(form_data, db=None):
        captured_upserts.append(form_data)
        return SimpleNamespace(**form_data.model_dump())

    monkeypatch.setattr(
        layered_mod,
        "generate_chat_completion",
        fake_generate_chat_completion,
        raising=False,
    )
    monkeypatch.setattr(
        layered_mod,
        "_request_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("open notebook request path should be inactive")
        ),
        raising=False,
    )
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

    assert len(captured_payloads) == 1
    assert captured_payloads[0]["model"] == "task-model"
    assert "demo" in captured_payloads[0]["messages"][0]["content"]
    assert any(
        upsert.layer_type == "abstract" and upsert.status == "ready"
        for upsert in captured_upserts
    )
    assert not any(upsert.layer_type == "key_data" for upsert in captured_upserts)
    assert all(upsert.source_system == "open_webui" for upsert in captured_upserts)


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


def test_sync_layers_for_file_regenerates_natively_without_open_notebook_mapping(monkeypatch):
    request = _fake_request()
    file_obj = SimpleNamespace(
        id="file-1",
        filename="demo.txt",
        meta={},
        data={"content": "hello world"},
    )
    captured_payloads = []
    captured_upserts = []

    def fake_get_file_by_id(file_id, db=None):
        assert file_id == "file-1"
        return file_obj

    def fake_update_file_metadata_by_id(file_id, meta, db=None):
        assert file_id == "file-1"
        file_obj.meta = {**(file_obj.meta or {}), **meta}
        return file_obj

    async def fake_generate_chat_completion(request, form_data, user, **kwargs):
        captured_payloads.append(form_data)
        return {"choices": [{"message": {"content": "abstract"}}]}

    def fake_upsert_layer(form_data, db=None):
        captured_upserts.append(form_data)
        return SimpleNamespace(**form_data.model_dump())

    monkeypatch.setattr(layered_mod.Files, "get_file_by_id", fake_get_file_by_id)
    monkeypatch.setattr(
        layered_mod.Files,
        "update_file_metadata_by_id",
        fake_update_file_metadata_by_id,
    )
    monkeypatch.setattr(
        layered_mod,
        "generate_chat_completion",
        fake_generate_chat_completion,
        raising=False,
    )
    monkeypatch.setattr(
        layered_mod,
        "_request_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("open notebook request path should be inactive")
        ),
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

    layered_mod.sync_layers_for_file(request, "kb-1", "file-1")
    assert len(captured_payloads) == 1
    assert file_obj.meta == {}
    assert any(
        upsert.layer_type == "abstract" and upsert.status == "ready"
        for upsert in captured_upserts
    )
    assert all(upsert.source_system == "open_webui" for upsert in captured_upserts)
    assert all(upsert.source_ref_id == "chunk:1" for upsert in captured_upserts)

    captured_payloads.clear()
    layered_mod.sync_layers_for_file(request, "kb-1", "file-1")
    assert len(captured_payloads) == 1


def test_sync_layers_for_large_file_creates_native_chunk_rows(monkeypatch):
    request = _fake_request()
    file_obj = SimpleNamespace(
        id="file-1",
        filename="large.txt",
        meta={},
        data={"content": "very large content"},
    )
    captured_payloads = []
    captured_upserts = []

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

    async def fake_generate_chat_completion(request, form_data, user, **kwargs):
        captured_payloads.append(form_data)
        return {
            "choices": [
                {
                    "message": {
                        "content": f"abs-{len(captured_payloads)}",
                    }
                }
            ]
        }

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
    monkeypatch.setattr(
        layered_mod,
        "generate_chat_completion",
        fake_generate_chat_completion,
        raising=False,
    )
    monkeypatch.setattr(
        layered_mod,
        "_request_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("open notebook request path should be inactive")
        ),
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

    layered_mod.sync_layers_for_file(request, "kb-1", "file-1")

    assert len(captured_payloads) == 2
    assert file_obj.meta == {}

    abstract_ready = [
        upsert
        for upsert in captured_upserts
        if upsert.layer_type == "abstract" and upsert.status == "ready"
    ]
    assert len(abstract_ready) == 2
    assert abstract_ready[0].part_index == 1
    assert abstract_ready[0].part_total == 2
    assert abstract_ready[0].display_title == "Abstract 1/2"
    assert abstract_ready[0].source_ref_id == "chunk:1"
    assert abstract_ready[1].part_index == 2
    assert abstract_ready[1].part_total == 2
    assert abstract_ready[1].display_title == "Abstract 2/2"
    assert abstract_ready[1].source_ref_id == "chunk:2"


def test_sync_layers_for_file_updates_content_hash_for_native_rows(monkeypatch):
    request = _fake_request()
    file_obj = SimpleNamespace(
        id="file-1",
        filename="demo.txt",
        meta={},
        data={"content": "new content"},
        hash="new-hash",
    )
    captured_upserts = []

    def fake_get_file_by_id(file_id, db=None):
        return file_obj

    def fake_update_file_metadata_by_id(file_id, meta, db=None):
        file_obj.meta = {**(file_obj.meta or {}), **meta}
        return file_obj

    async def fake_generate_chat_completion(request, form_data, user, **kwargs):
        return {"choices": [{"message": {"content": "updated"}}]}

    monkeypatch.setattr(layered_mod.Files, "get_file_by_id", fake_get_file_by_id)
    monkeypatch.setattr(
        layered_mod.Files,
        "update_file_metadata_by_id",
        fake_update_file_metadata_by_id,
    )
    monkeypatch.setattr(
        layered_mod,
        "generate_chat_completion",
        fake_generate_chat_completion,
        raising=False,
    )
    monkeypatch.setattr(
        layered_mod,
        "_request_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("open notebook request path should be inactive")
        ),
        raising=False,
    )
    monkeypatch.setattr(
        layered_mod.KnowledgeLayers,
        "upsert_layer",
        lambda form_data, db=None: captured_upserts.append(form_data) or SimpleNamespace(),
        raising=False,
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

    ready_rows = [row for row in captured_upserts if row.status == "ready"]
    assert len(ready_rows) == 1
    assert ready_rows[0].content_hash == "new-hash"
    assert ready_rows[0].source_system == "open_webui"


def test_regenerate_layers_for_chunked_file_targets_all_parts_natively(monkeypatch):
    request = _fake_request()
    file_obj = SimpleNamespace(
        id="file-1",
        filename="large.txt",
        meta={},
        data={"content": "large content"},
        hash="hash-large",
    )
    captured_payloads = []
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

    async def fake_generate_chat_completion(request, form_data, user, **kwargs):
        captured_payloads.append(form_data)
        return {
            "choices": [
                {
                    "message": {
                        "content": f"part-{len(captured_payloads)}",
                    }
                }
            ]
        }

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
    monkeypatch.setattr(
        layered_mod,
        "generate_chat_completion",
        fake_generate_chat_completion,
        raising=False,
    )
    monkeypatch.setattr(
        layered_mod,
        "_request_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("open notebook request path should be inactive")
        ),
        raising=False,
    )
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

    assert len(captured_payloads) == 2
    assert deleted_layer_calls == [("kb-1", "file-1", ["abstract"])]
    assert captured_meta_updates == []
    ready_parts = [
        upsert
        for upsert in captured_upserts
        if upsert.layer_type == "abstract" and upsert.status == "ready"
    ]
    assert len(ready_parts) == 2
    assert {part.part_index for part in ready_parts} == {1, 2}
    assert {part.source_ref_id for part in ready_parts} == {"chunk:1", "chunk:2"}
