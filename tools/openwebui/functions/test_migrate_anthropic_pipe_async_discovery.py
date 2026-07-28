import argparse
import hashlib
import importlib.util
import stat
from pathlib import Path

import pytest

MIGRATION_PATH = Path(__file__).with_name('migrate_anthropic_pipe_async_discovery.py')


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        'migrate_anthropic_pipe_async_discovery',
        MIGRATION_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source() -> str:
    return '''"""
title: Anthropic API Integration
requirements: pydantic>=2.0.0, anthropic>=0.75.0
"""

import requests


class Pipe:
    async def get_anthropic_models(self):
        model_url = "http://models.test/models"
        headers = {"Accept": "application/json"}
        response = requests.get(model_url, headers=headers, timeout=15)
        response.raise_for_status()
        payload = response.json()
        return payload
'''


def test_patch_source_replaces_blocking_discovery_with_native_async_http() -> None:
    migration = _load_migration_module()
    source = _source()

    patched = migration.patch_source(
        source,
        expected_md5=hashlib.md5(source.encode()).hexdigest(),
    )

    assert 'requirements: pydantic>=2.0.0, anthropic>=0.75.0, httpx>=0.27.0' in patched
    assert 'import httpx' in patched
    assert 'import requests' not in patched
    assert 'requests.get' not in patched
    assert 'async with httpx.AsyncClient(' in patched
    assert 'timeout=15' in patched
    assert 'trust_env=True' in patched
    assert 'follow_redirects=True' in patched
    assert 'response = await client.get(model_url, headers=headers)' in patched
    compile(patched, '<patched-anthropic-pipe>', 'exec')


def test_patch_source_rejects_an_unexpected_source_hash() -> None:
    migration = _load_migration_module()

    with pytest.raises(migration.MigrationError, match='source hash mismatch'):
        migration.patch_source(_source(), expected_md5='0' * 32)


@pytest.mark.parametrize(
    'old,new,missing_name',
    [
        ('import requests\n', '', 'requests import'),
        (
            'response = requests.get(model_url, headers=headers, timeout=15)\n',
            '',
            'blocking model request',
        ),
        (
            'requirements: pydantic>=2.0.0, anthropic>=0.75.0\n',
            '',
            'requirements declaration',
        ),
    ],
)
def test_patch_source_refuses_partial_or_different_plugin_versions(
    old: str,
    new: str,
    missing_name: str,
) -> None:
    migration = _load_migration_module()
    source = _source().replace(old, new)

    with pytest.raises(migration.MigrationError, match=missing_name):
        migration.patch_source(source)


def test_patch_source_is_not_silently_reapplied() -> None:
    migration = _load_migration_module()
    patched = migration.patch_source(_source())

    with pytest.raises(migration.MigrationError, match='requirements declaration'):
        migration.patch_source(patched)


def _function(source: str | None = None) -> dict:
    return {
        'id': 'anthropic_pipe',
        'name': 'Anthropic',
        'content': _source() if source is None else source,
        'meta': {'description': 'fixture'},
        'updated_at': 123,
    }


