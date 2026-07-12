import inspect

from open_webui import functions
from open_webui.routers import ollama, openai
from open_webui.utils import chat


def test_openai_dispatch_uses_global_model_system_prompt_helper():
    source = inspect.getsource(openai.generate_chat_completion)

    assert 'apply_model_system_prompt_to_body' in source


def test_ollama_dispatch_uses_global_model_system_prompt_helper():
    source = inspect.getsource(ollama.generate_chat_completion)

    assert 'apply_model_system_prompt_to_body' in source


def test_ollama_openai_compatible_dispatch_uses_global_model_system_prompt_helper():
    source = inspect.getsource(ollama.generate_openai_chat_completion)

    assert 'apply_model_system_prompt_to_body' in source
    assert source.index('system = None') < source.index('if model_info is not None:')


def test_function_pipe_dispatch_uses_global_model_system_prompt_helper():
    source = inspect.getsource(functions.generate_function_chat_completion)

    assert 'apply_model_system_prompt_to_body' in source


def test_direct_connection_dispatch_uses_global_model_system_prompt_helper():
    source = inspect.getsource(chat.generate_direct_chat_completion)

    assert 'apply_model_system_prompt_to_body' in source
