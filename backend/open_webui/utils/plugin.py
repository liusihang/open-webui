from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import types
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from open_webui.env import (
    ENABLE_PIP_INSTALL_FRONTMATTER_REQUIREMENTS,
    ENABLE_PLUGINS,
    OFFLINE_MODE,
    PIP_OPTIONS,
    PIP_PACKAGE_INDEX_OPTIONS,
)
from open_webui.models.functions import FunctionModel, Functions
from open_webui.models.tools import Tools
from open_webui.utils.cache_invalidation import (
    CACHE_NAMESPACE_FUNCTIONS,
    CACHE_NAMESPACE_TOOLS,
    ensure_cache_fresh,
)

log = logging.getLogger(__name__)

_PLUGIN_MODULE_EXECUTOR = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix='open-webui-plugin-loader',
)


def resolve_valves_schema_options(valves_class: type, schema: dict, user: Any = None) -> dict:
    """
    Resolve dynamic options in a Valves schema.

    For properties with `input.options`, this function handles two cases:
    - List: Used directly as dropdown options
    - String: Treated as method name, called to get options dynamically

    Usage in Valves:
        class UserValves(BaseModel):
            # Static options
            priority: str = Field(
                default="medium",
                json_schema_extra={
                    "input": {
                        "type": "select",
                        "options": ["low", "medium", "high"]
                    }
                }
            )

            # Dynamic options (method name)
            model: str = Field(
                default="",
                json_schema_extra={
                    "input": {
                        "type": "select",
                        "options": "get_model_options"
                    }
                }
            )

            @classmethod
            def get_model_options(cls, __user__=None) -> list[dict]:
                return [{"value": "gpt-4", "label": "GPT-4"}]

    Args:
        valves_class: The Valves or UserValves Pydantic model class
        schema: The JSON schema dict from valves_class.schema()
        user: Optional user object passed to methods that accept __user__

    Returns:
        Modified schema dict with resolved options
    """
    if not schema or 'properties' not in schema:
        return schema

    # Make a copy to avoid mutating the original
    schema = dict(schema)
    schema['properties'] = dict(schema.get('properties', {}))

    for prop_name, prop_schema in list(schema['properties'].items()):
        # Get the original field info from the Pydantic model
        if not hasattr(valves_class, 'model_fields'):
            continue

        field_info = valves_class.model_fields.get(prop_name)
        if not field_info:
            continue

        # Check json_schema_extra for options
        json_schema_extra = field_info.json_schema_extra
        if not json_schema_extra or not isinstance(json_schema_extra, dict):
            continue

        input_config = json_schema_extra.get('input')
        if not input_config or not isinstance(input_config, dict):
            continue

        options = input_config.get('options')
        if options is None:
            continue

        resolved_options = None

        # Case 1: options is already a list - use directly
        if isinstance(options, list):
            resolved_options = options

        # Case 2: options is a string - treat as method name
        elif isinstance(options, str) and options:
            method = getattr(valves_class, options, None)
            if method is None or not callable(method):
                log.warning(f"options '{options}' not found or not callable on {valves_class.__name__}")
                continue

            try:
                import inspect

                sig = inspect.signature(method)
                params = sig.parameters

                # Prepare kwargs based on what the method accepts
                kwargs = {}
                if '__user__' in params and user is not None:
                    kwargs['__user__'] = user.model_dump() if hasattr(user, 'model_dump') else user
                if 'user' in params and user is not None:
                    kwargs['user'] = user.model_dump() if hasattr(user, 'model_dump') else user

                resolved_options = method(**kwargs) if kwargs else method()

                # Validate return type
                if not isinstance(resolved_options, list):
                    log.warning(f"Method '{options}' did not return a list for {prop_name}")
                    continue

            except Exception as e:
                log.warning(f'Failed to resolve options for {prop_name}: {e}')
                continue
        else:
            # Invalid options type - skip
            continue

        # Update the schema with resolved options
        schema['properties'][prop_name] = dict(prop_schema)
        if 'input' not in schema['properties'][prop_name]:
            schema['properties'][prop_name]['input'] = {'type': 'select'}
        else:
            schema['properties'][prop_name]['input'] = dict(schema['properties'][prop_name].get('input', {}))
        schema['properties'][prop_name]['input']['options'] = resolved_options

    return schema


