from types import SimpleNamespace

import pytest

from open_webui.models.knowledge import KnowledgeUpdateForm
from open_webui.routers import knowledge as knowledge_mod


@pytest.mark.asyncio
async def test_create_knowledge_with_meta(monkeypatch):
    captured: dict = {}

    async def fake_embed(*args, **kwargs):
        return True

    def fake_insert_new_knowledge(user_id, form_data):
        captured["user_id"] = user_id
        captured["meta"] = form_data.meta
        return SimpleNamespace(
            id="kb-zotero-create",
            name=form_data.name,
            description=form_data.description,
            meta=form_data.meta,
            user_id=user_id,
            access_grants=[],
            created_at=1,
            updated_at=1,
            model_dump=lambda: {
                "id": "kb-zotero-create",
                "name": form_data.name,
                "description": form_data.description,
                "meta": form_data.meta,
                "user_id": user_id,
                "access_grants": [],
                "created_at": 1,
                "updated_at": 1,
            },
        )

    monkeypatch.setattr(knowledge_mod, "has_permission", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        knowledge_mod, "filter_allowed_access_grants", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        knowledge_mod.Knowledges,
        "insert_new_knowledge",
        fake_insert_new_knowledge,
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod, "embed_knowledge_base_metadata", fake_embed, raising=False
    )

    fake_request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(config=SimpleNamespace(USER_PERMISSIONS={}))
        )
    )
    fake_user = SimpleNamespace(id="user-42", role="user")
    meta = {
        "source": "zotero",
        "zotero_top_collection_key": "ABCD1234",
        "zotero_top_collection_name": "Machine Learning",
    }
    form_data = knowledge_mod.KnowledgeForm(
        name="Zotero / ML",
        description="synced",
        meta=meta,
        access_grants=[],
    )

    result = await knowledge_mod.create_new_knowledge(
        request=fake_request, form_data=form_data, user=fake_user
    )

    assert captured["user_id"] == "user-42"
    assert captured["meta"] == meta
    assert result.meta == meta
    assert result.user_id == "user-42"


@pytest.mark.asyncio
async def test_update_knowledge_with_meta_only_payload(monkeypatch):
    captured: dict = {}

    async def fake_embed(*args, **kwargs):
        return True

    existing = SimpleNamespace(
        id="kb-zotero-update",
        name="Before",
        description="Before update",
        meta={"source": "legacy"},
        user_id="user-42",
        access_grants=[],
        created_at=1,
        updated_at=1,
    )

    def fake_get_knowledge_by_id(id):
        return existing

    def fake_update_knowledge_by_id(id, form_data):
        captured["id"] = id
        captured["meta"] = form_data.meta
        return SimpleNamespace(
            id=id,
            name=form_data.name or existing.name,
            description=form_data.description or existing.description,
            meta=form_data.meta,
            user_id="user-42",
            access_grants=[],
            created_at=1,
            updated_at=2,
            model_dump=lambda: {
                "id": id,
                "name": form_data.name or existing.name,
                "description": form_data.description or existing.description,
                "meta": form_data.meta,
                "user_id": "user-42",
                "access_grants": [],
                "created_at": 1,
                "updated_at": 2,
            },
        )

    monkeypatch.setattr(
        knowledge_mod.Knowledges,
        "get_knowledge_by_id",
        fake_get_knowledge_by_id,
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod.Knowledges,
        "update_knowledge_by_id",
        fake_update_knowledge_by_id,
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod.Knowledges,
        "get_file_metadatas_by_id",
        lambda *args, **kwargs: [],
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod, "filter_allowed_access_grants", lambda *args, **kwargs: []
    )
    monkeypatch.setattr(
        knowledge_mod.AccessGrants, "has_access", lambda *args, **kwargs: False
    )
    monkeypatch.setattr(
        knowledge_mod, "embed_knowledge_base_metadata", fake_embed, raising=False
    )

    fake_request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(config=SimpleNamespace(USER_PERMISSIONS={}))
        )
    )
    fake_user = SimpleNamespace(id="user-42", role="user")
    meta = {
        "source": "zotero",
        "zotero_top_collection_key": "WXYZ9876",
        "zotero_top_collection_name": "Physics",
    }
    form_data = KnowledgeUpdateForm(meta=meta, access_grants=[])

    result = await knowledge_mod.update_knowledge_by_id(
        request=fake_request,
        id="kb-zotero-update",
        form_data=form_data,
        user=fake_user,
    )

    assert captured["id"] == "kb-zotero-update"
    assert captured["meta"] == meta
    assert result.meta == meta
    assert result.user_id == "user-42"
    assert result.name == "Before"
    assert result.description == "Before update"
