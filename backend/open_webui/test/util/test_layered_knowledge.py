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
        == "tr-findings"
    )
    assert layered_mod.get_layer_transformation_id(request, "key_data") == "tr-data"
    assert layered_mod.get_layer_transformation_id(request, "unknown") is None


def test_sync_layers_for_file_triggers_remote_calls(monkeypatch):
    request = _fake_request()
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

    assert len(post_calls) == 3
    assert len(get_calls) == 1
    assert all(call[4] == "secret" for call in captured_requests)
    assert any(
        upsert.layer_type == "abstract" and upsert.status == "ready"
        for upsert in captured_upserts
    )
    assert any(
        upsert.layer_type == "key_data" and upsert.status == "ready"
        for upsert in captured_upserts
    )