def extract_frontmatter(content):
    """
    Extract frontmatter as a dictionary from the provided content string.
    """
    frontmatter = {}
    frontmatter_started = False
    frontmatter_ended = False
    frontmatter_pattern = re.compile(r'^\s*([a-z_]+):\s*(.*)\s*$', re.IGNORECASE)

    try:
        lines = content.splitlines()
        if len(lines) < 1 or lines[0].strip() != '"""':
            # The content doesn't start with triple quotes
            return {}

        frontmatter_started = True

        for line in lines[1:]:
            if '"""' in line:
                if frontmatter_started:
                    frontmatter_ended = True
                    break

            if frontmatter_started and not frontmatter_ended:
                match = frontmatter_pattern.match(line)
                if match:
                    key, value = match.groups()
                    frontmatter[key.strip()] = value.strip()

    except Exception as e:
        log.exception(f'Failed to extract frontmatter: {e}')
        return {}

    return frontmatter


def replace_imports(content):
    """
    Replace the import paths in the content.
    """
    replacements = {
        'from utils': 'from open_webui.utils',
        'from apps': 'from open_webui.apps',
        'from main': 'from open_webui.main',
        'from config': 'from open_webui.config',
    }

    for old, new in replacements.items():
        content = content.replace(old, new)

    return content


def _load_plugin_module(
    module_name: str,
    content: str,
    class_types: tuple[tuple[str, str | None], ...],
    missing_class_error: str,
):
    # Imports and constructors are arbitrary synchronous plugin code, so all
    # module initialization must run outside the application's event loop.
    temp_file = tempfile.NamedTemporaryFile(delete=False)
    temp_file.close()
    module = types.ModuleType(module_name)
    sys.modules[module_name] = module

    try:
        with open(temp_file.name, 'w', encoding='utf-8') as f:
            f.write(content)
        module.__dict__['__file__'] = temp_file.name

        exec(content, module.__dict__)
        frontmatter = extract_frontmatter(content)
        log.info(f'Loaded module: {module.__name__}')

        for class_name, plugin_type in class_types:
            if hasattr(module, class_name):
                return (
                    getattr(module, class_name)(),
                    plugin_type,
                    frontmatter,
                    module,
                )

        raise Exception(missing_class_error)
    except BaseException:
        if sys.modules.get(module_name) is module:
            sys.modules.pop(module_name, None)
        raise
    finally:
        os.unlink(temp_file.name)


def _discard_abandoned_plugin_module(module_name: str, future: Future):
    try:
        _, _, _, module = future.result()
    except BaseException:
        return

    if sys.modules.get(module_name) is module:
        sys.modules.pop(module_name, None)


async def _load_plugin_module_off_loop(
    module_name: str,
    content: str,
    class_types: tuple[tuple[str, str | None], ...],
    missing_class_error: str,
):
    # A dedicated single-worker queue preserves the event loop's former
    # serialization without filling asyncio's shared default executor with lock
    # waiters during a cold multi-plugin load.
    abandoned = threading.Event()
    future = _PLUGIN_MODULE_EXECUTOR.submit(
        _load_plugin_module,
        module_name,
        content,
        class_types,
        missing_class_error,
    )

    def discard_if_abandoned(completed: Future) -> None:
        if abandoned.is_set():
            _discard_abandoned_plugin_module(module_name, completed)

    # This callback belongs to the underlying concurrent future, so cleanup
    # still runs in the worker even if the request's asyncio loop has closed.
    future.add_done_callback(discard_if_abandoned)
    asyncio_future = asyncio.wrap_future(future)
    try:
        plugin, plugin_type, frontmatter, _ = await asyncio.shield(asyncio_future)
        return plugin, plugin_type, frontmatter
    except asyncio.CancelledError:
        # Synchronous plugin code cannot be interrupted safely. Let the worker
        # finish, then discard only the exact module created by this abandoned
        # load so a newer reload of the same ID cannot be removed by mistake.
        abandoned.set()
        if future.done():
            _discard_abandoned_plugin_module(module_name, future)
        raise


