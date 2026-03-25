from types import SimpleNamespace

import pytest

from open_webui.routers import knowledge as knowledge_mod


@pytest.mark.asyncio
async def test_knowledge_create_defaults_to_private_owner_scope(monkeypatch):
    """
    Plugin contract guard:
    creating a knowledge base without explicit access_grants must keep it private
    and owned by the authenticated user.
    """
    captured: dict = {}

    async def fake_embed(*args, **kwargs):
        return True

    def fake_insert_new_knowledge(user_id, form_data):
        captured["user_id"] = user_id
        captured["access_grants"] = form_data.access_grants
        return SimpleNamespace(
            id="kb-zotero-1",
            name=form_data.name,
            description=form_data.description,
            user_id=user_id,
            access_grants=[],
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
    form_data = knowledge_mod.KnowledgeForm(
        name="Zotero / ML",
        description="synced",
        access_grants=None,
    )

    result = await knowledge_mod.create_new_knowledge(
        request=fake_request, form_data=form_data, user=fake_user
    )

    assert captured["user_id"] == "user-42"
    assert captured["access_grants"] is None
    assert result.user_id == "user-42"
    assert result.access_grants == []
