import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")

from open_webui.internal.db import Base
from open_webui.models.access_grants import AccessGrant
from open_webui.models.channels import Channel, Channels


@contextmanager
def _yield_session(session):
    yield session


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[Channel.__table__, AccessGrant.__table__],
    )
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    return session_factory()


def test_get_or_create_openclaw_channel_is_stable_and_owner_only(monkeypatch):
    session = _make_session()

    import open_webui.models.access_grants as access_grants_mod
    import open_webui.models.channels as channels_mod

    monkeypatch.setattr(channels_mod, "get_db_context", lambda db=None: _yield_session(session))
    monkeypatch.setattr(
        access_grants_mod, "get_db_context", lambda db=None: _yield_session(session)
    )

    first = Channels.get_or_create_openclaw_channel("user-1", db=session)
    second = Channels.get_or_create_openclaw_channel("user-1", db=session)

    assert first.id == second.id
    assert first.type == "openclaw"
    assert first.user_id == "user-1"

    grants = (
        session.query(AccessGrant)
        .filter_by(resource_type="channel", resource_id=first.id)
        .all()
    )
    assert len(grants) == 2
    assert {
        (grant.principal_type, grant.principal_id, grant.permission)
        for grant in grants
    } == {
        ("user", "user-1", "read"),
        ("user", "user-1", "write"),
    }
