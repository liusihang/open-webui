from open_webui.routers import chats


def test_chat_config_exposes_global_system_prompt_storage_key():
    assert chats.CHAT_CONFIG_KEYS['GLOBAL_SYSTEM_PROMPT'] == 'chat.global_system_prompt'


def test_chat_config_form_accepts_global_system_prompt():
    form = chats.ChatConfigForm(
        ENABLE_CONTEXT_COMPACTION=False,
        CONTEXT_COMPACTION_TOKEN_THRESHOLD=80000,
        CONTEXT_COMPACTION_PROMPT_TEMPLATE='',
        GLOBAL_SYSTEM_PROMPT='Always report public tool progress.',
    )

    assert form.model_dump()['GLOBAL_SYSTEM_PROMPT'] == 'Always report public tool progress.'


def test_chat_config_updates_persists_empty_global_system_prompt():
    updates = chats.chat_config_updates({'GLOBAL_SYSTEM_PROMPT': ''})

    assert updates == {'chat.global_system_prompt': ''}


def test_legacy_chat_config_submission_does_not_overwrite_global_system_prompt():
    form = chats.ChatConfigForm(
        ENABLE_CONTEXT_COMPACTION=False,
        CONTEXT_COMPACTION_TOKEN_THRESHOLD=80000,
        CONTEXT_COMPACTION_PROMPT_TEMPLATE='',
    )

    assert chats.chat_config_updates(form.model_dump()) == {
        'chat.context_compaction.enable': False,
        'chat.context_compaction.token_threshold': 80000,
        'chat.context_compaction.prompt_template': '',
    }
