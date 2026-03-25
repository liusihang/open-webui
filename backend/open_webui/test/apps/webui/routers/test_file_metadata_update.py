from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from open_webui.routers import files as files_mod


def _fake_file(owner_id: str = "owner-1"):
    return SimpleNamespace(
        id="file-1",
        user_id=owner_id,
        hash=None,
        filename="paper.pdf",
        path="/tmp/paper.pdf",
        data={"content": "original"},
        meta={
            "name": "paper.pdf",
            "content_type": "application/pdf",
            "data": {
                "existing": "keep-me",
                "nested": {"old": "value"},
            },
        },
        created_at=1,
        updated_at=1,
    )


def test_owner_can_update_file_metadata(monkeypatch):
    file_obj = _fake_file()
    processed = []

    def fake_get_file_by_id(*args, **kwargs):
        return file_obj

    def fake_update_file_metadata_by_id(file_id, meta, db=None):
        return SimpleNamespace(**{**file_obj.__dict__, "meta": {**file_obj.meta, **meta}})

    monkeypatch.setattr(files_mod.Files, "get_file_by_id", fake_get_file_by_id)
    monkeypatch.setattr(
        files_mod.Files,
        "update_file_metadata_by_id",
        fake_update_file_metadata_by_id,
    )
    monkeypatch.setattr(files_mod, "has_access_to_file", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        files_mod.Knowledges,
        "get_knowledges_by_file_id",
        lambda *args, **kwargs: [SimpleNamespace(id="kb-1")],
        raising=False,
    )
    monkeypatch.setattr(
        files_mod.VECTOR_DB_CLIENT,
        "delete",
        lambda *args, **kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        files_mod,
        "process_file",
        lambda request, form_data, user, db=None: processed.append(form_data.collection_name),
        raising=False,
    )

    result = files_mod.update_file_metadata_by_id(
        request=SimpleNamespace(),
        id="file-1",
        form_data=files_mod.FileMetadataUpdateForm(meta={"source": "zotero-sync"}),
        user=SimpleNamespace(id="owner-1", role="user"),
        db=None,
    )

    assert result.meta["source"] == "zotero-sync"
    assert result.meta["name"] == "paper.pdf"
    assert result.meta["content_type"] == "application/pdf"
    assert processed == [None, "kb-1"]


def test_non_owner_without_write_access_gets_not_found(monkeypatch):
    file_obj = _fake_file()
    update_called = {"called": False}

    monkeypatch.setattr(files_mod.Files, "get_file_by_id", lambda *args, **kwargs: file_obj)

    def fake_update_file_metadata_by_id(*args, **kwargs):
        update_called["called"] = True
        return None

    monkeypatch.setattr(
        files_mod.Files,
        "update_file_metadata_by_id",
        fake_update_file_metadata_by_id,
    )
    monkeypatch.setattr(files_mod, "has_access_to_file", lambda *args, **kwargs: False)

    with pytest.raises(HTTPException) as exc_info:
        files_mod.update_file_metadata_by_id(
            request=SimpleNamespace(),
            id="file-1",
            form_data=files_mod.FileMetadataUpdateForm(meta={"source": "forbidden-update"}),
            user=SimpleNamespace(id="intruder", role="user"),
            db=None,
        )

    assert exc_info.value.status_code == 404
    assert update_called["called"] is False


def test_metadata_data_merges_existing_keys(monkeypatch):
    file_obj = _fake_file()
    captured = {}

    monkeypatch.setattr(files_mod.Files, "get_file_by_id", lambda *args, **kwargs: file_obj)

    def fake_update_file_metadata_by_id(file_id, meta, db=None):
        captured["meta"] = meta
        return SimpleNamespace(**{**file_obj.__dict__, "meta": {**file_obj.meta, **meta}})

    monkeypatch.setattr(
        files_mod.Files,
        "update_file_metadata_by_id",
        fake_update_file_metadata_by_id,
    )
    monkeypatch.setattr(files_mod, "has_access_to_file", lambda *args, **kwargs: False)

    result = files_mod.update_file_metadata_by_id(
        request=SimpleNamespace(),
        id="file-1",
        form_data=files_mod.FileMetadataUpdateForm(
            meta={
                "data": {
                    "zotero": {"item_key": "ITEM-1"},
                    "nested": {"new": "value"},
                }
            }
        ),
        user=SimpleNamespace(id="owner-1", role="user"),
        db=None,
    )

    assert captured["meta"]["data"]["existing"] == "keep-me"
    assert captured["meta"]["data"]["zotero"]["item_key"] == "ITEM-1"
    assert captured["meta"]["data"]["nested"] == {"new": "value"}
    assert result.meta["data"]["existing"] == "keep-me"
