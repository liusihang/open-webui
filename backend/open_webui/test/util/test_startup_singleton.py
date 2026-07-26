import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_startup_singleton_lock_allows_only_one_holder(tmp_path):
    from open_webui.utils.startup_singleton import StartupSingletonLock

    first = StartupSingletonLock('background-worker', lock_dir=tmp_path, blocking=False)
    second = StartupSingletonLock('background-worker', lock_dir=tmp_path, blocking=False)

    try:
        assert first.acquire() is True
        assert second.acquire() is False
    finally:
        first.release()
        second.release()

    try:
        assert second.acquire() is True
    finally:
        second.release()


def test_import_time_migrations_are_guarded_by_startup_singleton():
    config_source = Path('backend/open_webui/config.py').read_text()

    assert 'run_migrations_once()' in config_source
    assert 'startup_singleton_lock(' in config_source


def test_lifespan_singleton_tasks_are_guarded_by_startup_singleton():
    main_source = Path('backend/open_webui/main.py').read_text()

    assert '_run_singleton_startup_tasks(app)' in main_source
    assert 'startup_singleton_lock(' in main_source
    assert 'startup_singleton_lock.release()' in main_source


def test_lifespan_dependency_install_is_guarded_by_run_once():
    main_source = Path('backend/open_webui/main.py').read_text()

    assert '_install_tool_and_function_dependencies_once()' in main_source
    assert 'run_startup_once(' in main_source


def test_expired_temporary_mode_profile_cleanup_is_only_registered_in_singleton_startup_path():
    main_source = Path('backend/open_webui/main.py').read_text()
    singleton_start = main_source.index('async def _run_singleton_startup_tasks')
    singleton_end = main_source.index('async def _install_tool_and_function_dependencies_once')
    singleton_source = main_source[singleton_start:singleton_end]

    assert 'ConversationModeProfiles.cleanup_expired_temporary_bindings()' in singleton_source
    assert main_source.count('cleanup_expired_temporary_bindings()') == 1


@pytest.mark.asyncio
async def test_singleton_startup_invokes_expired_temporary_mode_profile_cleanup(monkeypatch):
    main = importlib.import_module('open_webui.main')

    calls = []

    async def cleanup():
        calls.append('cleanup')
        return 1

    def discard_task(coroutine):
        coroutine.close()
        future = asyncio.get_running_loop().create_future()
        future.set_result(None)
        return future

    async def config_get(key, default=None):
        return default

    monkeypatch.setattr(main.ConversationModeProfiles, 'cleanup_expired_temporary_bindings', cleanup)
    monkeypatch.setattr(main.asyncio, 'create_task', discard_task)
    monkeypatch.setattr(main.Config, 'get', config_get)

    await main._run_singleton_startup_tasks(SimpleNamespace())

    assert calls == ['cleanup']


@pytest.mark.asyncio
async def test_run_startup_once_skips_after_success_marker(tmp_path):
    from open_webui.utils.startup_singleton import run_startup_once

    calls = []

    async def callback():
        calls.append('ran')
        return 'result'

    first_ran, first_result = await run_startup_once('dependency-install', callback, lock_dir=tmp_path)
    second_ran, second_result = await run_startup_once('dependency-install', callback, lock_dir=tmp_path)

    assert first_ran is True
    assert first_result == 'result'
    assert second_ran is False
    assert second_result is None
    assert calls == ['ran']
