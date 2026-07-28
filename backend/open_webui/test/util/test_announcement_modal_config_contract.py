from pathlib import Path

import pytest
from open_webui.routers import auths
from pydantic import ValidationError

OPEN_WEBUI_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = OPEN_WEBUI_DIR / 'config.py'
MAIN_PATH = OPEN_WEBUI_DIR / 'main.py'

ANNOUNCEMENT_FIELDS = {
    'ANNOUNCEMENT_MODAL_ENABLED': 'ui.announcement_modal.enabled',
    'ANNOUNCEMENT_MODAL_KEY': 'ui.announcement_modal.key',
    'ANNOUNCEMENT_MODAL_TITLE': 'ui.announcement_modal.title',
    'ANNOUNCEMENT_MODAL_CONTENT': 'ui.announcement_modal.content',
}


def _admin_config_payload() -> dict:
    return {
        'SHOW_ADMIN_DETAILS': False,
        'ADMIN_EMAIL': None,
        'WEBUI_URL': '',
        'ENABLE_SIGNUP': False,
        'ENABLE_API_KEYS': True,
        'ENABLE_API_KEYS_ENDPOINT_RESTRICTIONS': False,
        'API_KEYS_ALLOWED_ENDPOINTS': '',
        'DEFAULT_USER_ROLE': 'user',
        'DEFAULT_GROUP_ID': '',
        'JWT_EXPIRES_IN': '-1',
        'ENABLE_COMMUNITY_SHARING': False,
        'ENABLE_MESSAGE_RATING': True,
        'ENABLE_FOLDERS': True,
        'FOLDER_MAX_FILE_COUNT': None,
        'AUTOMATION_MAX_COUNT': None,
        'AUTOMATION_MIN_INTERVAL': None,
        'ENABLE_AUTOMATIONS': False,
        'ENABLE_CHANNELS': False,
        'ENABLE_CALENDAR': False,
        'ENABLE_MEMORIES': True,
        'ENABLE_MEMORY_SYSTEM_CONTEXT': True,
        'ENABLE_NOTES': True,
        'ENABLE_USER_WEBHOOKS': False,
        'ENABLE_USER_STATUS': True,
        'PENDING_USER_OVERLAY_TITLE': None,
        'PENDING_USER_OVERLAY_CONTENT': None,
        'RESPONSE_WATERMARK': None,
    }


def test_admin_config_exposes_announcement_fields_for_protected_read_write() -> None:
    assert {field: auths.ADMIN_CONFIG_KEYS[field] for field in ANNOUNCEMENT_FIELDS} == ANNOUNCEMENT_FIELDS
    assert set(ANNOUNCEMENT_FIELDS).issubset(auths.AdminConfig.model_fields)


def test_legacy_admin_submission_does_not_overwrite_announcement() -> None:
    form = auths.AdminConfig.model_validate(_admin_config_payload())

    updates = auths.admin_config_updates(form)

    assert not set(ANNOUNCEMENT_FIELDS.values()).intersection(updates)


def test_current_admin_submission_updates_complete_announcement() -> None:
    form = auths.AdminConfig.model_validate(
        {
            **_admin_config_payload(),
            'ANNOUNCEMENT_MODAL_ENABLED': True,
            'ANNOUNCEMENT_MODAL_KEY': '2026-07-release',
            'ANNOUNCEMENT_MODAL_TITLE': 'What changed',
            'ANNOUNCEMENT_MODAL_CONTENT': 'Release notes',
        }
    )

    updates = auths.admin_config_updates(form)

    assert {key: updates[key] for key in ANNOUNCEMENT_FIELDS.values()} == {
        'ui.announcement_modal.enabled': True,
        'ui.announcement_modal.key': '2026-07-release',
        'ui.announcement_modal.title': 'What changed',
        'ui.announcement_modal.content': 'Release notes',
    }


@pytest.mark.parametrize(
    ('field', 'value', 'message'),
    [
        ('ANNOUNCEMENT_MODAL_KEY', '   ', 'version key is required'),
        ('ANNOUNCEMENT_MODAL_CONTENT', '\n', 'content is required'),
    ],
)
def test_enabled_announcement_requires_displayable_fields(field, value, message) -> None:
    payload = {
        **_admin_config_payload(),
        'ANNOUNCEMENT_MODAL_ENABLED': True,
        'ANNOUNCEMENT_MODAL_KEY': '2026-07-release',
        'ANNOUNCEMENT_MODAL_CONTENT': 'Release notes',
        field: value,
    }

    with pytest.raises(ValidationError, match=message):
        auths.AdminConfig.model_validate(payload)


def test_announcement_defaults_are_registered_disabled_and_empty() -> None:
    config_text = CONFIG_PATH.read_text()

    assert (
        "ANNOUNCEMENT_MODAL_ENABLED = os.getenv('ANNOUNCEMENT_MODAL_ENABLED', 'False').lower() == 'true'" in config_text
    )
    assert "ANNOUNCEMENT_MODAL_KEY = os.getenv('ANNOUNCEMENT_MODAL_KEY', '')" in config_text
    assert "ANNOUNCEMENT_MODAL_TITLE = os.getenv('ANNOUNCEMENT_MODAL_TITLE', '')" in config_text
    assert "ANNOUNCEMENT_MODAL_CONTENT = os.getenv('ANNOUNCEMENT_MODAL_CONTENT', '')" in config_text

    for field, storage_key in ANNOUNCEMENT_FIELDS.items():
        assert f"'{storage_key}': {field}" in config_text


def test_authenticated_app_config_projects_complete_announcement() -> None:
    main_text = MAIN_PATH.read_text()

    for storage_key in ANNOUNCEMENT_FIELDS.values():
        assert f"'{storage_key}'," in main_text

    assert "'announcement_modal': {" in main_text
    assert "'enabled': config.get('ui.announcement_modal.enabled')" in main_text
    assert "'key': config.get('ui.announcement_modal.key')" in main_text
    assert "'title': config.get('ui.announcement_modal.title')" in main_text
    assert "'content': config.get('ui.announcement_modal.content')" in main_text
