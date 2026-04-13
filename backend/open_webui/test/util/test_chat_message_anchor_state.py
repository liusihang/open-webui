import os
import sys
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))

from open_webui.internal.db import Base
from open_webui.models.chats import Chat
from open_webui.models.chat_messages import ChatMessage, ChatMessages


@contextmanager
def _yield_session(session):
    yield session


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Chat.__table__, ChatMessage.__table__])
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    return session_factory()


def _seed_chat(session, chat_id: str = "chat-1", user_id: str = "user-1") -> None:
    session.add(
        Chat(
            id=chat_id,
            user_id=user_id,
            title="Anchor Chat",
            chat={"history": {"messages": {}, "currentId": None}},
            created_at=1,
            updated_at=1,
        )
    )
    session.commit()


def test_upsert_message_round_trips_anchor_metadata(monkeypatch):
    session = _make_session()
    _seed_chat(session)

    import open_webui.models.chat_messages as chat_messages_mod

    monkeypatch.setattr(chat_messages_mod, "get_db_context", lambda db=None: _yield_session(session))

    message = ChatMessages.upsert_message(
        message_id="m-assistant-1",
        chat_id="chat-1",
        user_id="user-1",
        data={
            "role": "assistant",
            "content": "PING",
            "provider_response_id": "resp_123",
            "provider_route": "responses",
            "anchor_valid": True,
            "anchor_model_id": "openai/gpt-5.4-mini",
        },
        db=session,
    )

    assert message is not None
    assert message.provider_response_id == "resp_123"
    assert message.provider_route == "responses"
    assert message.anchor_valid is True
    assert message.anchor_model_id == "openai/gpt-5.4-mini"

    loaded = ChatMessages.get_message_by_id("chat-1-m-assistant-1", db=session)
    assert loaded is not None
    assert loaded.provider_response_id == "resp_123"
    assert loaded.provider_route == "responses"
    assert loaded.anchor_valid is True
    assert loaded.anchor_model_id == "openai/gpt-5.4-mini"
