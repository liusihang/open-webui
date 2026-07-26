from types import SimpleNamespace

import pytest
from open_webui.socket import main as socket_main
from open_webui.models.automations import AutomationModel
from open_webui.models.chats import ChatModel
from open_webui.utils import access_control, automations


@pytest.mark.asyncio
async def test_automation_creates_bound_chat_profile_before_completion(monkeypatch) -> None:
    events = []
    automation = AutomationModel(
        id='automation-1',
        user_id='user-1',
        name='Scheduled task',
        data={'prompt': 'Run the report', 'model_id': 'model-1', 'rrule': 'FREQ=DAILY'},
        is_active=True,
        created_at=1,
        updated_at=1,
    )
    user = SimpleNamespace(id='user-1', role='user')
    bound_chat = ChatModel(
        id='automation-chat',
        user_id=user.id,
        title=automation.name,
        chat={'title': automation.name, 'mode': 'chat', 'history': {'currentId': None, 'messages': {}}},
        created_at=1,
        updated_at=1,
        mode_profile_revision_id='chat-current-revision',
    )

    async def get_user(user_id):
        return user

    async def config_get(key, default=None):
        if key == 'user.permissions':
            return {}
        return default

    async def permitted(*args, **kwargs):
        return True

    async def render_prompt(prompt, owner):
        return prompt

    async def insert_bound_chat(app, *, mode, revision_hint, chat_id, user_id, form_data, **kwargs):
        assert mode == 'chat'
        assert revision_hint is None
        assert user_id == user.id
        assert form_data.chat['mode'] == 'chat'
        events.append('bound')
        return SimpleNamespace(chat=bound_chat)

    async def no_op(*args, **kwargs):
        return None

    async def completion(request, form_data, *, user):
        assert form_data['chat_id'] == bound_chat.id
        assert events == ['bound']
        events.append('completion')

    monkeypatch.setattr(automations.Users, 'get_user_by_id', get_user)
    monkeypatch.setattr(automations.Config, 'get', config_get)
    monkeypatch.setattr(access_control, 'has_permission', permitted)
    monkeypatch.setattr(automations, 'prompt_template', render_prompt)
    monkeypatch.setattr(automations, 'insert_new_chat_with_current_mode_profile', insert_bound_chat, raising=False)
    monkeypatch.setattr(automations, '_record_run', no_op)
    monkeypatch.setattr(automations, 'publish_event', no_op)
    monkeypatch.setattr(socket_main.sio, 'emit', no_op)
    monkeypatch.setattr(automations, 'create_token', lambda **kwargs: 'token')
    monkeypatch.setattr(automations, '_build_request', lambda app, token: SimpleNamespace())

    app = SimpleNamespace(
        state=SimpleNamespace(
            MODELS={'model-1': {}},
            CHAT_COMPLETION_HANDLER=completion,
        )
    )

    await automations.execute_automation(app, automation)

    assert events == ['bound', 'completion']
