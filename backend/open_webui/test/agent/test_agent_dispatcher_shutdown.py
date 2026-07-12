from __future__ import annotations

import asyncio
import importlib
import os
from contextlib import suppress

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret')
os.environ.setdefault('ENABLE_DB_MIGRATIONS', 'false')

import pytest

main = importlib.import_module('open_webui.main')


@pytest.mark.asyncio
async def test_cancel_and_await_background_task_finishes_async_cleanup() -> None:
    cancel_received = asyncio.Event()
    cleanup_finished = asyncio.Event()

    async def worker() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancel_received.set()
            raise
        finally:
            await asyncio.sleep(0)
            cleanup_finished.set()

    task = asyncio.create_task(worker())
    await asyncio.sleep(0)
    helper = getattr(main, '_cancel_and_await_task', None)
    try:
        assert helper is not None
        await helper(task)
        assert cancel_received.is_set()
        assert cleanup_finished.is_set()
        assert task.cancelled()
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
