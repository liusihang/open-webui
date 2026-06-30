from pathlib import Path


def test_startup_singleton_lock_allows_only_one_holder(tmp_path):
    from open_webui.utils.startup_singleton import StartupSingletonLock

    first = StartupSingletonLock("background-worker", lock_dir=tmp_path, blocking=False)
    second = StartupSingletonLock("background-worker", lock_dir=tmp_path, blocking=False)

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
    config_source = Path("backend/open_webui/config.py").read_text()

    assert "run_migrations_once()" in config_source
    assert "startup_singleton_lock(" in config_source


def test_lifespan_singleton_tasks_are_guarded_by_startup_singleton():
    main_source = Path("backend/open_webui/main.py").read_text()

    assert "_run_singleton_startup_tasks(app)" in main_source
    assert "startup_singleton_lock(" in main_source
    assert "startup_singleton_lock.release()" in main_source
