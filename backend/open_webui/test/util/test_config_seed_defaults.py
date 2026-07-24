from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from open_webui.models import config as config_models
from open_webui.models.config import Config
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.asyncio
async def test_seed_defaults_is_safe_under_concurrent_worker_startup(tmp_path, monkeypatch):
    engine = create_async_engine(
        f'sqlite+aiosqlite:///{tmp_path / "config.db"}',
        connect_args={'timeout': 30},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Config.__table__.create)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    readers_ready = 0
    all_readers_ready = asyncio.Event()
    reader_lock = asyncio.Lock()

    class BarrierSession:
        def __init__(self, session):
            self._session = session

        async def execute(self, statement, *args, **kwargs):
            nonlocal readers_ready
            result = await self._session.execute(statement, *args, **kwargs)
            if isinstance(statement, Select):
                async with reader_lock:
                    readers_ready += 1
                    if readers_ready == 4:
                        all_readers_ready.set()
                await asyncio.wait_for(all_readers_ready.wait(), timeout=5)
            return result

        def add(self, value):
            self._session.add(value)

        def get_bind(self):
            return self._session.get_bind()

        async def commit(self):
            await self._session.commit()

    @asynccontextmanager
    async def db_context():
        async with session_factory() as session:
            yield BarrierSession(session)

    monkeypatch.setattr(config_models, 'get_async_db', db_context)
    defaults = {
        'chat.global_system_prompt': '',
        'agent.mode.enable': True,
    }

    results = await asyncio.gather(
        *(Config.seed_defaults(defaults) for _ in range(4)),
        return_exceptions=True,
    )

    assert all(not isinstance(result, Exception) for result in results), results
    async with session_factory() as session:
        rows = (await session.execute(select(Config).order_by(Config.key))).scalars().all()
    assert [(row.key, row.value) for row in rows] == [
        ('agent.mode.enable', True),
        ('chat.global_system_prompt', ''),
    ]

    async with session_factory() as session:
        existing = await session.get(Config, 'agent.mode.enable')
        existing.value = False
        await session.commit()

    await Config.seed_defaults(defaults)
    async with session_factory() as session:
        existing = await session.get(Config, 'agent.mode.enable')
    assert existing.value is False

    await engine.dispose()
