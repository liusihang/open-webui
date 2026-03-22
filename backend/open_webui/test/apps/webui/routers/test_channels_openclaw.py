from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from open_webui.models.channels import ChannelModel
from open_webui.models import messages as messages_mod
from open_webui.models.messages import MessageTable
from open_webui.routers import channels as channels_mod


def _openclaw_route():
    return next(
        route
        for route in channels_mod.router.routes
        if getattr(route, "path", None) == "/openclaw/me" and "GET" in route.methods
    )


def _fake_request():
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    ENABLE_CHANNELS=True,
                    USER_PERMISSIONS={"features": {"channels": True}},
                )
            )
        )
    )


def _fake_user(user_id: str = "2"):
    return SimpleNamespace(id=user_id, role="user")


def _fake_openclaw_channel(user_id: str, channel_id: str | None = None):
    resource_id = channel_id or f"openclaw-{user_id}"
    return ChannelModel(
        id=resource_id,
        user_id=user_id,
        type="openclaw",
        name="openclaw",
        description=None,
        is_private=None,
        data={"openclaw": {"user_id": user_id}},
        meta={"openclaw": {"user_id": user_id}},
        access_grants=[
            {
                "id": f"grant-{user_id}-read",
                "resource_type": "channel",
                "resource_id": resource_id,
                "principal_type": "user",
                "principal_id": user_id,
                "permission": "read",
                "created_at": 1,
            },
            {
                "id": f"grant-{user_id}-write",
                "resource_type": "channel",
                "resource_id": resource_id,
                "principal_type": "user",
                "principal_id": user_id,
                "permission": "write",
                "created_at": 1,
            },
        ],
        created_at=1,
        updated_at=1,
        updated_by=None,
        archived_at=None,
        archived_by=None,
        deleted_at=None,
        deleted_by=None,
    )


@pytest.mark.asyncio
async def test_openclaw_me_returns_stable_channel_for_user(monkeypatch):
    route = _openclaw_route()

    seen_user_ids: list[str] = []

    async def fake_enter_room_for_users(*args, **kwargs):
        return None

    async def fake_emit_to_users(*args, **kwargs):
        return None

    def fake_get_or_create_openclaw_channel(user_id, db=None):
        seen_user_ids.append(user_id)
        return _fake_openclaw_channel(user_id)

    monkeypatch.setattr(channels_mod, "check_channels_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(channels_mod, "enter_room_for_users", fake_enter_room_for_users)
    monkeypatch.setattr(channels_mod, "emit_to_users", fake_emit_to_users)
    monkeypatch.setattr(
        channels_mod.Channels,
        "get_or_create_openclaw_channel",
        fake_get_or_create_openclaw_channel,
        raising=False,
    )

    response_1 = await route.endpoint(
        request=_fake_request(), user=_fake_user("2"), db=SimpleNamespace()
    )
    response_2 = await route.endpoint(
        request=_fake_request(), user=_fake_user("2"), db=SimpleNamespace()
    )

    assert isinstance(response_1, ChannelModel)
    assert isinstance(response_2, ChannelModel)
    assert response_1.id == response_2.id == "openclaw-2"
    assert response_1.user_id == response_2.user_id == "2"
    assert response_1.type == response_2.type == "openclaw"
    assert seen_user_ids == ["2", "2"]
    assert [
        {
            key: grant.model_dump()[key]
            for key in ["principal_type", "principal_id", "permission"]
        }
        for grant in response_1.access_grants
    ] == [
        {
            "principal_type": "user",
            "principal_id": "2",
            "permission": "read",
        },
        {
            "principal_type": "user",
            "principal_id": "2",
            "permission": "write",
        },
    ]
    assert [
        {
            key: grant.model_dump()[key]
            for key in ["principal_type", "principal_id", "permission"]
        }
        for grant in response_2.access_grants
    ] == [
        {
            "principal_type": "user",
            "principal_id": "2",
            "permission": "read",
        },
        {
            "principal_type": "user",
            "principal_id": "2",
            "permission": "write",
        },
    ]


def test_openclaw_message_serialization_uses_integration_identity(monkeypatch):
    message = SimpleNamespace(
        id="message-1",
        user_id="user-1",
        channel_id="channel-1",
        reply_to_id=None,
        parent_id=None,
        is_pinned=False,
        pinned_at=None,
        pinned_by=None,
        content="hello from openclaw",
        data=None,
        meta={
            "openclaw": {
                "id": "openclaw",
                "name": "OpenClaw",
                "role": "integration",
            }
        },
        created_at=1,
        updated_at=1,
    )

    class FakeDb:
        def get(self, model, id):
            if id == "message-1":
                return message
            return None

    fake_user = SimpleNamespace(id="user-1", name="Real User", role="user")

    @contextmanager
    def fake_get_db_context(db=None):
        yield FakeDb()

    monkeypatch.setattr(messages_mod, "get_db_context", fake_get_db_context)
    monkeypatch.setattr(messages_mod.Users, "get_user_by_id", lambda user_id, db=None: fake_user)

    table = MessageTable()
    monkeypatch.setattr(
        table,
        "get_reactions_by_message_id",
        lambda *args, **kwargs: [],
        raising=False,
    )
    monkeypatch.setattr(
        table,
        "get_thread_replies_by_message_id",
        lambda *args, **kwargs: [],
        raising=False,
    )

    response = table.get_message_by_id("message-1", db=SimpleNamespace())

    assert response is not None
    assert response.user is not None
    assert response.user.id == "openclaw"
    assert response.user.name == "OpenClaw"
    assert response.user.role == "integration"
    assert response.user != fake_user
