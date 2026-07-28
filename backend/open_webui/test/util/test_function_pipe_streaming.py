import asyncio
import threading
import time
from types import SimpleNamespace

import open_webui.functions as functions_module
import pytest
from open_webui.functions import (
    _execute_function_pipe,
    _iterate_function_pipe_result,
    generate_function_chat_completion,
    get_function_models,
)


@pytest.mark.asyncio
async def test_sync_pipe_call_does_not_block_the_event_loop() -> None:
    caller_thread = threading.get_ident()
    pipe_thread = None

    def pipe():
        nonlocal pipe_thread
        pipe_thread = threading.get_ident()
        time.sleep(0.05)
        return 'done'

    task = asyncio.create_task(_execute_function_pipe(pipe, {}))
    await asyncio.sleep(0.01)

    assert not task.done()
    assert pipe_thread is not None
    assert pipe_thread != caller_thread
    assert await task == 'done'


@pytest.mark.asyncio
async def test_sync_manifold_model_discovery_does_not_block_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caller_thread = threading.get_ident()
    pipes_thread = None

    class FunctionModule:
        def pipes(self):
            nonlocal pipes_thread
            pipes_thread = threading.get_ident()
            time.sleep(0.05)
            return [{'id': 'child', 'name': 'Child'}]

    pipe = SimpleNamespace(
        id='test-manifold',
        type='pipe',
        created_at=1,
    )

    async def get_test_pipes(function_type, active_only):
        assert function_type == 'pipe'
        assert active_only is True
        return [pipe]

    async def get_test_function_module(request, pipe_id):
        del request
        assert pipe_id == pipe.id
        return FunctionModule()

    monkeypatch.setattr(
        functions_module.Functions,
        'get_functions_by_type',
        get_test_pipes,
    )
    monkeypatch.setattr(
        functions_module,
        'get_function_module_by_id',
        get_test_function_module,
    )

    task = asyncio.create_task(get_function_models(SimpleNamespace()))
    await asyncio.sleep(0.01)

    assert not task.done()
    assert pipes_thread is not None
    assert pipes_thread != caller_thread
    assert await task == [
        {
            'id': 'test-manifold.child',
            'name': 'Child',
            'object': 'model',
            'created': 1,
            'owned_by': 'openai',
            'pipe': {'type': 'pipe'},
            'has_user_valves': False,
        }
    ]


@pytest.mark.asyncio
async def test_sync_pipe_iteration_does_not_block_the_event_loop() -> None:
    caller_thread = threading.get_ident()
    next_threads: list[int] = []

    class SlowIterator:
        def __init__(self) -> None:
            self._done = False

        def __iter__(self):
            return self

        def __next__(self):
            if self._done:
                raise StopIteration
            self._done = True
            next_threads.append(threading.get_ident())
            time.sleep(0.05)
            return 'chunk'

    stream = _iterate_function_pipe_result(SlowIterator())
    next_task = asyncio.create_task(anext(stream))
    await asyncio.sleep(0.01)

    assert not next_task.done()
    assert next_threads and next_threads[0] != caller_thread
    assert await next_task == 'chunk'
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


@pytest.mark.asyncio
async def test_cancelled_sync_pipe_iteration_closes_after_pending_next_returns() -> None:
    next_started = threading.Event()
    release_next = threading.Event()
    closed = threading.Event()

    class BlockingIterator:
        def __iter__(self):
            return self

        def __next__(self):
            next_started.set()
            release_next.wait(timeout=1)
            return 'late chunk'

        def close(self) -> None:
            closed.set()

    stream = _iterate_function_pipe_result(BlockingIterator())
    pending = asyncio.create_task(anext(stream))
    assert await asyncio.to_thread(next_started.wait, 0.2)

    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    release_next.set()
    await stream.aclose()
    assert await asyncio.to_thread(closed.wait, 0.2)


@pytest.mark.asyncio
async def test_streaming_pipe_error_is_not_followed_by_success_terminators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def pipe_stream():
        yield {'error': {'message': 'provider stream failed'}}

    def pipe(body):
        del body
        return pipe_stream()

    async def no_model_info(model_id):
        del model_id
        return None

    async def get_test_function_module(request, pipe_id):
        del request, pipe_id
        return SimpleNamespace(pipe=pipe)

    monkeypatch.setattr(functions_module.Models, 'get_model_by_id', no_model_info)
    monkeypatch.setattr(
        functions_module,
        'get_function_module_by_id',
        get_test_function_module,
    )

    request = SimpleNamespace(
        cookies=None,
        state=SimpleNamespace(
            bypass_system_prompt=True,
            bypass_global_system_prompt=True,
        ),
    )
    user = SimpleNamespace(id='test-user')
    response = await generate_function_chat_completion(
        request,
        {'model': 'test-pipe', 'stream': True},
        user,
    )

    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
    body = ''.join(chunks)

    assert '"provider stream failed"' in body
    assert '"finish_reason": "stop"' not in body
    assert '[DONE]' not in body