def _args(tmp_path: Path, **overrides) -> argparse.Namespace:
    values = {
        'base_url': 'http://openwebui.test',
        'function_id': 'anthropic_pipe',
        'expected_md5': hashlib.md5(_source().encode()).hexdigest(),
        'backup': str(tmp_path / 'anthropic-backup.json'),
        'apply': True,
        'confirm_exclusive_maintenance': True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_redirect_handler_refuses_to_forward_the_request() -> None:
    migration = _load_migration_module()

    assert (
        migration._RejectRedirects().redirect_request(
            object(),
            None,
            302,
            'Found',
            {},
            'https://redirect.test/functions',
        )
        is None
    )


def test_apply_requires_an_explicit_exclusive_maintenance_window(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    migration = _load_migration_module()
    monkeypatch.setenv('OPENWEBUI_TOKEN', 'secret')

    with pytest.raises(migration.MigrationError, match='exclusive maintenance'):
        migration.migrate(
            _args(tmp_path, confirm_exclusive_maintenance=False),
        )


def test_apply_uses_a_private_durable_backup_and_verifies_the_update(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    migration = _load_migration_module()
    current = _function()
    patched = migration.patch_source(current['content'])
    calls = []
    responses = [current, current, current, _function(patched)]

    def request_json(method, url, token, payload=None):
        calls.append((method, url, token, payload))
        return responses.pop(0)

    monkeypatch.setenv('OPENWEBUI_TOKEN', 'secret')
    monkeypatch.setattr(migration, '_request_json', request_json)

    result = migration.migrate(_args(tmp_path))

    backup = Path(result['backup'])
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert result['verified_readback'] is True
    assert [call[0] for call in calls] == ['GET', 'GET', 'POST', 'GET']
    assert calls[2][3]['content'] == patched


def test_apply_refuses_source_drift_during_the_maintenance_preflight(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    migration = _load_migration_module()
    responses = [_function(), _function(_source() + '\n# concurrent edit\n')]
    calls = []

    def request_json(method, url, token, payload=None):
        calls.append(method)
        return responses.pop(0)

    monkeypatch.setenv('OPENWEBUI_TOKEN', 'secret')
    monkeypatch.setattr(migration, '_request_json', request_json)

    with pytest.raises(migration.MigrationError, match='changed during preflight'):
        migration.migrate(_args(tmp_path))

    assert calls == ['GET', 'GET']


def test_apply_reconciles_a_post_error_when_readback_is_already_patched(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    migration = _load_migration_module()
    current = _function()
    patched = migration.patch_source(current['content'])
    calls = []

    def request_json(method, url, token, payload=None):
        calls.append(method)
        if calls == ['GET'] or calls == ['GET', 'GET']:
            return current
        if method == 'POST':
            raise migration.MigrationError('request timed out')
        return _function(patched)

    monkeypatch.setenv('OPENWEBUI_TOKEN', 'secret')
    monkeypatch.setattr(migration, '_request_json', request_json)

    result = migration.migrate(_args(tmp_path))

    assert calls == ['GET', 'GET', 'POST', 'GET']
    assert result['verified_readback'] is True
    assert result['post_reconciled_after_error'] is True


def test_apply_reports_a_post_error_when_readback_remains_original(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    migration = _load_migration_module()
    current = _function()
    calls = []

    def request_json(method, url, token, payload=None):
        calls.append(method)
        if method == 'POST':
            raise migration.MigrationError('request timed out')
        return current

    monkeypatch.setenv('OPENWEBUI_TOKEN', 'secret')
    monkeypatch.setattr(migration, '_request_json', request_json)

    with pytest.raises(migration.MigrationError, match='update was not applied'):
        migration.migrate(_args(tmp_path))

    assert calls == ['GET', 'GET', 'POST', 'GET']


def test_apply_reports_an_unknown_readback_after_a_post_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    migration = _load_migration_module()
    current = _function()
    unknown = _function(_source() + '\n# concurrent replacement\n')
    calls = []

    def request_json(method, url, token, payload=None):
        calls.append(method)
        if method == 'POST':
            raise migration.MigrationError('request timed out')
        if len(calls) == 4:
            return unknown
        return current

    monkeypatch.setenv('OPENWEBUI_TOKEN', 'secret')
    monkeypatch.setattr(migration, '_request_json', request_json)

    with pytest.raises(migration.MigrationError, match='unknown post-update state after POST error'):
        migration.migrate(_args(tmp_path))

    assert calls == ['GET', 'GET', 'POST', 'GET']


def test_apply_reports_a_completed_post_that_left_original_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    migration = _load_migration_module()
    current = _function()
    calls = []

    def request_json(method, url, token, payload=None):
        calls.append(method)
        return current

    monkeypatch.setenv('OPENWEBUI_TOKEN', 'secret')
    monkeypatch.setattr(migration, '_request_json', request_json)

    with pytest.raises(migration.MigrationError, match='original source remains'):
        migration.migrate(_args(tmp_path))

    assert calls == ['GET', 'GET', 'POST', 'GET']


def test_apply_reports_an_unknown_post_state_without_overwriting_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    migration = _load_migration_module()
    current = _function()
    responses = [current, current, current, _function(_source() + '\n# unknown state\n')]

    def request_json(method, url, token, payload=None):
        return responses.pop(0)

    monkeypatch.setenv('OPENWEBUI_TOKEN', 'secret')
    monkeypatch.setattr(migration, '_request_json', request_json)

    with pytest.raises(migration.MigrationError, match='unknown post-update state'):
        migration.migrate(_args(tmp_path))


def test_apply_reports_unknown_state_when_verification_readback_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    migration = _load_migration_module()
    current = _function()
    calls = []

    def request_json(method, url, token, payload=None):
        calls.append(method)
        if method == 'GET' and len(calls) <= 2:
            return current
        if method == 'POST':
            return current
        raise migration.MigrationError('verification timed out')

    monkeypatch.setenv('OPENWEBUI_TOKEN', 'secret')
    monkeypatch.setattr(migration, '_request_json', request_json)

    with pytest.raises(migration.MigrationError, match='post-update state is unknown'):
        migration.migrate(_args(tmp_path))

    assert calls == ['GET', 'GET', 'POST', 'GET']
