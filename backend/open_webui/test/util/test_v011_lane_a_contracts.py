from pathlib import Path
from types import SimpleNamespace

from open_webui import config as app_config
from open_webui.routers import auths
from open_webui.utils.headers import parse_custom_headers
from sqlalchemy.exc import IntegrityError

BACKEND_ROOT = Path(__file__).resolve().parents[3] / 'open_webui'


def test_trusted_header_signup_race_catches_sqlalchemy_integrity_error():
    assert auths.IntegrityError is IntegrityError


def test_custom_header_group_placeholders_are_substituted():
    user = SimpleNamespace(id='user-1', name=' Test User ', email=' test@example.com ', role='user')
    groups = [
        SimpleNamespace(id='group-1', name=' Research '),
        SimpleNamespace(id='group-2', name='Reviewers'),
    ]

    headers = parse_custom_headers(
        {
            'X-User-Groups': '{{USER_GROUPS}}',
            'X-User-Group-Ids': '{{USER_GROUP_IDS}}',
        },
        user=user,
        user_groups=groups,
    )

    assert headers == {
        'X-User-Groups': 'Research,Reviewers',
        'X-User-Group-Ids': 'group-1,group-2',
    }


def test_official_subagent_config_surface_is_excluded():
    official_names = {
        'ENABLE_SUBAGENTS',
        'SUBAGENTS_BACKGROUND_ENABLED',
        'SUBAGENTS_MAX_CONCURRENT',
        'SUBAGENTS_MAX_ASYNC',
        'SUBAGENTS_MAX_ITERATIONS',
        'SUBAGENTS_MAX_OUTPUT',
        'SUBAGENTS_SYSTEM_PROMPT',
    }

    assert official_names.isdisjoint(vars(app_config))
    assert not any(key.startswith('subagents.') for key in app_config.DEFAULT_CONFIG)
    configs_source = (BACKEND_ROOT / 'routers' / 'configs.py').read_text()
    assert "@router.get('/subagents'" not in configs_source
    assert "@router.post('/subagents'" not in configs_source
