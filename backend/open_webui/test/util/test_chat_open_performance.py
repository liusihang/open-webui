import os
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")

from open_webui.internal.db import Base
from open_webui.models.chats import Chat, ChatForm, Chats
from open_webui.models.tags import Tag, Tags
from open_webui.routers.chats import get_chat_tags_by_id


@contextmanager
def _yield_session(session):
    yield session


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Chat.__table__, Tag.__table__])
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    return session_factory()


def _seed_chat_and_tags(session):
    user_id = "user-1"
    tag_alpha = Tags.insert_new_tag("Alpha", user_id, db=session)
    tag_beta = Tags.insert_new_tag("Beta", user_id, db=session)

    chat = Chats.insert_new_chat(
        user_id,
        ChatForm(
            chat={
                "title": "Large Chat",
                "history": {
                    "currentId": "m2",
                    "messages": {
                        "m1": {
                            "id": "m1",
                            "role": "user",
                            "content": "hello",
                            "parentId": None,
                            "childrenIds": ["m2"],
                        },
                        "m2": {
                            "id": "m2",
                            "role": "assistant",
                            "content": "x" * 10_000,
                            "sources": [
                                {
                                    "source": {"id": "s1", "name": "Source 1"},
                                    "document": ["doc " * 1000],
                                    "metadata": [{"source": "s1", "name": "Source 1"}],
                                }
                            ],
                            "parentId": "m1",
                            "childrenIds": [],
                        },
                    },
                },
            },
            folder_id=None,
        ),
        db=session,
    )

    chat_row = session.get(Chat, chat.id)
    chat_row.meta = {"tags": [tag_alpha.id, tag_beta.id]}
    session.commit()
    session.refresh(chat_row)
    return chat, user_id, [tag_alpha.id, tag_beta.id]


def test_get_chat_tag_ids_returns_meta_tags_without_full_chat_load(monkeypatch):
    session = _make_session()

    import open_webui.models.chats as chats_mod
    import open_webui.models.tags as tags_mod

    monkeypatch.setattr(chats_mod, "get_db_context", lambda db=None: _yield_session(session))
    monkeypatch.setattr(tags_mod, "get_db_context", lambda db=None: _yield_session(session))
    chat, user_id, expected_tag_ids = _seed_chat_and_tags(session)

    original_query = session.query

    def guarded_query(*entities, **kwargs):
        assert Chat.chat not in entities
        return original_query(*entities, **kwargs)

    monkeypatch.setattr(session, "query", guarded_query)

    assert Chats.get_chat_tag_ids(chat.id, user_id, db=session) == expected_tag_ids


def test_is_chat_owner_uses_exists_without_loading_full_chat(monkeypatch):
    session = _make_session()

    import open_webui.models.chats as chats_mod
    import open_webui.models.tags as tags_mod

    monkeypatch.setattr(chats_mod, "get_db_context", lambda db=None: _yield_session(session))
    monkeypatch.setattr(tags_mod, "get_db_context", lambda db=None: _yield_session(session))
    chat, user_id, _ = _seed_chat_and_tags(session)

    def fail_if_full_row_loaded(*args, **kwargs):
        raise AssertionError("full chat row should not be loaded for owner checks")

    monkeypatch.setattr(session, "get", fail_if_full_row_loaded)

    assert Chats.is_chat_owner(chat.id, user_id, db=session) is True
    assert Chats.is_chat_owner(chat.id, "other-user", db=session) is False


@pytest.mark.asyncio
async def test_get_chat_tags_route_uses_lightweight_tag_lookup(monkeypatch):
    session = _make_session()

    import open_webui.models.chats as chats_mod
    import open_webui.models.tags as tags_mod

    monkeypatch.setattr(chats_mod, "get_db_context", lambda db=None: _yield_session(session))
    monkeypatch.setattr(tags_mod, "get_db_context", lambda db=None: _yield_session(session))
    chat, user_id, expected_tag_ids = _seed_chat_and_tags(session)

    original_get_chat_by_id_and_user_id = Chats.get_chat_by_id_and_user_id

    def fail_if_full_chat_loaded(*args, **kwargs):
        raise AssertionError("full chat lookup should not be used for tag reads")

    monkeypatch.setattr(Chats, "get_chat_by_id_and_user_id", fail_if_full_chat_loaded)

    response = await get_chat_tags_by_id(
        chat.id,
        user=type("User", (), {"id": user_id})(),
        db=session,
    )

    assert [tag.id for tag in response] == expected_tag_ids

    monkeypatch.setattr(Chats, "get_chat_by_id_and_user_id", original_get_chat_by_id_and_user_id)


def test_get_chat_by_id_skips_read_time_sanitization(monkeypatch):
    session = _make_session()

    import open_webui.models.chats as chats_mod
    import open_webui.models.tags as tags_mod

    monkeypatch.setattr(chats_mod, "get_db_context", lambda db=None: _yield_session(session))
    monkeypatch.setattr(tags_mod, "get_db_context", lambda db=None: _yield_session(session))
    chat, _, _ = _seed_chat_and_tags(session)

    original_sanitize = Chats._sanitize_chat_row

    def fail_if_read_time_sanitize_runs(*args, **kwargs):
        raise AssertionError("read path should not sanitize the full chat payload")

    monkeypatch.setattr(Chats, "_sanitize_chat_row", fail_if_read_time_sanitize_runs)

    result = Chats.get_chat_by_id(chat.id, db=session)

    assert result is not None
    assert result.id == chat.id

    monkeypatch.setattr(Chats, "_sanitize_chat_row", original_sanitize)