# May the intent of the one who wrote it survive every
# import and transformation, as a deed survives the generations.
async def load_tool_module_by_id(tool_id, content=None):
    if not ENABLE_PLUGINS:
        raise RuntimeError('Plugins are disabled by ENABLE_PLUGINS=false')

    frontmatter = None
    if content is None:
        tool = await Tools.get_tool_by_id(tool_id)
        if not tool:
            raise Exception(f'Toolkit not found: {tool_id}')

        content = tool.content

        content = replace_imports(content)
        await Tools.update_tool_by_id(tool_id, {'content': content})
    else:
        frontmatter = extract_frontmatter(content)
        # Install required packages found within the frontmatter.
        # Runs `pip install` via subprocess, which can take a long time;
        # offload to a thread so it doesn't block the event loop.
        await asyncio.to_thread(install_frontmatter_requirements, frontmatter.get('requirements', ''))

    try:
        tool_module, _, frontmatter = await _load_plugin_module_off_loop(
            f'tool_{tool_id}',
            content,
            (('Tools', None),),
            'No Tools class found in the module',
        )
        return tool_module, frontmatter
    except Exception as e:
        log.error(f'Error loading module: {tool_id}: {e}')
        raise


async def load_function_module_by_id(function_id: str, content: str | None = None):
    if not ENABLE_PLUGINS:
        raise RuntimeError('Plugins are disabled by ENABLE_PLUGINS=false')

    frontmatter = None
    if content is None:
        function = await Functions.get_function_by_id(function_id)
        if not function:
            raise Exception(f'Function not found: {function_id}')
        content = function.content

        content = replace_imports(content)
        await Functions.update_function_by_id(function_id, {'content': content})
    else:
        frontmatter = extract_frontmatter(content)
        # `pip install` via subprocess can block for a long time; offload it.
        await asyncio.to_thread(install_frontmatter_requirements, frontmatter.get('requirements', ''))

    try:
        return await _load_plugin_module_off_loop(
            f'function_{function_id}',
            content,
            (
                ('Pipe', 'pipe'),
                ('Filter', 'filter'),
                ('Action', 'action'),
                ('Event', 'event'),
            ),
            'No Function class found in the module',
        )
    except Exception as e:
        log.error(f'Error loading module: {function_id}: {e}')
        await Functions.update_function_by_id(function_id, {'is_active': False})
        raise


def _state_cache(request, name: str) -> dict:
    if not hasattr(request.app.state, name):
        setattr(request.app.state, name, {})
    return getattr(request.app.state, name)


def get_tools_cache(request) -> dict:
    return _state_cache(request, 'TOOLS')


def get_tool_contents_cache(request) -> dict:
    return _state_cache(request, 'TOOL_CONTENTS')


def get_functions_cache(request) -> dict:
    return _state_cache(request, 'FUNCTIONS')


def get_function_contents_cache(request) -> dict:
    return _state_cache(request, 'FUNCTION_CONTENTS')


async def get_tool_module_from_cache(request, tool_id, load_from_db=True):
    await ensure_cache_fresh(request.app, CACHE_NAMESPACE_TOOLS, tool_id)

    tools_cache = get_tools_cache(request)
    tool_contents_cache = get_tool_contents_cache(request)
    content = None

    if load_from_db:
        # Always load from the database by default
        tool = await Tools.get_tool_by_id(tool_id)
        if not tool:
            raise Exception(f'Tool not found: {tool_id}')
        content = tool.content

        new_content = replace_imports(content)
        if new_content != content:
            content = new_content
            # Update the tool content in the database
            await Tools.update_tool_by_id(tool_id, {'content': content})

        if tool_id in tool_contents_cache and tool_id in tools_cache:
            if tool_contents_cache[tool_id] == content:
                return tools_cache[tool_id], None

        tool_module, frontmatter = await load_tool_module_by_id(tool_id, content)
    else:
        if tool_id in tools_cache:
            return tools_cache[tool_id], None

        tool_module, frontmatter = await load_tool_module_by_id(tool_id)

    tools_cache[tool_id] = tool_module
    tool_contents_cache[tool_id] = content

    return tool_module, frontmatter


