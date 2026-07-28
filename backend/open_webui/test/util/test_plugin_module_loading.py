import asyncio
import builtins
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from open_webui.utils.plugin import (
    load_function_module_by_id,
    load_tool_module_by_id,
)

SLOW_MODULE_PREFIX = """
import threading
import time

EXECUTION_THREAD = threading.get_ident()
time.sleep(0.05)
"""


@pytest.mark.asyncio
async def test_function_module_execution_does_not_block_event_loop() -> None:
    function_id = 'slow_function_module_test'
    module_name = f'function_{function_id}'
    content = f"""{SLOW_MODULE_PREFIX}
class Pipe:
    execution_thread = EXECUTION_THREAD

    def __init__(self):
        self.construction_thread = threading.get_ident()
        time.sleep(0.05)
"""

    try:
        task = asyncio.create_task(load_function_module_by_id(function_id, content))
        await asyncio.sleep(0.01)

        assert not task.done()
        function, function_type, _ = await task
        assert function_type == 'pipe'
        assert function.execution_thread != threading.get_ident()
        assert function.construction_thread != threading.get_ident()
    finally:
        sys.modules.pop(module_name, None)


@pytest.mark.asyncio
async def test_tool_module_execution_does_not_block_event_loop() -> None:
    tool_id = 'slow_tool_module_test'
    module_name = f'tool_{tool_id}'
    content = f"""{SLOW_MODULE_PREFIX}
class Tools:
    execution_thread = EXECUTION_THREAD

    def __init__(self):
        self.construction_thread = threading.get_ident()
        time.sleep(0.05)
"""

    try:
        task = asyncio.create_task(load_tool_module_by_id(tool_id, content))
        await asyncio.sleep(0.01)

        assert not task.done()
        tools, _ = await task
        assert tools.execution_thread != threading.get_ident()
        assert tools.construction_thread != threading.get_ident()
    finally:
        sys.modules.pop(module_name, None)


@pytest.mark.asyncio
async def test_plugin_module_initialization_remains_serialized() -> None:
    state_name = '_open_webui_plugin_loading_test_state'
    function_ids = ('serialized_function_one', 'serialized_function_two')
    content = f"""
import builtins
import time

class Pipe:
    def __init__(self):
        state = getattr(builtins, '{state_name}')
        state['active'] += 1
        state['max_active'] = max(state['max_active'], state['active'])
        time.sleep(0.05)
        state['active'] -= 1
"""
    setattr(builtins, state_name, {'active': 0, 'max_active': 0})

    try:
        await asyncio.gather(*(load_function_module_by_id(function_id, content) for function_id in function_ids))
        assert getattr(builtins, state_name)['max_active'] == 1
    finally:
        delattr(builtins, state_name)
        for function_id in function_ids:
            sys.modules.pop(f'function_{function_id}', None)


@pytest.mark.asyncio
async def test_failed_tool_module_load_removes_sys_modules_entry() -> None:
    tool_id = 'invalid_tool_module_test'
    module_name = f'tool_{tool_id}'

    with pytest.raises(Exception, match='No Tools class found in the module'):
        await load_tool_module_by_id(tool_id, 'VALUE = 1')

    assert module_name not in sys.modules


@pytest.mark.asyncio
async def test_concurrent_plugin_loads_do_not_starve_default_executor() -> None:
    loop = asyncio.get_running_loop()
    previous_executor = loop._default_executor
    constrained_executor = ThreadPoolExecutor(max_workers=2)
    loop.set_default_executor(constrained_executor)

    state_name = '_open_web_ui_plugin_executor_starvation_test'
    function_ids = tuple(f'executor_starvation_{index}' for index in range(3))
    first_constructor_started = threading.Event()
    setattr(builtins, state_name, first_constructor_started)
    content = f"""
import builtins
import time

class Pipe:
    def __init__(self):
        getattr(builtins, '{state_name}').set()
        time.sleep(0.1)
"""
    loads = [asyncio.create_task(load_function_module_by_id(function_id, content)) for function_id in function_ids]

    try:
        for _ in range(100):
            if first_constructor_started.is_set():
                break
            await asyncio.sleep(0.001)
        assert first_constructor_started.is_set()

        # Give the other plugin-load tasks time to enter their execution path.
        await asyncio.sleep(0.01)
        unrelated_work = asyncio.create_task(asyncio.to_thread(lambda: 'unrelated'))
        done, _ = await asyncio.wait({unrelated_work}, timeout=0.05)

        assert unrelated_work in done
        assert unrelated_work.result() == 'unrelated'
    finally:
        await asyncio.gather(*loads)
        delattr(builtins, state_name)
        for function_id in function_ids:
            sys.modules.pop(f'function_{function_id}', None)
        loop._default_executor = previous_executor
        constrained_executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_base_exception_during_load_removes_sys_modules_entry() -> None:
    class PluginLoadAbort(BaseException):
        pass

    exception_name = '_open_web_ui_plugin_load_abort'
    tool_id = 'base_exception_tool_module_test'
    module_name = f'tool_{tool_id}'
    setattr(builtins, exception_name, PluginLoadAbort)

    try:
        with pytest.raises(PluginLoadAbort):
            await load_tool_module_by_id(
                tool_id,
                f"import builtins\nraise builtins.{exception_name}('abort')",
            )

        assert module_name not in sys.modules
    finally:
        delattr(builtins, exception_name)
        sys.modules.pop(module_name, None)


@pytest.mark.asyncio
async def test_cancelled_load_removes_module_after_worker_finishes() -> None:
    state_name = '_open_web_ui_cancelled_plugin_load_test'
    tool_id = 'cancelled_tool_module_test'
    module_name = f'tool_{tool_id}'
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    setattr(
        builtins,
        state_name,
        {'started': started, 'release': release, 'finished': finished},
    )
    content = f"""
import builtins

class Tools:
    def __init__(self):
        state = getattr(builtins, '{state_name}')
        state['started'].set()
        state['release'].wait(timeout=1)
        state['finished'].set()
"""
    task = asyncio.create_task(load_tool_module_by_id(tool_id, content))

    try:
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.001)
        assert started.is_set()

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        release.set()
        for _ in range(100):
            if finished.is_set() and module_name not in sys.modules:
                break
            await asyncio.sleep(0.001)

        assert finished.is_set()
        assert module_name not in sys.modules
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        delattr(builtins, state_name)
        sys.modules.pop(module_name, None)


def test_cancelled_load_cleanup_survives_event_loop_shutdown() -> None:
    state_name = '_open_web_ui_cancelled_plugin_closed_loop_test'
    tool_id = 'cancelled_closed_loop_tool_module_test'
    module_name = f'tool_{tool_id}'
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    setattr(
        builtins,
        state_name,
        {'started': started, 'release': release, 'finished': finished},
    )
    content = f"""
import builtins

class Tools:
    def __init__(self):
        state = getattr(builtins, '{state_name}')
        state['started'].set()
        state['release'].wait(timeout=1)
        state['finished'].set()
"""

    async def cancel_load() -> None:
        task = asyncio.create_task(load_tool_module_by_id(tool_id, content))
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.001)
        assert started.is_set()

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    try:
        # asyncio.run closes this loop before the plugin worker is released.
        asyncio.run(cancel_load())
        release.set()
        for _ in range(100):
            if finished.is_set() and module_name not in sys.modules:
                break
            time.sleep(0.001)

        assert finished.is_set()
        assert module_name not in sys.modules
    finally:
        release.set()
        delattr(builtins, state_name)
        sys.modules.pop(module_name, None)