async def get_function_module_from_cache(
    request, function_id, function: FunctionModel | None = None, load_from_db=True
):
    await ensure_cache_fresh(request.app, CACHE_NAMESPACE_FUNCTIONS, function_id)

    functions_cache = get_functions_cache(request)
    function_contents_cache = get_function_contents_cache(request)
    content = None

    if load_from_db:
        # Always load from the database by default
        # This is useful for hooks like "inlet" or "outlet" where the content might change
        # and we want to ensure the latest content is used.

        if function is None:
            function = await Functions.get_function_by_id(function_id)
        if not function:
            raise Exception(f'Function not found: {function_id}')
        content = function.content

        new_content = replace_imports(content)
        if new_content != content:
            content = new_content
            # Update the function content in the database
            await Functions.update_function_by_id(function_id, {'content': content})

        if function_id in function_contents_cache and function_id in functions_cache:
            if function_contents_cache[function_id] == content:
                return functions_cache[function_id], None, None

        function_module, function_type, frontmatter = await load_function_module_by_id(function_id, content)
    else:
        # Load from cache (e.g. "stream" hook)
        # This is useful for performance reasons

        if function_id in functions_cache:
            return functions_cache[function_id], None, None

        function_module, function_type, frontmatter = await load_function_module_by_id(function_id)

    functions_cache[function_id] = function_module
    function_contents_cache[function_id] = content

    return function_module, function_type, frontmatter


_installed_requirements = set()


def install_frontmatter_requirements(requirements: str):
    global _installed_requirements
    if not ENABLE_PIP_INSTALL_FRONTMATTER_REQUIREMENTS:
        log.info('ENABLE_PIP_INSTALL_FRONTMATTER_REQUIREMENTS is disabled, skipping installation of requirements.')
        return

    if OFFLINE_MODE:
        log.info('Offline mode enabled, skipping installation of requirements.')
        return

    if requirements:
        try:
            req_list = [req.strip() for req in requirements.split(',')]
            new_reqs = [req for req in req_list if req and req not in _installed_requirements]

            if not new_reqs:
                return

            log.info(f'Installing requirements: {" ".join(new_reqs)}')
            subprocess.check_call(
                [sys.executable, '-m', 'pip', 'install'] + PIP_OPTIONS + new_reqs + PIP_PACKAGE_INDEX_OPTIONS
            )
            _installed_requirements.update(new_reqs)
        except Exception as e:
            log.error(f'Error installing packages: {" ".join(new_reqs)}')
            raise e

    else:
        log.info('No requirements found in frontmatter.')


async def install_tool_and_function_dependencies():
    """
    Install all dependencies for all admin tools and active functions.

    By first collecting all dependencies from the frontmatter of each tool and function,
    and then installing them using pip. Duplicates or similar version specifications are
    handled by pip as much as possible.
    """
    if not ENABLE_PLUGINS:
        log.info('ENABLE_PLUGINS is disabled, skipping tool and function dependencies.')
        return

    function_list = await Functions.get_functions(active_only=True)
    tool_list = await Tools.get_tools()

    all_dependencies = ''
    try:
        for function in function_list:
            frontmatter = extract_frontmatter(replace_imports(function.content))
            if dependencies := frontmatter.get('requirements'):
                all_dependencies += f'{dependencies}, '
        for tool in tool_list:
            # Only install requirements for admin tools
            if tool.user and tool.user.role == 'admin':
                frontmatter = extract_frontmatter(replace_imports(tool.content))
                if dependencies := frontmatter.get('requirements'):
                    all_dependencies += f'{dependencies}, '

        # `pip install` via subprocess can block for a long time; offload it.
        await asyncio.to_thread(install_frontmatter_requirements, all_dependencies.strip(', '))
    except Exception as e:
        log.error(f'Error installing requirements: {e}')
