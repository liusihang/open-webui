"""
Built-in tools for Open WebUI.

These tools are automatically available when native function calling is enabled.

IMPORTANT: DO NOT IMPORT THIS MODULE DIRECTLY IN OTHER PARTS OF THE CODEBASE.
"""

import asyncio
import logging
import time
from typing import Literal, Optional
from urllib.parse import unquote

from fastapi import HTTPException, Request

from open_webui.config import RAG_EMBEDDING_QUERY_PREFIX
from open_webui.env import (
    KNOWLEDGE_GREP_MAX_MATCHES,
    VIEW_FILE_DEFAULT_MAX_CHARS,
    VIEW_FILE_MAX_CHARS,
)
from open_webui.events import EVENTS, publish_event
from open_webui.models.channels import Channel, ChannelMember, Channels
from open_webui.models.chats import Chats, chat_search_content_query, chat_search_terms
from open_webui.models.config import Config
from open_webui.models.groups import Groups
from open_webui.models.memories import Memories
from open_webui.models.messages import Message, Messages
from open_webui.models.notes import Notes
from open_webui.models.users import UserModel
from open_webui.retrieval.utils import filter_source_metadata, get_content_from_url
from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT
from open_webui.routers.images import (
    CreateImageForm,
    EditImageForm,
    image_edits,
    image_generations,
)
from open_webui.routers.memories import (
    AddMemoryForm,
    ListMemoryPathsForm,
    MemoryUpdateModel,
    ReadMemoryPathForm,
    SearchMemoriesForm,
    UpdateMemoriesForm,
    update_memory_by_id,
)
from open_webui.routers.memories import (
    add_memory as _add_memory,
)
from open_webui.routers.memories import (
    list_memory_paths as _list_memory_paths,
)
from open_webui.routers.memories import (
    read_memory_path as _read_memory_path,
)
from open_webui.routers.memories import (
    search_memories as _search_memories,
)
from open_webui.routers.memories import (
    update_memories as _update_memories,
)
from open_webui.routers.retrieval import search_web as _search_web
from open_webui.socket.main import sio
from open_webui.tasks import stop_item_tasks
from open_webui.tools.knowledge_fs import kb_exec  # noqa: F401 — re-exported
from open_webui.tools.knowledge_tools import file_content_page, knowledge_entries, model_evidence_result
from open_webui.utils.chat_id import is_saved_chat_id
from open_webui.utils.json_codec import JSONCodec
from open_webui.utils.native_pdf import is_native_pdf_attachment
from open_webui.utils.notifications import notify_target
from open_webui.utils.sanitize import sanitize_code
from open_webui.utils.skill_files import SkillFile, SkillFileOperation, skill_content_page, skill_overview

log = logging.getLogger(__name__)

MAX_KNOWLEDGE_BASE_SEARCH_ITEMS = 10_000


async def _has_write_access_to_note(note, user_id: str) -> bool:
    if note.user_id == user_id:
        return True

    from open_webui.models.access_grants import AccessGrants

    user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]
    return await AccessGrants.has_access(
        user_id=user_id,
        resource_type='note',
        resource_id=note.id,
        permission='write',
        user_group_ids=set(user_group_ids),
    )


async def _emit_note_updated(request: Request, user: dict, note) -> None:
    await sio.emit('events:note', note.model_dump(), to=f'note:{note.id}')
    await publish_event(
        request,
        EVENTS.NOTE_UPDATED,
        actor=user,
        subject_id=note.id,
        data={'title': note.title},
    )


async def _has_read_access_to_file(
    file,
    user: dict,
    model_knowledge: Optional[list[dict]] = None,
) -> bool:
    """Check if a user can read a file via ownership, admin role, model attachment, or access grants."""
    user_id = user.get('id')
    user_role = user.get('role', 'user')
    if file.user_id == user_id or user_role == 'admin':
        return True
    if model_knowledge and any(item.get('type') == 'file' and item.get('id') == file.id for item in model_knowledge):
        return True
    from open_webui.utils.access_control.files import has_access_to_file

    return await has_access_to_file(
        file_id=file.id,
        access_type='read',
        user=UserModel(**user),
    )


# =============================================================================
# TIME UTILITIES
# =============================================================================


async def notify(
    message: str,
    target: str = '',
    title: str = '',
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Send a notification to the user's configured notification target.

    :param message: Notification body.
    :param target: Optional target id or name. Empty uses the default target.
    :param title: Optional notification title.
    """
    user_id = (__user__ or {}).get('id')
    if not user_id:
        return 'Notification failed: user not found.'

    app_name = getattr(getattr(__request__, 'app', None), 'state', None)
    # LICENSE covers this Open WebUI notification identifier.
    # Do not alter, remove, obscure, or replace it except as LICENSE permits:
    # https://docs.openwebui.com/license.
    app_name = getattr(app_name, 'WEBUI_NAME', 'Open WebUI')
    try:
        result = await notify_target(user_id, message, target=target, title=title, app_name=app_name)
        return f'Notification sent to {result.get("target_id")}.'
    except Exception as e:
        return f'Notification failed: {e}'


async def get_current_timestamp(
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Get the current Unix timestamp in seconds.

    :return: JSON with current_timestamp (seconds), current_iso (UTC ISO format), and user_local_iso (user's local time)
    """
    try:
        import datetime
        from zoneinfo import ZoneInfo

        now = datetime.datetime.now(datetime.timezone.utc)
        result = {
            'current_timestamp': int(now.timestamp()),
            'current_iso': now.isoformat(),
        }

        # Include the user's local time if timezone is available
        tz_name = __user__.get('timezone') if __user__ else None
        if tz_name:
            try:
                user_tz = ZoneInfo(tz_name)
                user_now = now.astimezone(user_tz)
                result['user_local_iso'] = user_now.isoformat()
                result['user_timezone'] = tz_name
            except Exception:
                pass

        return JSONCodec.dumps(result, ensure_ascii=False)
    except Exception as e:
        log.exception(f'get_current_timestamp error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def calculate_timestamp(
    days_ago: int = 0,
    weeks_ago: int = 0,
    months_ago: int = 0,
    years_ago: int = 0,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Get the current Unix timestamp, optionally adjusted by days, weeks, months, or years.
    Use this to calculate timestamps for date filtering in search functions.
    Examples: "last week" = weeks_ago=1, "3 days ago" = days_ago=3, "a year ago" = years_ago=1

    :param days_ago: Number of days to subtract from current time (default: 0)
    :param weeks_ago: Number of weeks to subtract from current time (default: 0)
    :param months_ago: Number of months to subtract from current time (default: 0)
    :param years_ago: Number of years to subtract from current time (default: 0)
    :return: JSON with current_timestamp and calculated_timestamp (both in seconds)
    """
    try:
        import datetime

        from dateutil.relativedelta import relativedelta

        now = datetime.datetime.now(datetime.timezone.utc)
        current_ts = int(now.timestamp())

        # Calculate the adjusted time
        total_days = days_ago + (weeks_ago * 7)
        adjusted = now - datetime.timedelta(days=total_days)

        # Handle months and years separately (variable length)
        if months_ago > 0 or years_ago > 0:
            adjusted = adjusted - relativedelta(months=months_ago, years=years_ago)

        adjusted_ts = int(adjusted.timestamp())

        result = {
            'current_timestamp': current_ts,
            'current_iso': now.isoformat(),
            'calculated_timestamp': adjusted_ts,
            'calculated_iso': adjusted.isoformat(),
        }

        # Include the user's local time if timezone is available
        tz_name = __user__.get('timezone') if __user__ else None
        if tz_name:
            try:
                from zoneinfo import ZoneInfo

                user_tz = ZoneInfo(tz_name)
                result['user_local_iso'] = now.astimezone(user_tz).isoformat()
                result['calculated_local_iso'] = adjusted.astimezone(user_tz).isoformat()
                result['user_timezone'] = tz_name
            except Exception:
                pass

        return JSONCodec.dumps(result, ensure_ascii=False)
    except ImportError:
        # Fallback without dateutil
        import datetime

        now = datetime.datetime.now(datetime.timezone.utc)
        current_ts = int(now.timestamp())
        total_days = days_ago + (weeks_ago * 7) + (months_ago * 30) + (years_ago * 365)
        adjusted = now - datetime.timedelta(days=total_days)
        adjusted_ts = int(adjusted.timestamp())
        result = {
            'current_timestamp': current_ts,
            'current_iso': now.isoformat(),
            'calculated_timestamp': adjusted_ts,
            'calculated_iso': adjusted.isoformat(),
        }

        tz_name = __user__.get('timezone') if __user__ else None
        if tz_name:
            try:
                from zoneinfo import ZoneInfo

                user_tz = ZoneInfo(tz_name)
                result['user_local_iso'] = now.astimezone(user_tz).isoformat()
                result['calculated_local_iso'] = adjusted.astimezone(user_tz).isoformat()
                result['user_timezone'] = tz_name
            except Exception:
                pass

        return JSONCodec.dumps(result, ensure_ascii=False)
    except Exception as e:
        log.exception(f'calculate_timestamp error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# =============================================================================
# WEB SEARCH TOOLS
# =============================================================================


async def search_web(
    query: str,
    count: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Search the public web for information. Best for current events, external references,
    or topics not covered in internal documents.

    :param query: The search query to look up
    :param count: Number of results to return (default: admin-configured value)
    :return: JSON with search results containing title, link, and snippet for each result
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        engine = await Config.get('web.search.engine')
        user = UserModel(**__user__) if __user__ else None

        configured = await Config.get('web.search.result_count')
        max_count = 5 if configured is None else configured
        count = max(1, min(count, max_count)) if count is not None else max_count

        results = await _search_web(__request__, engine, query, user)

        # Limit results
        results = results[:count] if results else []

        return JSONCodec.dumps(
            [{'title': r.title, 'link': r.link, 'snippet': r.snippet} for r in results],
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'search_web error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def fetch_url(
    url: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Fetch and extract the main text content from a web page URL.

    :param url: The URL to fetch content from
    :return: The extracted text content from the page
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        content, _ = await get_content_from_url(__request__, url)

        # Truncate if configured (WEB_FETCH_MAX_CONTENT_LENGTH)
        # Guard: content may be None if the web loader silently failed
        if content is not None:
            max_length = await Config.get('web.fetch.max_content_length')
            if max_length and max_length > 0 and len(content) > max_length:
                content = content[:max_length] + '\n\n[Content truncated...]'
        else:
            content = ''

        return content
    except Exception as e:
        log.warning(f'fetch_url error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# =============================================================================
# IMAGE GENERATION TOOLS
# =============================================================================


async def generate_image(
    prompt: str,
    size: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
    __event_emitter__: callable = None,
    __chat_id__: str = None,
    __message_id__: str = None,
) -> str:
    """
    Generate an image based on a text prompt.

    :param prompt: A detailed description of the image to generate
    :param size: Optional output size in WIDTHxHEIGHT pixels (e.g. "1536x1024"), supported by the configured image model.
        Omit to use the configured default.
    :return: Confirmation that the image was generated, or an error message
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        user = UserModel(**__user__) if __user__ else None

        images = await image_generations(
            request=__request__,
            form_data=CreateImageForm(prompt=prompt, size=size),
            metadata=(
                {'channel_id': __chat_id__.removeprefix('channel:'), 'message_id': __message_id__}
                if isinstance(__chat_id__, str) and __chat_id__.startswith('channel:')
                else None
            ),
            user=user,
        )

        # Prepare file entries for the images
        image_files = [{'type': 'image', **img} for img in images]

        # Persist files to DB if chat context is available
        if is_saved_chat_id(__chat_id__) and __message_id__ and images:
            db_files = await Chats.add_message_files_by_id_and_message_id(
                __chat_id__,
                __message_id__,
                image_files,
            )
            if db_files is not None:
                image_files = db_files

        # Emit the images to the UI if event emitter is available
        if __event_emitter__ and image_files:
            await __event_emitter__(
                {
                    'type': 'chat:message:files',
                    'data': {
                        'files': image_files,
                    },
                }
            )
            # Return a message indicating the image is already displayed
            return JSONCodec.dumps(
                {
                    'status': 'success',
                    'message': 'The image has been successfully generated and is already visible to the user in the chat. You do not need to display or embed the image again - just acknowledge that it has been created.',
                    'images': images,
                },
                ensure_ascii=False,
            )

        return JSONCodec.dumps({'status': 'success', 'images': images}, ensure_ascii=False)
    except Exception as e:
        log.exception(f'generate_image error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def edit_image(
    prompt: str,
    image_urls: list[str],
    size: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
    __event_emitter__: callable = None,
    __chat_id__: str = None,
    __message_id__: str = None,
) -> str:
    """
    Transform one or more existing images according to a text prompt.
    Supports targeted edits such as adding, removing, replacing, inpainting, extending, or compositing image content.

    :param prompt: A description of the transformation to apply to the provided images
    :param image_urls: Source image URLs to modify or use as composition inputs
    :param size: Optional output size in WIDTHxHEIGHT pixels (e.g. "1536x1024"), supported by the configured image model.
        Omit to use the configured default.
    :return: Confirmation that the images were edited, or an error message
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        user = UserModel(**__user__) if __user__ else None

        images = await image_edits(
            request=__request__,
            form_data=EditImageForm(prompt=prompt, image=image_urls, size=size),
            metadata=(
                {'channel_id': __chat_id__.removeprefix('channel:'), 'message_id': __message_id__}
                if isinstance(__chat_id__, str) and __chat_id__.startswith('channel:')
                else None
            ),
            user=user,
        )

        # Prepare file entries for the images
        image_files = [{'type': 'image', **img} for img in images]

        # Persist files to DB if chat context is available
        if is_saved_chat_id(__chat_id__) and __message_id__ and images:
            db_files = await Chats.add_message_files_by_id_and_message_id(
                __chat_id__,
                __message_id__,
                image_files,
            )
            if db_files is not None:
                image_files = db_files

        # Emit the images to the UI if event emitter is available
        if __event_emitter__ and image_files:
            await __event_emitter__(
                {
                    'type': 'chat:message:files',
                    'data': {
                        'files': image_files,
                    },
                }
            )
            # Return a message indicating the image is already displayed
            return JSONCodec.dumps(
                {
                    'status': 'success',
                    'message': 'The edited image has been successfully generated and is already visible to the user in the chat. You do not need to display or embed the image again - just acknowledge that it has been created.',
                    'images': images,
                },
                ensure_ascii=False,
            )

        return JSONCodec.dumps({'status': 'success', 'images': images}, ensure_ascii=False)
    except Exception as e:
        log.exception(f'edit_image error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# =============================================================================
# USER INPUT TOOLS
# =============================================================================


async def ask_user(
    questions: list[dict],
    allow_other: bool = True,
    timeout_ms: int = 120_000,
    __event_call__: callable = None,
) -> str:
    """
    Ask the user clarifying questions before continuing.
    Use this when the next step depends on user intent, preference, or a tradeoff that cannot be inferred safely.

    :param questions: 1-3 question objects, each with id, header, question, and 2-3 options. Each option needs label and description.
        List the option you recommend first; the UI labels the first option Recommended.
    :param allow_other: Whether users may enter a free-form answer instead of choosing one of the options
    :param timeout_ms: How long the browser should keep the prompt open before cancelling it
    :return: JSON with status and answers keyed by question id
    """
    try:
        if not isinstance(questions, list) or not 1 <= len(questions) <= 3:
            raise ValueError('ask_user requires 1-3 questions.')

        normalized_questions = []
        seen_ids = set()
        for index, question in enumerate(questions):
            if not isinstance(question, dict):
                raise ValueError('Each question must be an object.')

            question_id = str(question.get('id') or '').strip()[:64]
            if not question_id:
                raise ValueError('Each question requires a non-empty id.')
            if question_id in seen_ids:
                raise ValueError(f'Duplicate question id: {question_id}')
            seen_ids.add(question_id)

            options = question.get('options')
            if not isinstance(options, list) or not 2 <= len(options) <= 3:
                raise ValueError('Each question requires 2-3 options.')

            normalized_options = []
            for option in options:
                if not isinstance(option, dict):
                    raise ValueError('Each option must be an object.')

                label = str(option.get('label') or '').strip()[:80]
                description = str(option.get('description') or '').strip()[:240]
                if not label or not description:
                    raise ValueError('Each option requires a label and description.')

                normalized_options.append(
                    {
                        'label': label,
                        'description': description,
                    }
                )

            question_text = str(question.get('question') or '').strip()[:500]
            if not question_text:
                raise ValueError('Each question requires question text.')

            normalized_questions.append(
                {
                    'id': question_id,
                    'header': str(question.get('header') or '').strip()[:48] or f'Question {index + 1}',
                    'question': question_text,
                    'options': normalized_options,
                    'allow_other': bool(question.get('allow_other', allow_other)),
                }
            )

        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or not 60_000 <= timeout_ms <= 240_000:
            timeout_ms = 120_000

        if __event_call__ is None:
            return JSONCodec.dumps(
                {
                    'status': 'error',
                    'error': 'User input requires an active browser session with WebSocket connection.',
                },
                ensure_ascii=False,
            )

        output = await __event_call__(
            {
                'type': 'request:user_input',
                'data': {
                    'questions': normalized_questions,
                    'allow_other': allow_other,
                    'timeout_ms': timeout_ms,
                },
            }
        )

        if not isinstance(output, dict):
            return JSONCodec.dumps({'status': 'error', 'error': 'Invalid user input response.'}, ensure_ascii=False)
        if output.get('error'):
            return JSONCodec.dumps({'status': 'error', 'error': output.get('error')}, ensure_ascii=False)
        if output.get('status') == 'cancelled':
            return JSONCodec.dumps({'status': 'cancelled', 'answers': {}}, ensure_ascii=False)

        return JSONCodec.dumps(
            {
                'status': 'answered',
                'answers': output.get('answers', {}),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'ask_user error: {e}')
        return JSONCodec.dumps({'status': 'error', 'error': str(e)}, ensure_ascii=False)


# =============================================================================
# CODE INTERPRETER TOOLS
# =============================================================================


async def execute_code(
    code: str,
    __request__: Request = None,
    __user__: dict = None,
    __event_emitter__: callable = None,
    __event_call__: callable = None,
    __chat_id__: str = None,
    __message_id__: str = None,
    __metadata__: dict = None,
) -> str:
    """
    Execute Python code in a sandboxed environment and return the output.
    Use this to perform calculations, data analysis, generate visualizations,
    or run any Python code that would help answer the user's question.

    :param code: The Python code to execute
    :return: JSON with stdout, stderr, and result from execution
    """
    from uuid import uuid4

    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        # Sanitize code (strips ANSI codes and markdown fences)
        code = sanitize_code(code)

        # Import blocked modules from config (same as middleware)
        from open_webui.config import CODE_INTERPRETER_BLOCKED_MODULES

        # Add import blocking code if there are blocked modules
        if CODE_INTERPRETER_BLOCKED_MODULES:
            import textwrap

            blocking_code = textwrap.dedent(
                f"""
                import builtins

                BLOCKED_MODULES = {CODE_INTERPRETER_BLOCKED_MODULES}

                _real_import = builtins.__import__
                def restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
                    if name.split('.')[0] in BLOCKED_MODULES:
                        importer_name = globals.get('__name__') if globals else None
                        if importer_name == '__main__':
                            raise ImportError(
                                f"Direct import of module {{name}} is restricted."
                            )
                    return _real_import(name, globals, locals, fromlist, level)

                builtins.__import__ = restricted_import
                """
            )
            code = blocking_code + '\n' + code

        engine = await Config.get('code_interpreter.engine', 'pyodide')
        if engine == 'pyodide':
            # Execute via frontend pyodide using bidirectional event call
            if __event_call__ is None:
                return JSONCodec.dumps(
                    {'error': 'Event call not available. WebSocket connection required for pyodide execution.'}
                )

            output = await __event_call__(
                {
                    'type': 'execute:python',
                    'data': {
                        'id': str(uuid4()),
                        'code': code,
                        'session_id': (__metadata__.get('session_id') if __metadata__ else None),
                        'files': (__metadata__.get('files', []) if __metadata__ else []),
                    },
                }
            )

            # Parse the output - pyodide returns dict with stdout, stderr, result
            if isinstance(output, dict):
                # Handle error responses from event_caller (e.g. session disconnected, timeout)
                if output.get('error') and not output.get('stdout') and not output.get('result'):
                    stderr = output['error']
                    stdout = ''
                    result = ''
                else:
                    stdout = output.get('stdout', '')
                    stderr = output.get('stderr', '')
                    result = output.get('result', '')
            else:
                stdout = ''
                stderr = ''
                result = str(output) if output else ''

        elif engine == 'jupyter':
            from open_webui.utils.code_interpreter import execute_code_jupyter

            jupyter_auth = await Config.get('code_interpreter.jupyter.auth')

            output = await execute_code_jupyter(
                await Config.get('code_interpreter.jupyter.url'),
                code,
                (await Config.get('code_interpreter.jupyter.auth_token') if jupyter_auth == 'token' else None),
                (await Config.get('code_interpreter.jupyter.auth_password') if jupyter_auth == 'password' else None),
                await Config.get('code_interpreter.jupyter.timeout'),
            )

            stdout = output.get('stdout', '')
            stderr = output.get('stderr', '')
            result = output.get('result', '')

        else:
            return JSONCodec.dumps({'error': f'Unknown code interpreter engine: {engine}'})

        # Handle image outputs (base64 encoded) - replace with uploaded URLs
        # Get actual user object for image upload (upload_image requires user.id attribute)
        if __user__ and __user__.get('id'):
            from open_webui.models.users import Users
            from open_webui.utils.files import get_image_url_from_base64

            user = await Users.get_user_by_id(__user__['id'])

            # Extract and upload images from stdout
            if stdout and isinstance(stdout, str):
                stdout_lines = stdout.split('\n')
                for idx, line in enumerate(stdout_lines):
                    if 'data:image/png;base64' in line:
                        image_url = await get_image_url_from_base64(
                            __request__,
                            line,
                            __metadata__ or {},
                            user,
                        )
                        if image_url:
                            stdout_lines[idx] = f'![Output Image]({image_url})'
                stdout = '\n'.join(stdout_lines)

            # Extract and upload images from result
            if result and isinstance(result, str):
                result_lines = result.split('\n')
                for idx, line in enumerate(result_lines):
                    if 'data:image/png;base64' in line:
                        image_url = await get_image_url_from_base64(
                            __request__,
                            line,
                            __metadata__ or {},
                            user,
                        )
                        if image_url:
                            result_lines[idx] = f'![Output Image]({image_url})'
                result = '\n'.join(result_lines)

        response = {
            'status': 'success',
            'stdout': stdout,
            'stderr': stderr,
            'result': result,
        }

        return JSONCodec.dumps(response, ensure_ascii=False)
    except Exception as e:
        log.exception(f'execute_code error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# =============================================================================
# MEMORY TOOLS
# =============================================================================


async def list_memory_paths(
    query: str = '',
    count: int = 100,
    type: str = 'all',
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    List saved memory paths to find existing memory groups before writing or moving memories.

    :param query: Optional query to filter memory paths or contents
    :param count: Maximum number of paths to return
    :param type: "user", "context", or "all"
    :return: JSON with memory paths, counts, children, and update times
    """
    try:
        user = UserModel(**__user__) if __user__ else None
        result = await _list_memory_paths(
            ListMemoryPathsForm(
                query=query or None,
                type=type if type in {'user', 'context', 'all'} else 'all',
                limit=count,
            ),
            user,
        )
        return JSONCodec.dumps(result, ensure_ascii=False)
    except Exception as e:
        log.exception(f'list_memory_paths error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def read_memory_path(
    path: str,
    count: int = 50,
    type: str = 'all',
    include_children: bool = True,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Read saved memories at a memory path, including nearby parent and child paths.

    :param path: Memory path to read
    :param count: Maximum number of memories to return
    :param type: "user", "context", or "all"
    :param include_children: Include memories under child paths
    :return: JSON with parent paths, child paths, and memories at the path
    """
    try:
        user = UserModel(**__user__) if __user__ else None
        result = await _read_memory_path(
            ReadMemoryPathForm(
                path=path,
                type=type if type in {'user', 'context', 'all'} else 'all',
                include_children=include_children,
                limit=count,
            ),
            user,
        )
        return JSONCodec.dumps(result, ensure_ascii=False)
    except Exception as e:
        log.exception(f'read_memory_path error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def search_memories(
    query: str = '',
    count: int = 5,
    type: str = 'all',
    path: Optional[str] = None,
    memory_id: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Search or browse saved memories by content, path, type, or memory ID.

    :param query: Optional query to search memory content and path
    :param count: Number of memories to return (default 5)
    :param type: "user", "context", or "all"
    :param path: Optional memory path to search around
    :param memory_id: Optional exact memory ID to read
    :return: JSON with matching memories and their dates
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        user = UserModel(**__user__) if __user__ else None

        memories = await _search_memories(
            SearchMemoriesForm(
                query=query or None,
                type=type if type in {'user', 'context', 'all'} else 'all',
                path=path,
                memory_id=memory_id,
                limit=count,
            ),
            user,
        )

        if not memories:
            return JSONCodec.dumps([])

        return JSONCodec.dumps(
            [
                {
                    'id': memory.id,
                    'type': memory.type,
                    'path': memory.path,
                    'content': memory.content,
                    'created_at': time.strftime('%Y-%m-%d', time.localtime(memory.created_at)),
                    'updated_at': time.strftime('%Y-%m-%d', time.localtime(memory.updated_at)),
                }
                for memory in memories
            ],
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'search_memories error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def add_memory(
    content: str,
    type: str = 'user',
    path: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Save enduring information that can improve future chats.

    Save stable preferences, goals, projects, relationships, habits, and standing instructions.
    Do not save one-off activity, meals, routine daily events, temporary mood, or other short-lived details
    unless the user explicitly asks you to remember them.

    :param content: The memory content to store
    :param type: Use "user" for facts/preferences about the user, or "context" for other durable context
    :param path: Optional stable memory address for grouping related memories
    :return: Confirmation that the memory was stored
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        user = UserModel(**__user__) if __user__ else None

        memory = await _add_memory(
            __request__,
            AddMemoryForm(content=content, type=Memories.normalize_memory_type(type), path=path),
            user,
        )

        return JSONCodec.dumps(
            {'status': 'success', 'id': memory.id, 'type': memory.type, 'path': memory.path},
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'add_memory error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def update_memory(
    operations: list[dict],
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Apply a batch of memory changes after learning enduring information.

    Use type "user" for facts, preferences, or instructions about the user.
    Use type "context" for other durable context that may help future chats.
    Do not save one-off activity, meals, routine daily events, temporary mood, or other short-lived details
    unless the user explicitly asks you to remember them.
    Path is optional. Use it as a stable memory address to group related memories.
    Prefer an existing path from list_memory_paths when one fits.
    Leave path empty when no useful grouping is clear.

    Operation shapes:
    - {"action": "add", "content": "...", "type": "user"|"context", "path": "..."}
    - {"action": "replace", "id": "...", "content": "...", "type": "user"|"context", "path": "..."}
    - {"action": "move", "id": "...", "path": "..."}
    - {"action": "remove", "id": "..."}

    :param operations: Memory operations to apply in one request
    :return: JSON with operation results
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        user = UserModel(**__user__) if __user__ else None
        operation_results = await _update_memories(
            __request__,
            UpdateMemoriesForm(operations=operations),
            user,
        )
        return JSONCodec.dumps(operation_results, ensure_ascii=False)
    except Exception as e:
        log.exception(f'update_memory error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def replace_memory_content(
    memory_id: str,
    content: str,
    type: Optional[str] = None,
    path: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Update an existing saved memory by its ID when its content needs correction.

    :param memory_id: The ID of the memory to update
    :param content: The new content for the memory
    :param type: Optional "user" or "context" type for the updated memory
    :param path: Optional stable memory address for grouping related memories
    :return: Confirmation that the memory was updated
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        user = UserModel(**__user__) if __user__ else None

        memory = await update_memory_by_id(
            memory_id=memory_id,
            request=__request__,
            form_data=MemoryUpdateModel(
                content=content,
                type=Memories.normalize_memory_type(type) if type else None,
                path=path,
            ),
            user=user,
        )

        return JSONCodec.dumps(
            {
                'status': 'success',
                'id': memory.id,
                'type': memory.type,
                'path': memory.path,
                'content': memory.content,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'replace_memory_content error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def delete_memory(
    memory_id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Delete a saved memory by its ID.

    :param memory_id: The ID of the memory to delete
    :return: Confirmation that the memory was deleted
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        user = UserModel(**__user__) if __user__ else None

        result = await Memories.delete_memory_by_id_and_user_id(memory_id, user.id)

        if result:
            await ASYNC_VECTOR_DB_CLIENT.delete(collection_name=f'user-memory-{user.id}', ids=[memory_id])
            return JSONCodec.dumps(
                {'status': 'success', 'message': f'Memory {memory_id} deleted'},
                ensure_ascii=False,
            )
        else:
            return JSONCodec.dumps({'error': 'Memory not found or access denied'})
    except Exception as e:
        log.exception(f'delete_memory error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def list_memories(
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    List all stored memories for the user, including IDs and timestamps.

    :return: JSON list of all memories with id, content, and dates
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        user = UserModel(**__user__) if __user__ else None

        memories = await Memories.get_memories_by_user_id(user.id)

        if memories:
            memory_rows = [
                {
                    'id': m.id,
                    'type': m.type,
                    'path': m.path,
                    'content': m.content,
                    'created_at': time.strftime('%Y-%m-%d %H:%M', time.localtime(m.created_at)),
                    'updated_at': time.strftime('%Y-%m-%d %H:%M', time.localtime(m.updated_at)),
                }
                for m in memories
            ]
            return JSONCodec.dumps(memory_rows, ensure_ascii=False)
        else:
            return JSONCodec.dumps([])
    except Exception as e:
        log.exception(f'list_memories error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# =============================================================================
# NOTES TOOLS
# =============================================================================


async def search_notes(
    query: str,
    count: int = 5,
    start_timestamp: Optional[int] = None,
    end_timestamp: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Search the user's saved notes by title and content.

    :param query: The search query to find matching notes
    :param count: Maximum number of results to return (default: 5)
    :param start_timestamp: Only include notes updated after this Unix timestamp (seconds)
    :param end_timestamp: Only include notes updated before this Unix timestamp (seconds)
    :return: JSON with matching notes containing id, title, and content snippet
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        user_id = __user__.get('id')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]

        result = await Notes.search_notes(
            user_id=user_id,
            filter={
                'query': query,
                'user_id': user_id,
                'group_ids': user_group_ids,
                'permission': 'read',
            },
            skip=0,
            limit=count * 3,  # Fetch more for filtering
        )

        # Convert timestamps to nanoseconds for comparison
        start_ts = start_timestamp * 1_000_000_000 if start_timestamp else None
        end_ts = end_timestamp * 1_000_000_000 if end_timestamp else None

        notes = []
        for note in result.items:
            # Apply date filters (updated_at is in nanoseconds)
            if start_ts and note.updated_at < start_ts:
                continue
            if end_ts and note.updated_at > end_ts:
                continue

            # Extract a snippet from the markdown content
            content_snippet = ''
            if note.data and note.data.get('content', {}).get('md'):
                md_content = note.data['content']['md']
                content_lower = md_content.lower()

                # Find the first matching word to center the snippet around.
                search_words = query.lower().split()
                match_pos = -1
                match_len = len(query)
                for word in search_words:
                    found_pos = content_lower.find(word)
                    if found_pos != -1:
                        match_pos = found_pos
                        match_len = len(word)
                        break

                if match_pos != -1:
                    snippet_start = max(0, match_pos - 50)
                    snippet_end = min(len(md_content), match_pos + match_len + 100)
                    content_snippet = (
                        ('...' if snippet_start > 0 else '')
                        + md_content[snippet_start:snippet_end]
                        + ('...' if snippet_end < len(md_content) else '')
                    )
                else:
                    content_snippet = md_content[:150] + ('...' if len(md_content) > 150 else '')

            notes.append(
                {
                    'id': note.id,
                    'title': note.title,
                    'snippet': content_snippet,
                    'updated_at': note.updated_at,
                }
            )

            if len(notes) >= count:
                break

        return JSONCodec.dumps(notes, ensure_ascii=False)
    except Exception as e:
        log.exception(f'search_notes error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def view_note(
    note_id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Get the full content of a note by its ID.

    :param note_id: The ID of the note to retrieve
    :return: JSON with the note's id, title, and full markdown content
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        note = await Notes.get_note_by_id(note_id)

        if not note:
            return JSONCodec.dumps({'error': 'Note not found'})

        # Check access permission
        user_id = __user__.get('id')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]

        from open_webui.models.access_grants import AccessGrants

        if (
            __user__.get('role') != 'admin'
            and note.user_id != user_id
            and not await AccessGrants.has_access(
                user_id=user_id,
                resource_type='note',
                resource_id=note.id,
                permission='read',
                user_group_ids=set(user_group_ids),
            )
        ):
            return JSONCodec.dumps({'error': 'Access denied'})

        # Extract markdown content
        content = ''
        if note.data and note.data.get('content', {}).get('md'):
            content = note.data['content']['md']

        return JSONCodec.dumps(
            {
                'id': note.id,
                'title': note.title,
                'content': content,
                'updated_at': note.updated_at,
                'created_at': note.created_at,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'view_note error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def write_note(
    title: str,
    content: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Create a new note with the given title and content.

    :param title: The title of the new note
    :param content: The markdown content for the note
    :return: JSON with success status and new note id
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.notes import NoteForm

        user_id = __user__.get('id')

        form = NoteForm(
            title=title,
            data={'content': {'md': content}},
            access_grants=[],  # Private by default - only owner can access
        )

        new_note = await Notes.insert_new_note(user_id, form)

        if not new_note:
            return JSONCodec.dumps({'error': 'Failed to create note'})

        return JSONCodec.dumps(
            {
                'status': 'success',
                'id': new_note.id,
                'title': new_note.title,
                'created_at': new_note.created_at,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'write_note error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def replace_note_content(
    note_id: str,
    content: Optional[str] = None,
    operations: Optional[list[dict]] = None,
    title: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Update an existing note by replacing the whole markdown content or applying range operations.

    Prefer "replace_range" when only part of the note changes.
    A "replace" operation must be the only operation in the request.
    start and end are 0-indexed character offsets into the markdown content from view_note.
    end is exclusive.
    Offsets never shift as operations are applied.
    Ranges must not overlap.
    expected is optional. When set, the request is rejected if the range's current text does not match it.

    :param note_id: The ID of the note to update
    :param content: The new markdown content for a whole-note update
    :param operations: Optional note operations:
    - {"action": "replace", "content": "..."}
    - {"action": "replace_range", "start": 0, "end": 10, "content": "...", "expected": "..."}
    :param title: Optional new title for the note
    :return: JSON with success status and updated note info
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.notes import NoteUpdateForm

        note = await Notes.get_note_by_id(note_id)

        if not note:
            return JSONCodec.dumps({'error': 'Note not found', 'code': 'not_found'})

        user_id = __user__.get('id')
        if __user__.get('role') != 'admin' and not await _has_write_access_to_note(note, user_id):
            return JSONCodec.dumps({'error': 'Write access denied', 'code': 'write_access_denied'})

        current_content = ((note.data or {}).get('content') or {}).get('md') or ''
        applied_operation_count = 0
        if operations is not None:
            if not isinstance(operations, list) or len(operations) == 0:
                return JSONCodec.dumps({'error': 'operations must be a non-empty list', 'code': 'invalid_operations'})

            range_operations = []
            for idx, operation in enumerate(operations):
                if not isinstance(operation, dict):
                    return JSONCodec.dumps(
                        {'error': 'each operation must be an object', 'code': 'invalid_operation', 'index': idx}
                    )

                action = operation.get('action')
                replacement = operation.get('content')

                if action == 'replace':
                    if len(operations) != 1:
                        return JSONCodec.dumps(
                            {
                                'error': 'replace operation must be the only operation',
                                'code': 'invalid_operations',
                                'index': idx,
                            }
                        )
                    if not isinstance(replacement, str):
                        return JSONCodec.dumps(
                            {
                                'error': 'replace operation content must be a string',
                                'code': 'invalid_content',
                                'index': idx,
                            }
                        )
                    content = replacement
                    applied_operation_count = 1
                    break

                if action != 'replace_range':
                    return JSONCodec.dumps(
                        {'error': 'unknown operation action', 'code': 'invalid_action', 'index': idx, 'action': action}
                    )

                start = operation.get('start')
                end = operation.get('end')
                expected = operation.get('expected')
                if not isinstance(start, int) or not isinstance(end, int):
                    return JSONCodec.dumps(
                        {'error': 'operation start and end must be integers', 'code': 'invalid_range', 'index': idx}
                    )
                if not isinstance(replacement, str):
                    return JSONCodec.dumps(
                        {'error': 'operation content must be a string', 'code': 'invalid_content', 'index': idx}
                    )
                if start < 0 or end < start or end > len(current_content):
                    return JSONCodec.dumps(
                        {'error': 'operation range is out of bounds', 'code': 'range_out_of_bounds', 'index': idx}
                    )
                if expected is not None and current_content[start:end] != expected:
                    return JSONCodec.dumps(
                        {
                            'error': 'operation expected text does not match current content',
                            'code': 'expected_mismatch',
                            'index': idx,
                        }
                    )

                range_operations.append({'start': start, 'end': end, 'content': replacement})

            range_operations.sort(key=lambda operation: operation['start'])
            previous_end = 0
            for idx, operation in enumerate(range_operations):
                if operation['start'] < previous_end:
                    return JSONCodec.dumps(
                        {'error': 'operation ranges must not overlap', 'code': 'overlapping_operations', 'index': idx}
                    )
                previous_end = operation['end']

            if range_operations:
                content = current_content
                for operation in reversed(range_operations):
                    content = content[: operation['start']] + operation['content'] + content[operation['end'] :]
                applied_operation_count = len(range_operations)
        elif content is None:
            return JSONCodec.dumps({'error': 'content or operations is required', 'code': 'content_required'})

        try:
            await stop_item_tasks(__request__.app.state.redis, f'note:{note_id}')
        except Exception:
            pass

        update_data = {
            'data': {
                **(note.data or {}),
                'content': {
                    **((note.data or {}).get('content') or {}),
                    'json': None,
                    'html': '',
                    'md': content,
                },
            }
        }
        if title:
            update_data['title'] = title

        form = NoteUpdateForm(**update_data)
        updated_note = await Notes.update_note_by_id(note_id, form)

        if not updated_note:
            return JSONCodec.dumps({'error': 'Failed to update note', 'code': 'update_failed'})

        await _emit_note_updated(__request__, __user__, updated_note)

        return JSONCodec.dumps(
            {
                'status': 'success',
                'id': updated_note.id,
                'title': updated_note.title,
                'updated_at': updated_note.updated_at,
                'applied_operation_count': applied_operation_count,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'replace_note_content error: {e}')
        return JSONCodec.dumps({'error': str(e), 'code': 'unexpected_error'})


# =============================================================================
# CHATS TOOLS
# =============================================================================


async def search_chats(
    query: str,
    count: int = 5,
    start_timestamp: Optional[int] = None,
    end_timestamp: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
    __chat_id__: str = None,
) -> str:
    """
    Search the user's previous chat conversations by title and message content,
    excluding the current chat. Helpful for finding details from earlier
    conversations when they are not already visible in the current context.
    Exact phrase matches are preferred, and descriptive keyword queries are
    supported.

    :param query: Exact phrase or descriptive keyword query to find matching previous chats
    :param count: Maximum number of results to return (default: 5)
    :param start_timestamp: Only include chats updated after this Unix timestamp (seconds)
    :param end_timestamp: Only include chats updated before this Unix timestamp (seconds)
    :return: JSON with matching chats containing id, title, updated_at, and content snippet
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        user_id = __user__.get('id')

        chats = await Chats.get_chats_by_user_id_and_search_text(
            user_id=user_id,
            search_text=query,
            include_archived=False,
            skip=0,
            limit=count * 3,  # Fetch more for filtering
        )

        results = []
        for chat in chats:
            # Skip the current chat to avoid showing it in search results
            if __chat_id__ and chat.id == __chat_id__:
                continue

            # Apply date filters (updated_at is in seconds)
            if start_timestamp and chat.updated_at < start_timestamp:
                continue
            if end_timestamp and chat.updated_at > end_timestamp:
                continue

            # Find a matching message snippet
            snippet = ''
            messages = (getattr(chat, 'chat', None) or {}).get('history', {}).get('messages', {})
            if not messages:
                messages = (getattr(chat, 'chat', None) or {}).get('messages', {}) or {}
            if isinstance(messages, list):
                messages = {str(idx): message for idx, message in enumerate(messages)}

            lower_query = chat_search_content_query(query)
            needles = list(dict.fromkeys([lower_query, *chat_search_terms(lower_query)])) if lower_query else []

            for needle in needles:
                for msg_id, msg in messages.items():
                    content = msg.get('content', '') if isinstance(msg, dict) else ''
                    if isinstance(content, str) and needle in content.lower():
                        idx = content.lower().find(needle)
                        start = max(0, idx - 50)
                        end = min(len(content), idx + len(needle) + 100)
                        snippet = (
                            ('...' if start > 0 else '') + content[start:end] + ('...' if end < len(content) else '')
                        )
                        break
                if snippet:
                    break

            title = chat.title or ''
            if not snippet and any(needle in title.lower() for needle in needles):
                snippet = f'Title match: {title}'

            results.append(
                {
                    'id': chat.id,
                    'title': chat.title,
                    'snippet': snippet,
                    'updated_at': chat.updated_at,
                }
            )

            if len(results) >= count:
                break

        return JSONCodec.dumps(results, ensure_ascii=False)
    except Exception as e:
        log.exception(f'search_chats error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def view_chat(
    chat_id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Get the full conversation history of a chat by its ID after a relevant
    previous chat has been identified.

    :param chat_id: The ID of the chat to retrieve
    :return: JSON with the chat's id, title, and messages
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        user_id = __user__.get('id')

        chat = await Chats.get_chat_by_id_and_user_id(chat_id, user_id)

        if not chat:
            return JSONCodec.dumps({'error': 'Chat not found or access denied'})

        # Extract messages from history
        messages = []
        history = chat.chat.get('history', {})
        msg_dict = history.get('messages', {})

        # Build message chain from currentId
        current_id = history.get('currentId')
        visited = set()

        while current_id and current_id not in visited:
            visited.add(current_id)
            msg = msg_dict.get(current_id)
            if msg:
                messages.append(
                    {
                        'role': msg.get('role', ''),
                        'content': msg.get('content', ''),
                    }
                )
            current_id = msg.get('parentId') if msg else None

        # Reverse to get chronological order
        messages.reverse()

        return JSONCodec.dumps(
            {
                'id': chat.id,
                'title': chat.title,
                'messages': messages,
                'updated_at': chat.updated_at,
                'created_at': chat.created_at,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'view_chat error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# =============================================================================
# SUB-AGENT TOOL
# =============================================================================


async def delegate_task(
    task: str,
    context: str = '',
    file_ids: list[str] | None = None,
    background: bool = False,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
    __chat_id__: str = None,
    __message_id__: str = None,
) -> str:
    """
    Delegate focused work to a parallel sub-agent using the current model and tools.

    :param task: The specific task for the sub-agent to complete
    :param context: Relevant context, decisions, or file paths for the task
    :param file_ids: Attached file IDs the sub-agent needs. Use this for images or files;
        do not put file IDs only in context.
    :param background: Return immediately and continue this chat when the sub-agent finishes
    :return: Foreground result text, or a JSON dispatch handle for background work
    """
    if __request__ is None:
        return 'Error: request context not available.'
    if getattr(__request__.state, 'internal', False) is True:
        return 'Error: sub-agents cannot delegate recursively.'

    from open_webui.utils.subagents import delegate

    return await delegate(
        task,
        context,
        background,
        file_ids=file_ids,
        request=__request__,
        user_data=__user__ or {},
        metadata=__metadata__ or {},
        parent_chat_id=__chat_id__ or '',
        parent_message_id=__message_id__,
    )


async def timer(
    prompt: str,
    at: str,
    cancel_on: list[Literal['chat.read', 'chat.user_message']] | None = None,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
    __chat_id__: str = None,
    __message_id__: str = None,
) -> str:
    """
    Set a one-shot timer for this chat.

    :param prompt: The prompt to send back into this chat when the timer fires
    :param at: Relative time like 10s, 5m, 1h, 2d, or a timezone-aware RFC 3339 timestamp
    :param cancel_on: Optional events that cancel the timer before it fires
    :return: JSON status with the scheduled time, or an error string
    """
    if __request__ is None:
        return 'Error: request context not available.'
    if getattr(__request__.state, 'internal', False) is True:
        return 'Error: timers cannot be set from internal chats.'

    from open_webui.utils.timers import create_timer

    return await create_timer(
        prompt=prompt,
        at=at,
        cancel_on=cancel_on,
        request=__request__,
        user_data=__user__ or {},
        metadata=__metadata__ or {},
        parent_chat_id=__chat_id__ or '',
        parent_message_id=__message_id__,
    )


# =============================================================================
# CHANNELS TOOLS
# =============================================================================


async def search_channels(
    query: str,
    count: int = 5,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Search channels by name and description to find accessible team spaces.

    :param query: The search query to find matching channels
    :param count: Maximum number of results to return (default: 5)
    :return: JSON with matching channels containing id, name, description, and type
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        user_id = __user__.get('id')

        # Get all channels the user has access to
        all_channels = await Channels.get_channels_by_user_id(user_id)

        # Filter by query
        lower_query = query.lower()
        matching_channels = []

        for channel in all_channels:
            name_match = lower_query in channel.name.lower() if channel.name else False
            desc_match = lower_query in (channel.description or '').lower()

            if name_match or desc_match:
                matching_channels.append(
                    {
                        'id': channel.id,
                        'name': channel.name,
                        'description': channel.description or '',
                        'type': channel.type or 'public',
                    }
                )

            if len(matching_channels) >= count:
                break

        return JSONCodec.dumps(matching_channels, ensure_ascii=False)
    except Exception as e:
        log.exception(f'search_channels error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def search_channel_messages(
    query: str,
    count: int = 10,
    start_timestamp: Optional[int] = None,
    end_timestamp: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Search messages in channels the user is a member of, including thread replies.
    Helpful for finding prior team/channel discussion.

    :param query: The search query to find matching messages
    :param count: Maximum number of results to return (default: 10)
    :param start_timestamp: Only include messages created after this Unix timestamp (seconds)
    :param end_timestamp: Only include messages created before this Unix timestamp (seconds)
    :return: JSON with matching messages containing channel info, message content, and thread context
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        user_id = __user__.get('id')

        # Get all channels the user has access to
        user_channels = await Channels.get_channels_by_user_id(user_id)
        channel_ids = [c.id for c in user_channels]
        channel_map = {c.id: c for c in user_channels}

        if not channel_ids:
            return JSONCodec.dumps([])

        # Convert timestamps to nanoseconds (Message.created_at is in nanoseconds)
        start_ts = start_timestamp * 1_000_000_000 if start_timestamp else None
        end_ts = end_timestamp * 1_000_000_000 if end_timestamp else None

        # Search messages using the model method
        matching_messages = await Messages.search_messages_by_channel_ids(
            channel_ids=channel_ids,
            query=query,
            start_timestamp=start_ts,
            end_timestamp=end_ts,
            limit=count,
        )

        results = []
        for msg in matching_messages:
            channel = channel_map.get(msg.channel_id)

            # Extract snippet around the match
            content = msg.content or ''
            lower_query = query.lower()
            idx = content.lower().find(lower_query)
            if idx != -1:
                start = max(0, idx - 50)
                end = min(len(content), idx + len(query) + 100)
                snippet = ('...' if start > 0 else '') + content[start:end] + ('...' if end < len(content) else '')
            else:
                snippet = content[:150] + ('...' if len(content) > 150 else '')

            results.append(
                {
                    'channel_id': msg.channel_id,
                    'channel_name': channel.name if channel else 'Unknown',
                    'message_id': msg.id,
                    'content_snippet': snippet,
                    'is_thread_reply': msg.parent_id is not None,
                    'parent_id': msg.parent_id,
                    'created_at': msg.created_at,
                }
            )

        return JSONCodec.dumps(results, ensure_ascii=False)
    except Exception as e:
        log.exception(f'search_channel_messages error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def view_channel_message(
    message_id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Get the full content of a channel message by its ID, including thread replies.

    :param message_id: The ID of the message to retrieve
    :return: JSON with the message content, channel info, and thread replies if any
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        user_id = __user__.get('id')

        message = await Messages.get_message_by_id(message_id)

        if not message:
            return JSONCodec.dumps({'error': 'Message not found'})

        # Verify user has access to the channel
        channel = await Channels.get_channel_by_id(message.channel_id)
        if not channel:
            return JSONCodec.dumps({'error': 'Channel not found'})

        # Check if user has access to the channel
        user_channels = await Channels.get_channels_by_user_id(user_id)
        channel_ids = [c.id for c in user_channels]

        if message.channel_id not in channel_ids:
            return JSONCodec.dumps({'error': 'Access denied'})

        # Build response with thread information
        result = {
            'id': message.id,
            'channel_id': message.channel_id,
            'channel_name': channel.name,
            'content': message.content,
            'user_id': message.user_id,
            'is_thread_reply': message.parent_id is not None,
            'parent_id': message.parent_id,
            'reply_count': message.reply_count,
            'created_at': message.created_at,
            'updated_at': message.updated_at,
        }

        # Include user info if available
        if message.user:
            result['user_name'] = message.user.name

        return JSONCodec.dumps(result, ensure_ascii=False)
    except Exception as e:
        log.exception(f'view_channel_message error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def view_channel_thread(
    parent_message_id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Get all messages in a channel thread, including the parent message and all replies.

    :param parent_message_id: The ID of the parent message that started the thread
    :return: JSON with the parent message and all thread replies in chronological order
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        user_id = __user__.get('id')

        # Get the parent message
        parent_message = await Messages.get_message_by_id(parent_message_id)

        if not parent_message:
            return JSONCodec.dumps({'error': 'Message not found'})

        # Verify user has access to the channel
        channel = await Channels.get_channel_by_id(parent_message.channel_id)
        if not channel:
            return JSONCodec.dumps({'error': 'Channel not found'})

        user_channels = await Channels.get_channels_by_user_id(user_id)
        channel_ids = [c.id for c in user_channels]

        if parent_message.channel_id not in channel_ids:
            return JSONCodec.dumps({'error': 'Access denied'})

        # Get all thread replies
        thread_replies = await Messages.get_thread_replies_by_message_id(parent_message_id)

        # Build the response
        messages = []

        # Add parent message first
        messages.append(
            {
                'id': parent_message.id,
                'content': parent_message.content,
                'user_id': parent_message.user_id,
                'user_name': parent_message.user.name if parent_message.user else None,
                'is_parent': True,
                'created_at': parent_message.created_at,
            }
        )

        # Add thread replies (reverse to get chronological order)
        for reply in reversed(thread_replies):
            messages.append(
                {
                    'id': reply.id,
                    'content': reply.content,
                    'user_id': reply.user_id,
                    'user_name': reply.user.name if reply.user else None,
                    'is_parent': False,
                    'reply_to_id': reply.reply_to_id,
                    'created_at': reply.created_at,
                }
            )

        return JSONCodec.dumps(
            {
                'channel_id': parent_message.channel_id,
                'channel_name': channel.name,
                'thread_id': parent_message_id,
                'message_count': len(messages),
                'messages': messages,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'view_channel_thread error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# =============================================================================
# KNOWLEDGE BASE TOOLS
# =============================================================================


async def list_knowledge_bases(
    count: int = 10,
    skip: int = 0,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    List the user's accessible knowledge bases so a relevant internal source
    can be chosen.

    :param count: Maximum number of KBs to return (default: 10)
    :param skip: Number of results to skip for pagination (default: 0)
    :return: JSON with KBs containing id, name, description, and file_count
    """
    return await search_knowledge_bases('', count=count, skip=skip, __request__=__request__, __user__=__user__)


async def search_knowledge_bases(
    query: str = '',
    count: int = 5,
    skip: int = 0,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Find accessible knowledge bases by name/description; an empty query lists them.
    Use only when the source is unknown; do not rediscover an already selected KB.

    :param query: Name/description keywords; omit to list accessible knowledge bases
    :param count: Maximum number of results to return (default: 5)
    :param skip: Number of results to skip for pagination (default: 0)
    :return: JSON with matching KBs containing id, name, description, and file_count
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.knowledge import Knowledges

        user_id = __user__.get('id')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]

        result = await Knowledges.search_knowledge_bases(
            user_id,
            filter={
                'query': query,
                'user_id': user_id,
                'group_ids': user_group_ids,
            },
            skip=skip,
            limit=count,
        )

        knowledge_bases = []
        for knowledge_base in result.items:
            files = await Knowledges.get_file_metadatas_by_id(knowledge_base.id)
            file_count = len(files) if files else 0

            knowledge_bases.append(
                {
                    'id': knowledge_base.id,
                    'name': knowledge_base.name,
                    'description': knowledge_base.description or '',
                    'file_count': file_count,
                    'updated_at': knowledge_base.updated_at,
                }
            )

        return JSONCodec.dumps(knowledge_bases, ensure_ascii=False)
    except Exception as e:
        log.exception(f'search_knowledge_bases error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def search_knowledge_files(
    query: str = '',
    knowledge_id: Optional[str] = None,
    count: int = 5,
    skip: int = 0,
    __request__: Request = None,
    __user__: dict = None,
    __model_knowledge__: Optional[list[dict]] = None,
    __metadata__: dict = None,
    __files__: Optional[list[dict]] = None,
    __knowledge_enabled__: bool = True,
) -> str:
    """
    Find or list files by filename within selected knowledge and chat attachments.
    An empty query lists files. With no selected source, browse authorized KB file metadata.
    Use when a filename is known but its file ID is not. This does not search file contents:
    use grep_knowledge_files for known words or clauses, query_knowledge_files for meaning.
    With a known file ID and location, read directly instead of searching again.

    :param query: Filename keywords; omit to list files, never file contents
    :param knowledge_id: Optional KB id to limit search to a specific knowledge base
    :param count: Maximum number of results to return (default: 5)
    :param skip: Number of results to skip for pagination (default: 0)
    :return: JSON with matching files containing id, filename, and updated_at
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.access_grants import AccessGrants
        from open_webui.models.files import Files
        from open_webui.models.knowledge import Knowledges

        user_id = __user__.get('id')
        user_role = __user__.get('role', 'user')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]

        entries = knowledge_entries(
            __model_knowledge__ or (),
            __metadata__,
            __files__,
            knowledge_enabled=__knowledge_enabled__,
            native_pdf=is_native_pdf_attachment,
        )
        count, skip = min(200, max(1, int(count))), max(0, int(skip))
        if knowledge_id and not __knowledge_enabled__:
            return JSONCodec.dumps({'error': 'Knowledge tools are disabled'})

        # Scope before querying; metadata-only browsing must not search unrelated attachments.
        if entries:
            attached_kb_ids = set()
            attached_file_ids = set()

            for item in entries:
                item_type = item.get('type')
                item_id = item.get('id')
                if item_type == 'collection':
                    attached_kb_ids.add(item_id)
                elif item_type == 'file':
                    attached_file_ids.add(item_id)

            # If knowledge_id specified, verify it's in the attached set
            if knowledge_id:
                if knowledge_id not in attached_kb_ids:
                    return JSONCodec.dumps({'error': f'Knowledge base {knowledge_id} is not attached to this model'})
                attached_kb_ids = {knowledge_id}

            all_files = []

            # Search within attached KBs
            for kb_id in sorted(attached_kb_ids):
                knowledge = await Knowledges.get_knowledge_by_id(kb_id)
                if not knowledge:
                    continue

                if not (
                    user_role == 'admin'
                    or knowledge.user_id == user_id
                    or await AccessGrants.has_access(
                        user_id=user_id,
                        resource_type='knowledge',
                        resource_id=knowledge.id,
                        permission='read',
                        user_group_ids=set(user_group_ids),
                    )
                ):
                    continue

                result = await Knowledges.search_files_by_id(
                    knowledge_id=kb_id,
                    user_id=user_id,
                    filter={'query': query},
                    skip=0,
                    limit=count + skip,
                )

                for file in result.items:
                    all_files.append(
                        {
                            'id': file.id,
                            'filename': file.filename,
                            'knowledge_id': knowledge.id,
                            'knowledge_name': knowledge.name,
                            'updated_at': file.updated_at,
                        }
                    )

            # Search within directly attached files (filename match)
            if not knowledge_id and attached_file_ids:
                query_lower = query.lower() if query else ''
                for file_id in sorted(attached_file_ids):
                    file = await Files.get_file_by_id(file_id)
                    if (
                        file
                        and await _has_read_access_to_file(file, __user__)
                        and (not query_lower or query_lower in file.filename.lower())
                    ):
                        all_files.append(
                            {
                                'id': file.id,
                                'filename': file.filename,
                                'updated_at': file.updated_at,
                            }
                        )

            # Apply pagination across combined results
            all_files = list({row['id']: row for row in all_files}.values())
            all_files = sorted(all_files, key=lambda row: (-(row['updated_at'] or 0), row['id']))[skip : skip + count]
            return JSONCodec.dumps(all_files, ensure_ascii=False)

        if not __knowledge_enabled__:
            return JSONCodec.dumps([])
        # No attached knowledge - search all accessible KBs
        if knowledge_id:
            # search_files_by_id does not enforce knowledge_id ownership; mirror the attached-KB check above.
            knowledge = await Knowledges.get_knowledge_by_id(knowledge_id)
            if not knowledge or not (
                user_role == 'admin'
                or knowledge.user_id == user_id
                or await AccessGrants.has_access(
                    user_id=user_id,
                    resource_type='knowledge',
                    resource_id=knowledge.id,
                    permission='read',
                    user_group_ids=set(user_group_ids),
                )
            ):
                return JSONCodec.dumps({'error': f'Access denied to knowledge base {knowledge_id}'})

            result = await Knowledges.search_files_by_id(
                knowledge_id=knowledge_id,
                user_id=user_id,
                filter={'query': query},
                skip=skip,
                limit=count,
            )
        else:
            result = await Knowledges.search_knowledge_files(
                filter={
                    'query': query,
                    'user_id': user_id,
                    'group_ids': user_group_ids,
                },
                skip=skip,
                limit=count,
            )

        files = []
        for file in result.items:
            file_info = {
                'id': file.id,
                'filename': file.filename,
                'updated_at': file.updated_at,
            }
            if hasattr(file, 'collection') and file.collection:
                file_info['knowledge_id'] = file.collection.get('id', '')
                file_info['knowledge_name'] = file.collection.get('name', '')
            files.append(file_info)

        return JSONCodec.dumps(files, ensure_ascii=False)
    except Exception as e:
        log.exception(f'search_knowledge_files error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def _get_accessible_chat_files(
    files: Optional[list[dict]],
    user: dict,
    file_id: Optional[str] = None,
) -> list[tuple[dict, object]]:
    from open_webui.models.files import Files

    accessible = []
    seen = set()

    for item in files or []:
        if not isinstance(item, dict) or item.get('type', 'file') != 'file':
            continue
        fid = item.get('id') or item.get('url') or ''
        if (
            not isinstance(fid, str)
            or not fid
            or fid in seen
            or fid.startswith(('http://', 'https://', 'data:'))
            or (file_id and fid != file_id)
        ):
            continue
        normalized = {**item, 'id': fid, 'type': 'file'}
        if 'name' not in normalized and item.get('filename'):
            normalized['name'] = item.get('filename')
        seen.add(fid)

        file = await Files.get_file_by_id(fid)
        if file and await _has_read_access_to_file(file, user):
            accessible.append((normalized, file))

    return accessible


def _grep_file_models(
    files_to_search: list,
    pattern: str,
    case_insensitive: bool = False,
    count_only: bool = False,
    *,
    read_tool: str = 'view_knowledge_file',
) -> str:
    from open_webui.tools.knowledge_fs import build_matcher

    matches, err = build_matcher(pattern, case_insensitive)
    if err:
        return JSONCodec.dumps({'error': err})

    # Context is a locator, not a replacement for reading a whole clause/document.
    # Bound both wide searches and a single very long extracted PDF line.
    max_lines = 250
    max_chars = max(1, min(VIEW_FILE_DEFAULT_MAX_CHARS, VIEW_FILE_MAX_CHARS, 10_000))
    max_line_chars = 1_000
    results = []
    total_matches = 0
    selected_matches = 0
    counts = []
    context_truncated = False

    for file in files_to_search:
        content = ''
        if file.data:
            content = file.data.get('content', '')
        if not content:
            continue

        lines = content.split('\n')
        file_matches = 0
        selected_lines = set()

        for i, line in enumerate(lines):
            if matches(line):
                file_matches += 1
                total_matches += 1
                if not count_only and selected_matches < min(KNOWLEDGE_GREP_MAX_MATCHES, max_lines):
                    selected_lines.add(i)
                    selected_matches += 1

        if file_matches > 0 and count_only:
            counts.append(f'{file.id}  {file.filename}: {file_matches}')

        if selected_lines:
            # Merge overlapping two-line windows within this file. Matching lines
            # keep their original colon format; surrounding lines use dashes.
            context_lines = {
                index
                for matched in selected_lines
                for index in range(max(0, matched - 2), min(len(lines), matched + 3))
            }
            for index in sorted(context_lines):
                if len(results) >= max_lines:
                    context_truncated = True
                    break
                is_match = index in selected_lines
                sep = ':' if is_match else '-'
                prefix = f'{file.id}  {file.filename}{sep}{index + 1}{sep} '
                line = lines[index]
                if len(line) > max_line_chars:
                    line = line[:max_line_chars] + ' [line truncated]'
                    context_truncated = True
                results.append((prefix + line, is_match))

    if count_only:
        if not counts:
            return f'No matches for "{pattern}"'
        return '\n'.join(counts) + f'\n[{total_matches} total matches]'

    if not results:
        return f'No matches for "{pattern}"'

    notice = (
        f'[Output truncated: {{shown}} of {total_matches} matches shown; '
        f'narrow file_id/pattern or use {read_tool} with file_id and '
        'start_line/end_line for full text.]'
    )
    # Reserve enough room for an honest notice, including when a row's filename
    # is unusually large. Never silently slice a source row or its identifiers.
    available = max(0, max_chars - len(notice.format(shown=total_matches)) - 1)
    output_lines = []
    shown_matches = 0
    used = 0
    for line, is_match in results:
        size = len(line) + (1 if output_lines else 0)
        if used + size > available:
            context_truncated = True
            break
        output_lines.append(line)
        used += size
        shown_matches += int(is_match)
    output = '\n'.join(output_lines)
    if context_truncated or shown_matches < total_matches:
        output += ('\n' if output else '') + notice.format(shown=shown_matches)
    return output[:max_chars]


async def list_chat_files(
    __request__: Request = None,
    __user__: dict = None,
    __files__: list[dict] = None,
) -> str:
    """
    List files attached to the current chat.

    :return: JSON with attached chat files containing id, filename, content type, size, and updated time when available
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        files = []
        for item, file in await _get_accessible_chat_files(__files__, __user__):
            file_info = {
                'id': file.id,
                'filename': file.filename,
                'name': item.get('name') or file.filename,
                'type': item.get('type', 'file'),
                'updated_at': file.updated_at,
            }
            content_type = item.get('content_type') or (file.meta or {}).get('content_type')
            size = item.get('size') or (file.meta or {}).get('size')
            if content_type:
                file_info['content_type'] = content_type
            if size:
                file_info['size'] = size
            files.append(file_info)

        return JSONCodec.dumps(files, ensure_ascii=False)
    except Exception as e:
        log.exception(f'list_chat_files error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def grep_chat_files(
    pattern: str,
    file_id: Optional[str] = None,
    case_insensitive: bool = False,
    count_only: bool = False,
    __request__: Request = None,
    __user__: dict = None,
    __files__: list[dict] = None,
) -> str:
    """
    Search exact text across files attached to the current chat.
    Pass file_id from the attached_files block to search one file.
    Returns matching lines and up to two nearby lines per side, within an output budget.
    For complete clauses, use view_file with the indicated file ID and start_line/end_line.
    Auto-detected regex uses RE2 syntax; no lookarounds/backreferences, and shorthand classes are ASCII-only.

    :param pattern: The text pattern to search for
    :param file_id: Optional attached file ID to search within a single file
    :param case_insensitive: If true, ignore case when matching
    :param count_only: If true, return only match counts per file
    :return: Matching lines with file IDs, filenames, and line numbers
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    if not pattern or not pattern.strip():
        return JSONCodec.dumps({'error': 'Pattern is required'})

    if isinstance(file_id, str) and file_id.lower() in ('none', 'null', ''):
        file_id = None

    try:
        attached_ids = set()
        for item in __files__ or []:
            if not isinstance(item, dict) or item.get('type', 'file') != 'file':
                continue
            fid = item.get('id') or item.get('url')
            if isinstance(fid, str) and fid and not fid.startswith(('http://', 'https://', 'data:')):
                attached_ids.add(fid)

        if not attached_ids:
            return JSONCodec.dumps({'error': 'No files are attached to this chat'})
        if file_id and file_id not in attached_ids:
            return JSONCodec.dumps({'error': 'File not found'})

        files_to_search = [file for _, file in await _get_accessible_chat_files(__files__, __user__, file_id)]
        if not files_to_search:
            return JSONCodec.dumps({'error': 'No accessible files found'})

        return await asyncio.to_thread(
            _grep_file_models, files_to_search, pattern, case_insensitive, count_only, read_tool='view_file'
        )
    except Exception as e:
        log.exception(f'grep_chat_files error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def query_chat_files(
    query: str,
    file_id: Optional[str] = None,
    count: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
    __files__: list[dict] = None,
) -> str:
    """Compatibility entry for attached-file semantic search through the unified evidence path."""
    if __request__ is None or not __user__:
        return JSONCodec.dumps({'error': 'Authenticated request context is required'})
    if isinstance(file_id, str) and file_id.lower() in ('none', 'null', ''):
        file_id = None
    attached = [item.get('id') or item.get('url') for item in __files__ or [] if isinstance(item, dict)]
    if file_id and file_id not in attached:
        return JSONCodec.dumps({'error': 'File not found'})
    accessible = await _get_accessible_chat_files(__files__, __user__, file_id)
    if not accessible:
        return JSONCodec.dumps({'error': 'No accessible files found'})
    top_k = await Config.get('rag.top_k') or 5
    count = top_k if count is None else max(1, min(int(count), top_k))
    return await query_knowledge_files(
        query,
        count=count,
        file_ids=[file_id] if file_id else None,
        __request__=__request__,
        __user__=__user__,
        __knowledge_enabled__=False,
        __metadata__={'files': [item for item, _ in accessible]},
    )


async def _knowledge_source_files(
    user, *, model_knowledge=(), metadata=None, files=(), file_id=None, knowledge_id=None, knowledge_enabled=True
):
    """Resolve selected sources once for exact-search adapters; never scan all KBs."""
    from open_webui.models.files import Files
    from open_webui.models.knowledge import Knowledges

    entries = knowledge_entries(
        model_knowledge, metadata, files, knowledge_enabled=knowledge_enabled, native_pdf=is_native_pdf_attachment
    )
    if file_id:
        if not knowledge_enabled and not any(item['type'] == 'file' and item['id'] == file_id for item in entries):
            raise PermissionError('File not found')
        file = await Files.get_file_by_id(file_id)
        if not file or not await _has_read_access_to_file(file, user):
            raise PermissionError('File not found')
        if knowledge_id:
            raise ValueError('Use file_id or knowledge_id, not both')
        selected = [item for item in entries if item['type'] in ('file', 'collection')]
        if selected and not any(item['type'] == 'file' and item['id'] == file_id for item in selected):
            for item in selected:
                if item['type'] == 'collection' and await Knowledges.has_file(item['id'], file_id):
                    break
            else:
                raise PermissionError('File is outside the selected scope')
        return [file]
    from open_webui.models.access_grants import AccessGrants

    if knowledge_id:
        if not knowledge_enabled or (
            entries and not any(item['type'] == 'collection' and item['id'] == knowledge_id for item in entries)
        ):
            raise PermissionError('Knowledge base is outside the selected scope')
        entries = [{'type': 'collection', 'id': knowledge_id}]
    found = {}
    group_ids = {group.id for group in await Groups.get_groups_by_member_id(user['id'], include_inherited=True)}
    for entry in entries:
        if entry['type'] == 'file':
            file = await Files.get_file_by_id(entry['id'])
            if file and await _has_read_access_to_file(file, user):
                found[file.id] = file
        elif entry['type'] == 'collection':
            kb = await Knowledges.get_knowledge_by_id(entry['id'])
            if kb and (
                user.get('role') == 'admin'
                or kb.user_id == user['id']
                or await AccessGrants.has_access(
                    user_id=user['id'],
                    resource_type='knowledge',
                    resource_id=kb.id,
                    permission='read',
                    user_group_ids=group_ids,
                )
            ):
                for file in await Knowledges.get_files_by_id(kb.id):
                    found[file.id] = file
    return list(found.values())


async def grep_knowledge_files(
    pattern: str,
    file_id: Optional[str] = None,
    case_insensitive: bool = False,
    count_only: bool = False,
    knowledge_id: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
    __model_knowledge__: Optional[list[dict]] = None,
    __metadata__: dict = None,
    __files__: Optional[list[dict]] = None,
    __knowledge_enabled__: bool = True,
) -> str:
    """
    Locate exact words, quotations, identifiers or clauses in selected files and chat attachments.
    Returns matching lines (filename:line:) and nearby context (filename-line-), with file IDs.
    Output is bounded; for complete clauses read the indicated file/line range with view_knowledge_file.
    Prefer plain text, e.g. A6.3. For unknown wording or cross-document meaning use query_knowledge_files.
    Regex is auto-detected (e.g. "error|warn", "A6[.]3|A6[.]4"); avoid double-escaping
    backslashes when constructing JSON. The decoded pattern is not unescaped a second time.
    Regex uses RE2 syntax; no lookarounds/backreferences, and shorthand character classes are ASCII-only.

    :param pattern: The text pattern to search for (regex auto-detected)
    :param file_id: Optional file ID to search within a single file only
    :param case_insensitive: If true, ignore case when matching (default: false)
    :param count_only: If true, return only match counts per file (default: false)
    :param knowledge_id: Optional authorized KB ID; narrows selected scope and excludes other attachments
    :return: Matching lines and up to two nearby lines per side, deduplicated within each file; truncation is marked
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    if not pattern or not pattern.strip():
        return JSONCodec.dumps({'error': 'Pattern is required'})

    try:
        files_to_search = await _knowledge_source_files(
            __user__,
            model_knowledge=__model_knowledge__ or (),
            metadata=__metadata__,
            files=__files__ or (),
            file_id=file_id,
            knowledge_id=knowledge_id,
            knowledge_enabled=__knowledge_enabled__,
        )

        if not files_to_search:
            return JSONCodec.dumps({'error': 'No accessible files found'})

        return await asyncio.to_thread(_grep_file_models, files_to_search, pattern, case_insensitive, count_only)

    except (ValueError, PermissionError) as e:
        return JSONCodec.dumps({'error': str(e)})
    except Exception as e:
        log.exception(f'grep_knowledge_files error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def view_file(
    file_id: str,
    offset: int = 0,
    max_chars: int = VIEW_FILE_DEFAULT_MAX_CHARS,
    line_numbers: bool = False,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
    __model_knowledge__: Optional[list[dict]] = None,
    __metadata__: dict = None,
    __files__: Optional[list[dict]] = None,
    __knowledge_enabled__: bool = True,
) -> str:
    """Compatibility entry for saved calls; use view_knowledge_file for new calls."""
    return await view_knowledge_file(
        file_id,
        offset=offset,
        max_chars=max_chars,
        line_numbers=line_numbers,
        start_line=start_line,
        end_line=end_line,
        __request__=__request__,
        __user__=__user__,
        __model_knowledge__=__model_knowledge__,
        __metadata__=__metadata__,
        __files__=__files__,
        __knowledge_enabled__=__knowledge_enabled__,
    )


async def view_knowledge_file(
    file_id: Optional[str] = None,
    offset: int = 0,
    max_chars: int = VIEW_FILE_DEFAULT_MAX_CHARS,
    line_numbers: bool = False,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    evidence_refs: Optional[list[str]] = None,
    __request__: Request = None,
    __user__: dict = None,
    __model_knowledge__: Optional[list[dict]] = None,
    __metadata__: dict = None,
    __files__: Optional[list[dict]] = None,
    __model__: dict = None,
    __knowledge_enabled__: bool = True,
) -> str:
    """Read a known authorized file/location, or a batch of stable evidence references.
    Reuse sufficient content already in the conversation; reread for exact wording,
    missing details after compression, changed files, or a whole-document review.
    File reads return parsed text; evidence reads also preserve versioned image evidence.

    :param file_id: File ID to read; use this OR evidence_refs, not both.
    :param offset: Character offset within the file or a single evidence body; use next_offset to continue.
    :param max_chars: Total returned text budget across the call; a server-side hard cap applies.
    :param line_numbers: Prefix file text with real line numbers.
    :param start_line: Optional 1-indexed file line; overrides offset.
    :param end_line: Optional inclusive final file line.
    :param evidence_refs: Stored evidence_ref values from query_knowledge_files, not URLs or paths.
    :return: File text with pagination, or authorized text/image evidence with source locations and warnings.
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})
    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})
    __request__.state.rag_model_images = []
    try:
        offset = max(0, int(offset))
        max_chars = min(max(1, int(max_chars)), VIEW_FILE_MAX_CHARS)
        if evidence_refs is not None:
            if file_id or start_line is not None or end_line is not None or line_numbers:
                raise ValueError('Use file_id and file positions OR evidence_refs, not both')
            if offset and len(evidence_refs) != 1:
                raise ValueError('An evidence offset requires exactly one evidence_ref')
            return await view_knowledge_evidence(
                evidence_refs,
                __request__=__request__,
                __user__=__user__,
                __model_knowledge__=__model_knowledge__,
                __metadata__=__metadata__,
                __files__=__files__,
                __model__=__model__,
                __knowledge_enabled__=__knowledge_enabled__,
                max_chars=max_chars,
                offset=offset,
            )
        if not file_id:
            raise ValueError('Provide file_id or evidence_refs')
        from open_webui.models.knowledge import Knowledges
        from open_webui.models.access_grants import AccessGrants

        file = (
            await _knowledge_source_files(
                __user__,
                file_id=file_id,
                model_knowledge=__model_knowledge__ or (),
                metadata=__metadata__,
                files=__files__ or (),
                knowledge_enabled=__knowledge_enabled__,
            )
        )[0]
        knowledge_info = None
        groups = {group.id for group in await Groups.get_groups_by_member_id(__user__['id'], include_inherited=True)}
        for kb in await Knowledges.get_knowledges_by_file_id(file_id):
            if (
                __user__.get('role') == 'admin'
                or kb.user_id == __user__['id']
                or await AccessGrants.has_access(
                    user_id=__user__['id'],
                    resource_type='knowledge',
                    resource_id=kb.id,
                    permission='read',
                    user_group_ids=groups,
                )
            ):
                knowledge_info = {'id': kb.id, 'name': kb.name}
                break
        return JSONCodec.dumps(
            file_content_page(
                file,
                offset=offset,
                max_chars=max_chars,
                line_numbers=line_numbers,
                start_line=start_line,
                end_line=end_line,
                knowledge=knowledge_info,
            ),
            ensure_ascii=False,
        )
    except (ValueError, TypeError, PermissionError) as exc:
        return JSONCodec.dumps({'error': str(exc)})
    except Exception as exc:
        log.exception('view_knowledge_file error')
        return JSONCodec.dumps({'error': str(exc)})


async def list_knowledge(
    knowledge_id: Optional[str] = None,
    skip: int = 0,
    count: int = 50,
    __request__: Request = None,
    __user__: dict = None,
    __model_knowledge__: Optional[list[dict]] = None,
) -> str:
    """
    List knowledge bases, files, and notes attached to the current model.
    Use only when the available sources or their IDs are unknown or need confirmation.
    Skip discovery when the needed sources are already known; it is not required each turn.
    Without knowledge_id: returns KB summaries (name, description, file_count)
    plus standalone files and notes — no file listing inside KBs.
    With knowledge_id: includes paginated file listing for that specific KB.
    Use skip/count to page through large KBs.

    :param knowledge_id: Optional KB ID to get file listing for
    :param skip: Number of files to skip for pagination (default: 0)
    :param count: Maximum files per page (default: 50, max: 200)
    :return: JSON with knowledge_bases, files, and notes attached to this model
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    if not __model_knowledge__:
        return JSONCodec.dumps({'knowledge_bases': [], 'files': [], 'notes': []})

    # Coerce parameters from LLM tool calls (may come as strings)
    if isinstance(skip, str):
        try:
            skip = int(skip)
        except ValueError:
            skip = 0
    if isinstance(count, str):
        try:
            count = int(count)
        except ValueError:
            count = 50
    if isinstance(knowledge_id, str) and knowledge_id.lower() in ('none', 'null', ''):
        knowledge_id = None

    count = min(count, 200)

    try:
        from open_webui.models.access_grants import AccessGrants
        from open_webui.models.files import Files
        from open_webui.models.knowledge import Knowledges
        from open_webui.models.notes import Notes

        user_id = __user__.get('id')
        user_role = __user__.get('role', 'user')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]

        knowledge_bases = []
        files = []
        notes = []

        for item in __model_knowledge__:
            item_type = item.get('type')
            item_id = item.get('id')

            if item_type == 'collection':
                knowledge = await Knowledges.get_knowledge_by_id(item_id)
                if knowledge and (
                    user_role == 'admin'
                    or knowledge.user_id == user_id
                    or await AccessGrants.has_access(
                        user_id=user_id,
                        resource_type='knowledge',
                        resource_id=knowledge.id,
                        permission='read',
                        user_group_ids=set(user_group_ids),
                    )
                ):
                    kb_files = await Knowledges.get_files_by_id(knowledge.id)
                    file_count = len(kb_files) if kb_files else 0

                    kb_entry = {
                        'id': knowledge.id,
                        'name': knowledge.name,
                        'description': knowledge.description or '',
                        'file_count': file_count,
                    }

                    # Include file listing only when this KB is targeted
                    if knowledge_id and knowledge_id == knowledge.id:
                        if kb_files:
                            paged_files = kb_files[skip : skip + count]
                            kb_entry['files'] = [{'id': f.id, 'filename': f.filename} for f in paged_files]
                            kb_entry['files_skip'] = skip
                            kb_entry['files_count'] = len(paged_files)
                            kb_entry['files_total'] = file_count
                            kb_entry['has_more'] = skip + count < file_count

                    knowledge_bases.append(kb_entry)

            elif item_type == 'file':
                file = await Files.get_file_by_id(item_id)
                if file:
                    files.append(
                        {
                            'id': file.id,
                            'filename': file.filename,
                            'updated_at': file.updated_at,
                        }
                    )

            elif item_type == 'note':
                note = await Notes.get_note_by_id(item_id)
                if note and (
                    user_role == 'admin'
                    or note.user_id == user_id
                    or await AccessGrants.has_access(
                        user_id=user_id,
                        resource_type='note',
                        resource_id=note.id,
                        permission='read',
                    )
                ):
                    notes.append(
                        {
                            'id': note.id,
                            'title': note.title,
                        }
                    )

        return JSONCodec.dumps(
            {
                'knowledge_bases': knowledge_bases,
                'files': files,
                'notes': notes,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'list_knowledge error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def query_knowledge_files(
    query: str = '',
    knowledge_ids: Optional[list[str]] = None,
    count: int = 5,
    query_image_refs: Optional[list[str]] = None,
    modalities: Optional[list[str]] = None,
    file_ids: Optional[list[str]] = None,
    __request__: Request = None,
    __user__: dict = None,
    __model_knowledge__: list[dict] = None,
    __metadata__: dict = None,
    __model__: dict = None,
    __files__: Optional[list[dict]] = None,
    __knowledge_enabled__: bool = True,
) -> str:
    """Search authorized attached knowledge and files, returning stable text/image evidence.
    Use for unknown wording, cross-document questions or text/image semantic search.
    For known filenames use search_knowledge_files; for exact words/clauses use grep_knowledge_files.
    Read a known file/location directly. Reuse already sufficient evidence instead of repeating a search.

    :param query: Text question; may be empty when a query image is supplied.
    :param knowledge_ids: Stored KB IDs. Narrows attached scope; with no attachments choose IDs using knowledge browsing tools.
    :param count: Maximum evidence results, 1 to 25.
    :param query_image_refs: For image-based search only: at most one image ID from attached_files, or an authorized image evidence/asset reference. Omit for text-only search. Never a URL, path or base64.
    :param modalities: Target types: text, image, or both. Defaults to both.
    :param file_ids: Optional file IDs to narrow the selected scope; use this OR knowledge_ids.
    :return: JSON items with evidence_ref, content, source, real location, and warnings. Text-only models have not seen image pixels.
    """
    if __request__ is None or not __user__:
        return JSONCodec.dumps({'error': 'Authenticated request context is required'})
    __request__.state.rag_model_images = []
    from open_webui.retrieval.integration import model_images, request_scope, services, split_external_scope

    try:
        user = UserModel(**__user__)
        metadata = __metadata__ if __metadata__ is not None else {}
        if knowledge_ids is not None and not __knowledge_enabled__:
            raise PermissionError('Knowledge tools are disabled')
        entries = knowledge_entries(
            __model_knowledge__ or (),
            metadata,
            __files__,
            knowledge_enabled=__knowledge_enabled__,
            native_pdf=is_native_pdf_attachment,
        )
        if (
            not __knowledge_enabled__
            and file_ids is not None
            and any(
                identity not in {entry['id'] for entry in entries if entry['type'] == 'file'} for identity in file_ids
            )
        ):
            raise PermissionError('File is outside the chat attachments')
        scope = await request_scope(
            user,
            metadata={**metadata, 'folder_knowledge': [], 'files': entries},
            knowledge_ids=knowledge_ids,
            query_image_refs=query_image_refs or (),
            file_ids=file_ids,
        )
        if knowledge_ids is not None:
            metadata['rag_selected_knowledge_ids'] = list(scope.knowledge_ids)
        if file_ids is not None:
            metadata['rag_selected_file_ids'] = list(scope.file_ids)
        scope, external = await split_external_scope(scope)
        notes = [
            entry
            for entry in [
                *entries,
            ]
            if isinstance(entry, dict) and entry.get('type') == 'note' and entry.get('id')
        ]
        nonlocal_items = external + (notes if knowledge_ids is None and file_ids is None else [])
        if not scope.knowledge_ids and not scope.file_ids and not nonlocal_items:
            return JSONCodec.dumps(
                {'items': [], 'warnings': ['No authorized knowledge selected. Choose a knowledge base ID first.']}
            )
        _, evidence, _, state = await services(__request__, user=user)
        seen = set(metadata.get('rag_seen_refs') or ())
        result = (
            await evidence.search(
                query_text=query,
                query_image_refs=query_image_refs or (),
                modalities=modalities or ('text', 'image'),
                count=count,
                user=user,
                resolved_scope=scope,
                seen_refs=seen,
            )
            if (scope.knowledge_ids or scope.file_ids)
            else {'items': [], 'warnings': []}
        )
        if nonlocal_items and query.strip() and not query_image_refs:
            # This legacy helper is restricted to official notes/external
            # connectors. Local document vectors never take this second path.
            legacy = JSONCodec.loads(
                await _query_knowledge_files_legacy(
                    query, count=count, __request__=__request__, __user__=__user__, __model_knowledge__=nonlocal_items
                )
            )
            if isinstance(legacy, list):
                result['legacy_items'] = legacy
            else:
                result['warnings'].append('External knowledge or note lookup failed.')
        metadata['rag_seen_refs'] = list(seen)
        result['encoding_space'] = state
        capabilities = (__model__ or {}).get('info', {}).get('meta', {}).get('capabilities') or {}
        __request__.state.rag_model_images = await model_images(
            __request__, result['items'], user=user, scope=scope, vision=capabilities.get('vision', False)
        )
        return JSONCodec.dumps(
            model_evidence_result(result, max_chars=min(VIEW_FILE_DEFAULT_MAX_CHARS, VIEW_FILE_MAX_CHARS)),
            ensure_ascii=False,
        )
    except Exception as exc:
        log.warning('Unified knowledge search failed: %s', type(exc).__name__)
        return JSONCodec.dumps(
            {
                'error': str(exc) if isinstance(exc, (ValueError, PermissionError)) else 'Knowledge search failed',
                'error_type': type(exc).__name__,
            }
        )


async def view_knowledge_evidence(
    evidence_refs: list[str],
    max_chars: int = VIEW_FILE_DEFAULT_MAX_CHARS,
    offset: int = 0,
    __request__: Request = None,
    __user__: dict = None,
    __model_knowledge__: list[dict] = None,
    __metadata__: dict = None,
    __model__: dict = None,
    __files__: Optional[list[dict]] = None,
    __knowledge_enabled__: bool = True,
) -> str:
    """Read stored evidence references in the current authorized knowledge scope.

    :param evidence_refs: Stable evidence_ref values returned by query_knowledge_files. Old versions are not redirected.
    :param max_chars: Total text budget across the batch, capped by the server.
    :param offset: Offset within a single evidence body; zero for batches.
    :return: Authorized text/image descriptions and source locations. Images are visible only to vision-capable models.
    """
    if __request__ is None or not __user__:
        return JSONCodec.dumps({'error': 'Authenticated request context is required'})
    __request__.state.rag_model_images = []
    from open_webui.retrieval.integration import model_images, request_scope, services

    try:
        user = UserModel(**__user__)
        metadata = __metadata__ if __metadata__ is not None else {}
        if offset and len(evidence_refs) != 1:
            raise ValueError('An evidence offset requires exactly one evidence_ref')
        entries = knowledge_entries(
            __model_knowledge__ or (),
            metadata,
            __files__,
            knowledge_enabled=__knowledge_enabled__,
            native_pdf=is_native_pdf_attachment,
        )
        scope = await request_scope(user, metadata={**metadata, 'folder_knowledge': [], 'files': entries})
        # A user who discovered a KB may read its returned refs in this turn too.
        if __knowledge_enabled__ and not scope.knowledge_ids and metadata.get('rag_selected_knowledge_ids'):
            scope = await request_scope(user, knowledge_ids=metadata['rag_selected_knowledge_ids'])
        elif (
            __knowledge_enabled__
            and not scope.file_ids
            and not scope.knowledge_ids
            and metadata.get('rag_selected_file_ids')
        ):
            scope = await request_scope(user, file_ids=metadata['rag_selected_file_ids'])
        _, evidence, _, _ = await services(__request__, user=user)
        seen = set(metadata.get('rag_seen_refs') or ())
        result = await evidence.read(evidence_refs, user=user, resolved_scope=scope, seen_refs=seen)
        metadata['rag_seen_refs'] = list(seen)
        capabilities = (__model__ or {}).get('info', {}).get('meta', {}).get('capabilities') or {}
        __request__.state.rag_model_images = await model_images(
            __request__, result['items'], user=user, scope=scope, vision=capabilities.get('vision', False)
        )
        return JSONCodec.dumps(
            model_evidence_result(result, offset=offset, max_chars=min(max(1, int(max_chars)), VIEW_FILE_MAX_CHARS)),
            ensure_ascii=False,
        )
    except Exception as exc:
        return JSONCodec.dumps({'error': 'Evidence is unavailable', 'error_type': type(exc).__name__})


async def _query_knowledge_files_legacy(
    query: str,
    knowledge_ids: Optional[list[str]] = None,
    count: int = 5,
    __request__: Request = None,
    __user__: dict = None,
    __model_knowledge__: list[dict] = None,
) -> str:
    """
    Search knowledge base files using semantic/vector search. Searches across collections (KBs),
    individual files, and notes that the user has access to.
    Helpful for internal documentation, uploaded knowledge, and attached model knowledge.

    :param query: The search query to find semantically relevant content
    :param knowledge_ids: Optional list of KB ids to limit search to specific knowledge bases
    :param count: Maximum number of results to return (default: 5)
    :return: JSON with relevant chunks containing content, source filename, and relevance score
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    # Coerce parameters from LLM tool calls (may come as strings)
    if isinstance(count, str):
        try:
            count = int(count)
        except ValueError:
            count = 5  # Default fallback

    # Handle knowledge_ids being string "None", "null", or empty
    if isinstance(knowledge_ids, str):
        if knowledge_ids.lower() in ('none', 'null', ''):
            knowledge_ids = None
        else:
            # Try to parse as JSON array if it looks like one
            try:
                knowledge_ids = JSONCodec.loads(knowledge_ids)
            except JSONCodec.JSONDecodeError:
                # Treat as single ID
                knowledge_ids = [knowledge_ids]

    try:
        from open_webui.models.access_grants import AccessGrants
        from open_webui.models.files import Files
        from open_webui.models.knowledge import Knowledges
        from open_webui.models.notes import Notes
        from open_webui.retrieval.external import retrieve_external_knowledge
        from open_webui.retrieval.utils import query_collection

        user_id = __user__.get('id')
        user_role = __user__.get('role', 'user')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]

        embedding_function = getattr(__request__.app.state, 'EMBEDDING_FUNCTION', None)
        if not embedding_function:
            return JSONCodec.dumps({'error': 'Embedding function not configured'})
        user_model = UserModel(**__user__)

        collection_names = []
        external_knowledges = []
        note_results = []  # Notes aren't vectorized, handle separately

        # If model has attached knowledge, use those
        if __model_knowledge__:
            for item in __model_knowledge__:
                item_type = item.get('type')
                item_id = item.get('id')

                if item_type == 'collection':
                    # Knowledge base - use KB ID as collection name
                    knowledge = await Knowledges.get_knowledge_by_id(item_id)
                    if knowledge and (
                        user_role == 'admin'
                        or knowledge.user_id == user_id
                        or await AccessGrants.has_access(
                            user_id=user_id,
                            resource_type='knowledge',
                            resource_id=knowledge.id,
                            permission='read',
                            user_group_ids=set(user_group_ids),
                        )
                    ):
                        if (knowledge.meta or {}).get('source') == 'external':
                            external_knowledges.append(knowledge)
                        else:
                            collection_names.append(item_id)

                elif item_type == 'file':
                    # Individual file - use file-{id} as collection name
                    file = await Files.get_file_by_id(item_id)
                    if file:
                        collection_names.append(f'file-{item_id}')

                elif item_type == 'note':
                    # Note - always return full content as context
                    note = await Notes.get_note_by_id(item_id)
                    if note and (
                        user_role == 'admin'
                        or note.user_id == user_id
                        or await AccessGrants.has_access(
                            user_id=user_id,
                            resource_type='note',
                            resource_id=note.id,
                            permission='read',
                        )
                    ):
                        content = note.data.get('content', {}).get('md', '')
                        note_results.append(
                            {
                                'content': content,
                                'source': note.title,
                                'note_id': note.id,
                                'type': 'note',
                            }
                        )

        elif knowledge_ids:
            # User specified specific KBs
            for knowledge_id in knowledge_ids:
                knowledge = await Knowledges.get_knowledge_by_id(knowledge_id)
                if knowledge and (
                    user_role == 'admin'
                    or knowledge.user_id == user_id
                    or await AccessGrants.has_access(
                        user_id=user_id,
                        resource_type='knowledge',
                        resource_id=knowledge.id,
                        permission='read',
                        user_group_ids=set(user_group_ids),
                    )
                ):
                    if (knowledge.meta or {}).get('source') == 'external':
                        external_knowledges.append(knowledge)
                    else:
                        collection_names.append(knowledge_id)
        else:
            # No model knowledge and no specific IDs - search all accessible KBs
            result = await Knowledges.search_knowledge_bases(
                user_id,
                filter={
                    'query': '',
                    'user_id': user_id,
                    'group_ids': user_group_ids,
                },
                skip=0,
                limit=50,
            )
            for knowledge_base in result.items:
                if (knowledge_base.meta or {}).get('source') == 'external':
                    external_knowledges.append(knowledge_base)
                else:
                    collection_names.append(knowledge_base.id)

        chunks = []

        # Add note results first
        chunks.extend(note_results)

        # Query vector collections if any
        if collection_names:
            query_results = await query_collection(
                __request__,
                collection_names=collection_names,
                queries=[query],
                embedding_function=lambda queries, prefix: embedding_function(queries, prefix=prefix, user=user_model),
                k=count,
                user=user_model,
            )

            if query_results and 'documents' in query_results:
                documents = query_results.get('documents', [[]])[0]
                metadatas = query_results.get('metadatas', [[]])[0]
                distances = query_results.get('distances', [[]])[0]

                file_ids = {metadata['file_id'] for metadata in metadatas if metadata.get('file_id')}
                if file_ids:
                    file_names = {
                        file.id: (file.meta or {}).get('name')
                        for file in await Files.get_file_metadatas_by_ids(list(file_ids))
                    }
                    for metadata in metadatas:
                        file_name = file_names.get(metadata.get('file_id'))
                        if file_name:
                            if metadata.get('source') == metadata.get('name'):
                                metadata['source'] = file_name
                            metadata['name'] = file_name

                for idx, doc in enumerate(documents):
                    chunk_info = {
                        **filter_source_metadata(metadatas[idx]),
                        'content': doc,
                        'source': metadatas[idx].get('source', metadatas[idx].get('name', 'Unknown')),
                        'file_id': metadatas[idx].get('file_id', ''),
                    }
                    if idx < len(distances):
                        chunk_info['distance'] = distances[idx]
                    chunks.append(chunk_info)

        for knowledge in external_knowledges:
            query_results = await retrieve_external_knowledge(
                __request__,
                knowledge,
                queries=[query],
                count=count,
                user=user_model,
            )
            documents = query_results.get('documents', [[]])[0]
            metadatas = query_results.get('metadatas', [[]])[0]
            distances = query_results.get('distances', [[]])[0]

            for idx, doc in enumerate(documents):
                metadata = metadatas[idx] if idx < len(metadatas) else {}
                chunk_info = {
                    **filter_source_metadata(metadata),
                    'content': doc,
                    'source': metadata.get('source', metadata.get('name', knowledge.name)),
                    'type': 'external',
                    'knowledge_id': knowledge.id,
                }
                if idx < len(distances):
                    chunk_info['distance'] = distances[idx]
                chunks.append(chunk_info)

        # Limit to requested count
        chunks = chunks[:count]

        return JSONCodec.dumps(chunks, ensure_ascii=False)
    except Exception as e:
        log.exception(f'query_knowledge_files error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def query_knowledge_bases(
    query: str,
    count: int = 5,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Search knowledge bases by semantic similarity to query.
    Finds KBs whose name/description match the meaning of your query.
    Helpful for discovering which knowledge base to query next.

    :param query: Natural language query describing what you're looking for
    :param count: Maximum results (default: 5)
    :return: JSON with matching KBs (id, name, description, similarity)
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        import heapq

        from open_webui.models.knowledge import Knowledges
        from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT
        from open_webui.routers.knowledge import KNOWLEDGE_BASES_COLLECTION

        user_id = __user__.get('id')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]
        embedding_function = getattr(__request__.app.state, 'EMBEDDING_FUNCTION', None)
        if not embedding_function:
            return JSONCodec.dumps({'error': 'Embedding function not configured'})
        user_model = UserModel(**__user__)
        query_embedding = await embedding_function(query, prefix=RAG_EMBEDDING_QUERY_PREFIX, user=user_model)

        # Min-heap of (distance, knowledge_base_id) - only holds top `count` results
        top_results_heap = []
        seen_ids = set()
        page_offset = 0
        page_size = 100

        while True:
            accessible_knowledge_bases = await Knowledges.search_knowledge_bases(
                user_id,
                filter={'user_id': user_id, 'group_ids': user_group_ids},
                skip=page_offset,
                limit=page_size,
            )

            if not accessible_knowledge_bases.items:
                break

            accessible_ids = [kb.id for kb in accessible_knowledge_bases.items]

            search_results = await ASYNC_VECTOR_DB_CLIENT.search(
                collection_name=KNOWLEDGE_BASES_COLLECTION,
                vectors=[query_embedding],
                filter={'knowledge_base_id': {'$in': accessible_ids}},
                limit=count,
            )

            if search_results and search_results.ids and search_results.ids[0]:
                result_ids = search_results.ids[0]
                result_distances = search_results.distances[0] if search_results.distances else [0] * len(result_ids)

                for knowledge_base_id, distance in zip(result_ids, result_distances):
                    if knowledge_base_id in seen_ids:
                        continue
                    seen_ids.add(knowledge_base_id)

                    if len(top_results_heap) < count:
                        heapq.heappush(top_results_heap, (distance, knowledge_base_id))
                    elif distance > top_results_heap[0][0]:
                        heapq.heapreplace(top_results_heap, (distance, knowledge_base_id))

            page_offset += page_size
            if len(accessible_knowledge_bases.items) < page_size:
                break
            if page_offset >= MAX_KNOWLEDGE_BASE_SEARCH_ITEMS:
                break

        # Sort by distance descending (best first) and fetch KB details
        sorted_results = sorted(top_results_heap, key=lambda x: x[0], reverse=True)

        matching_knowledge_bases = []
        for distance, knowledge_base_id in sorted_results:
            knowledge_base = await Knowledges.get_knowledge_by_id(knowledge_base_id)
            if knowledge_base:
                matching_knowledge_bases.append(
                    {
                        'id': knowledge_base.id,
                        'name': knowledge_base.name,
                        'description': knowledge_base.description or '',
                        'similarity': round(distance, 4),
                    }
                )

        return JSONCodec.dumps(matching_knowledge_bases, ensure_ascii=False)

    except Exception as e:
        log.exception(f'query_knowledge_bases error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# =============================================================================
# SKILLS TOOLS
# =============================================================================


async def view_skill(
    id: str,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
    __event_call__: callable = None,
) -> str:
    """Read skill instructions and file list. Large skills return an opening preview and next_offset.

    :param id: Skill ID from the available skills manifest.
    """
    if __request__ is None or not __user__:
        return JSONCodec.dumps({'error': 'Request and user context required'})
    try:
        terminal_skill_prefix = 'terminal:'
        if isinstance(id, str) and id.startswith(terminal_skill_prefix):
            from open_webui.utils.terminals import get_terminal_skill

            skill_name = unquote(id.removeprefix(terminal_skill_prefix))
            skill = await get_terminal_skill(
                __request__,
                __user__,
                __metadata__ if __metadata__ is not None else {},
                skill_name,
                {'__event_call__': __event_call__},
                overview=True,
            )
            if not skill:
                return JSONCodec.dumps({'error': f"Skill '{id}' not found"})
            return JSONCodec.dumps(skill, ensure_ascii=False)

        from types import SimpleNamespace
        from open_webui.models.skills import get_skill_snapshot
        from open_webui.routers.skills import authorized_skill
        from open_webui.utils.skill_files import file_summaries

        actor = SimpleNamespace(**__user__)
        skill = await authorized_skill(id.lower(), actor)
        if not skill.is_active:
            await authorized_skill(skill.id, actor, 'write')
        metadata = __metadata__ if __metadata__ is not None else {}
        context = metadata.get('chat_context') or {}
        metadata['chat_context'] = context
        versions = context.setdefault('skill_versions', {})
        version_id = skill.version_id
        snapshot = await get_skill_snapshot(skill, version_id)
        versions[skill.id] = version_id
        return JSONCodec.dumps(
            {
                'id': skill.id,
                'name': snapshot['name'],
                **await skill_overview(snapshot['content'], file_summaries(snapshot['data']['files'])),
            },
            ensure_ascii=False,
        )
    except Exception as error:
        return JSONCodec.dumps({'error': getattr(error, 'detail', str(error))})


async def read_skill_file(
    id: str,
    path: str,
    offset: int = 0,
    max_chars: int = 10000,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
    __event_call__: callable = None,
) -> str:
    """Read a skill file from the loaded snapshot. Terminal skills support SKILL.md only.

    :param id: Skill ID.
    :param path: Relative path within the skill.
    :param offset: Character offset for paging text.
    :param max_chars: Maximum characters to return, capped by the admin skill content limit (default 10000).
    """
    try:
        from types import SimpleNamespace
        from urllib.parse import urlencode
        from open_webui.models.skills import get_skill_snapshot
        from open_webui.routers.skills import authorized_skill
        from open_webui.utils.skill_files import file_summaries

        if not __user__ or __request__ is None:
            raise ValueError('Request and user context required')
        if id.startswith('terminal:'):
            from open_webui.utils.terminals import get_terminal_skill

            if path != 'SKILL.md':
                raise ValueError('Terminal skills support SKILL.md only; use terminal tools for supporting files.')
            skill = await get_terminal_skill(
                __request__,
                __user__,
                __metadata__ if __metadata__ is not None else {},
                unquote(id.removeprefix('terminal:')),
                {'__event_call__': __event_call__},
                offset=offset,
                max_chars=max_chars,
                refresh=False,
            )
            if not skill:
                raise ValueError(f"Skill '{id}' not found")
            return JSONCodec.dumps(
                {'path': path, 'content': skill['content'], 'next_offset': skill['next_offset']}, ensure_ascii=False
            )
        skill = await authorized_skill(id, SimpleNamespace(**__user__))
        if not skill.is_active:
            await authorized_skill(id, SimpleNamespace(**__user__), 'write')
        metadata = __metadata__ if __metadata__ is not None else {}
        context = metadata.get('chat_context') or {}
        metadata['chat_context'] = context
        versions = context.setdefault('skill_versions', {})
        version_id = versions.get(skill.id) or skill.version_id
        snapshot = await get_skill_snapshot(skill, version_id)
        versions[skill.id] = version_id
        file = next((f for f in snapshot['data']['files'] if f['path'] == path), None)
        if file is None:
            raise ValueError('File not found')
        if file.get('encoding'):
            return JSONCodec.dumps(
                {
                    **file_summaries([file])[0],
                    'url': '/workspace/skills/edit?' + urlencode({'id': id, 'path': path}),
                }
            )
        return JSONCodec.dumps(
            {
                'path': path,
                **await skill_content_page(file['content'], offset, max_chars),
            },
            ensure_ascii=False,
        )
    except Exception as error:
        return JSONCodec.dumps({'error': getattr(error, 'detail', str(error))})


async def grep_skill_files(
    id: str,
    pattern: str,
    path: str | None = None,
    case_insensitive: bool = True,
    max_results: int = 10,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
    __event_call__: callable = None,
) -> str:
    """Search skill files for text or regex matches.

    Literal text or auto-detected RE2 regex; no lookarounds/backreferences, and shorthand character classes are ASCII-only.
    Searches each line for non-empty, non-overlapping matches and returns merged, bounded excerpts (6000 characters total).
    Offsets and match_length describe the first match in each excerpt, in original-file characters; total_matches counts
    occurrences, not excerpts. Omitted excerpts have compact locations in omitted_matches; omitted_matches_truncated
    indicates when more locations remain. Terminal skills support SKILL.md only. No skill version is created.

    :param id: Skill ID, including terminal: IDs.
    :param pattern: Text or auto-detected regex to find (up to 4096 characters).
    :param path: Optional exact skill file path; otherwise search all text files, skipping binary files.
    :param case_insensitive: Ignore case when matching (default true).
    :param max_results: Maximum excerpts to return, from 1 to 30 (default 10).
    """
    from open_webui.utils.skill_files import SKILL_GREP_MAX_CHARS, grep_skill_content

    try:
        if __request__ is None or not __user__:
            raise ValueError('Request and user context required')
        metadata = __metadata__ if __metadata__ is not None else {}
        if id.startswith('terminal:'):
            from open_webui.utils.terminals import load_terminal_skill

            if path is not None and path != 'SKILL.md':
                raise ValueError('Terminal skills support SKILL.md only; use terminal tools for supporting files.')
            skill = await load_terminal_skill(
                __request__,
                __user__,
                metadata,
                unquote(id.removeprefix('terminal:')),
                {'__event_call__': __event_call__},
                refresh=False,
            )
            if skill is None:
                raise ValueError(f"Skill '{id}' not found")
            files = [{'path': 'SKILL.md', 'content': skill['content']}]
        else:
            from types import SimpleNamespace
            from open_webui.models.skills import get_skill_snapshot
            from open_webui.routers.skills import authorized_skill

            actor = SimpleNamespace(**__user__)
            skill = await authorized_skill(id, actor)
            if not skill.is_active:
                await authorized_skill(id, actor, 'write')
            context = metadata.get('chat_context') or {}
            metadata['chat_context'] = context
            versions = context.setdefault('skill_versions', {})
            version_id = versions.get(skill.id) or skill.version_id
            snapshot = await get_skill_snapshot(skill, version_id)
            versions[skill.id] = version_id
            files = snapshot['data']['files']
            if path is not None:
                file = next((file for file in files if file['path'] == path), None)
                if file is None:
                    raise ValueError('File not found')
                if file.get('encoding'):
                    raise ValueError('File is not a text file')
                files = [file]
        return await asyncio.to_thread(grep_skill_content, files, pattern, case_insensitive, max_results)
    except Exception as error:
        detail = str(getattr(error, 'detail', str(error)))
        result = JSONCodec.dumps({'error': detail}, ensure_ascii=False)
        while len(result) > SKILL_GREP_MAX_CHARS:
            detail = detail[: max(0, len(detail) - (len(result) - SKILL_GREP_MAX_CHARS))]
            result = JSONCodec.dumps({'error': detail}, ensure_ascii=False)
        return result


async def create_skill(
    id: str,
    name: str,
    content: str,
    files: list[SkillFile] = None,
    commit_message: str = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """Create a private workspace skill with SKILL.md and optional UTF-8 supporting files. No terminal is needed.

    :param id: Unique lowercase skill slug.
    :param name: Display name.
    :param content: Complete SKILL.md text, including name and description frontmatter.
    :param files: Optional supporting files, each with path and content. UTF-8 text only.
    :param commit_message: Optional description of this save.
    """
    try:
        from types import SimpleNamespace
        from open_webui.routers.skills import create_new_skill
        from open_webui.models.skills import SkillForm
        from open_webui.utils.skill_files import frontmatter
        from open_webui.utils.access_control import has_permission

        if __request__ is None or not __user__:
            raise ValueError('Request and user context required')
        files = [f.model_dump(exclude_none=True) if isinstance(f, SkillFile) else f for f in files or []]
        if any(f.get('encoding') or f.get('path') == 'SKILL.md' for f in files):
            raise ValueError('Supporting files must be UTF-8 text; pass SKILL.md in content')
        # Agent authoring requires the workspace editor permission, not just import access.
        if __user__.get('role') != 'admin' and not await has_permission(
            __user__['id'], 'workspace.skills', await Config.get('user.permissions')
        ):
            raise ValueError('Skill authoring permission required')
        result = await create_new_skill(
            __request__,
            SkillForm(
                id=id,
                name=name,
                content=content,
                description=str(frontmatter(content).get('description', '')),
                files=[{'path': 'SKILL.md', 'content': content}, *(files or [])],
                commit_message=commit_message,
                access_grants=[],
            ),
            SimpleNamespace(**__user__),
            None,
        )
        return JSONCodec.dumps({'id': result.id, 'url': '/workspace/skills/edit?id=' + result.id})
    except Exception as error:
        return JSONCodec.dumps({'error': getattr(error, 'detail', str(error))})


async def update_skill_files(
    id: str,
    operations: list[SkillFileOperation],
    commit_message: str = None,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
) -> str:
    """Save file operations as a new skill version, preserving untouched files. If the skill changed since it was read, call view_skill again and reapply the edit.

    :param id: Skill to edit. Requires write access.
    :param operations: File operations: {op: put, path, content}, {op: move, path, destination}, or {op: delete, path}. Use put with path SKILL.md to edit the instructions. Text writes only.
    :param commit_message: Optional description of the change.
    """
    try:
        from types import SimpleNamespace
        from open_webui.routers.skills import authorized_skill
        from open_webui.models.skills import Skills
        from open_webui.events import EVENTS, publish_event

        if __request__ is None or not __user__:
            raise ValueError('Request and user context required')
        user = SimpleNamespace(**__user__)
        skill = await authorized_skill(id, user, 'write')
        metadata = __metadata__ if __metadata__ is not None else {}
        context = metadata.get('chat_context') or {}
        metadata['chat_context'] = context
        versions = context.setdefault('skill_versions', {})
        expected_version_id = versions.get(skill.id) or skill.version_id
        operations = [
            op.model_dump(exclude_none=True) if isinstance(op, SkillFileOperation) else op for op in operations or []
        ]
        if any(op.get('encoding') for op in operations):
            raise ValueError('Agent file writes support UTF-8 text only')
        result = await Skills.update_skill_by_id(
            id,
            {
                'expected_version_id': expected_version_id,
                'operations': operations or [],
                'commit_message': commit_message,
            },
            user_id=user.id,
        )
        versions[skill.id] = result.version_id
        await publish_event(__request__, EVENTS.SKILL_UPDATED, actor=user, subject_id=id, data={'name': result.name})
        return JSONCodec.dumps({'id': result.id})
    except Exception as error:
        detail = getattr(error, 'detail', str(error))
        if isinstance(detail, dict) and detail.get('code') == 'version_conflict':
            detail = {
                'code': 'version_conflict',
                'message': 'Skill changed. Call view_skill again and reapply the edit.',
            }
        return JSONCodec.dumps({'error': detail})


# =============================================================================
# TOOL SEARCH
# =============================================================================


async def search_tools(
    query: str,
    count: int = 5,
    __metadata__: dict = None,
) -> str:
    """
    Search the tools listed in <available_tools> and return their full definitions.
    Pass the exact tool name when you already know it.
    Invoke a result with call_tool(tool_name, params), where params is a JSON-encoded object
    matching the tool's parameters. Do not call deferred tools directly by name.

    :param query: Keywords describing the capability you need (e.g. "jira create issue"), or an exact tool name
    :param count: Maximum number of results to return (default: 5, max: 20)
    :return: JSON with the definitions of the matching tools, which can then be invoked through call_tool
    """
    from open_webui.utils.tool_search import search_deferred_tools

    tools = __metadata__['tools']
    candidates = {name: tools[name]['spec'] for name in __metadata__['deferred_tools']}
    matches = search_deferred_tools(query, candidates, count)
    if not matches:
        return JSONCodec.dumps(
            {'tools': [], 'message': 'No matching tools found. Try different keywords or the exact tool name.'}
        )
    return JSONCodec.dumps({'tools': [candidates[name] for name in matches]})


# =============================================================================
# TASK MANAGEMENT TOOLS
# =============================================================================

from typing import Literal

from pydantic import BaseModel, Field

VALID_TASK_STATUSES = {'pending', 'in_progress', 'completed', 'cancelled'}


class TaskItem(BaseModel):
    id: Optional[str] = Field(None, description='Unique identifier for the task. Auto-generated if omitted.')
    content: str = Field(..., description='Task description.')
    status: Literal['pending', 'in_progress', 'completed', 'cancelled'] = Field('pending', description='Task status.')


def _task_summary(all_tasks: list[dict]) -> dict:
    """Build summary counts for a task list."""
    pending = sum(1 for t in all_tasks if t['status'] == 'pending')
    in_progress = sum(1 for t in all_tasks if t['status'] == 'in_progress')
    completed = sum(1 for t in all_tasks if t['status'] == 'completed')
    cancelled = sum(1 for t in all_tasks if t['status'] == 'cancelled')
    return {
        'total': len(all_tasks),
        'pending': pending,
        'in_progress': in_progress,
        'completed': completed,
        'cancelled': cancelled,
    }


async def _emit_tasks(event_emitter, all_tasks: list[dict]):
    """Persist task state to the UI."""
    if event_emitter:
        await event_emitter(
            {
                'type': 'chat:message:tasks',
                'data': {
                    'tasks': all_tasks,
                },
            }
        )


async def create_tasks(
    tasks: list[TaskItem],
    __chat_id__: str = None,
    __message_id__: str = None,
    __event_emitter__: callable = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Create a visible task checklist for multi-step work so progress can be shown in chat.

    :param tasks: List of task items. Each item: content (string, required), status (pending|in_progress|completed|cancelled, default pending), id (optional, auto-generated).
    :return: JSON with the full task list and summary counts
    """
    if not is_saved_chat_id(__chat_id__):
        return JSONCodec.dumps({'error': 'Saved chat context not available'})

    try:
        all_tasks = []
        for idx, task in enumerate(tasks):
            if hasattr(task, 'model_dump'):
                d = task.model_dump(exclude_none=True)
            elif isinstance(task, dict):
                d = task
            else:
                d = dict(task)

            content = str(d.get('content', '')).strip()
            if not content:
                continue

            item_id = str(d.get('id', '') or '').strip() or str(idx + 1)
            status = str(d.get('status', 'pending')).strip().lower()
            if status not in VALID_TASK_STATUSES:
                status = 'pending'

            all_tasks.append({'id': item_id, 'content': content, 'status': status})

        await Chats.update_chat_tasks_by_id(__chat_id__, all_tasks)
        await _emit_tasks(__event_emitter__, all_tasks)

        return JSONCodec.dumps(
            {'tasks': all_tasks, 'summary': _task_summary(all_tasks)},
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'tasks error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def update_task(
    id: str,
    status: str = 'completed',
    __chat_id__: str = None,
    __message_id__: str = None,
    __event_emitter__: callable = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Mark a single visible task item as completed, in_progress, pending, or cancelled.

    :param id: The task ID to update
    :param status: New status: completed, in_progress, pending, or cancelled (default: completed)
    :return: JSON with the updated task list and summary counts
    """
    if not is_saved_chat_id(__chat_id__):
        return JSONCodec.dumps({'error': 'Saved chat context not available'})

    try:
        status = status.strip().lower()
        if status not in VALID_TASK_STATUSES:
            return JSONCodec.dumps(
                {'error': f'Invalid status: {status}. Must be one of: {", ".join(sorted(VALID_TASK_STATUSES))}'}
            )

        all_tasks = await Chats.get_chat_tasks_by_id(__chat_id__)

        found = False
        for task in all_tasks:
            if task['id'] == id:
                task['status'] = status
                found = True
                break

        if not found:
            return JSONCodec.dumps({'error': f'Task with id "{id}" not found'})

        await Chats.update_chat_tasks_by_id(__chat_id__, all_tasks)
        await _emit_tasks(__event_emitter__, all_tasks)

        return JSONCodec.dumps(
            {'tasks': all_tasks, 'summary': _task_summary(all_tasks)},
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'update_task_status error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# =============================================================================
# AUTOMATION TOOLS
# =============================================================================


async def _validate_owned_automation_folder(user_id: str, folder_id: Optional[str]) -> Optional[str]:
    if not folder_id:
        return None
    from open_webui.models.folders import Folders

    folder = await Folders.get_folder_by_id_and_user_id(folder_id, user_id)
    if not folder:
        raise ValueError('Folder not found')
    return folder.id


async def create_automation(
    name: str,
    prompt: str,
    rrule: str,
    folder_id: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
) -> str:
    """
    Create a scheduled automation that runs a prompt on a recurring or one-time schedule.
    Use this when the user wants to schedule a task to run automatically.
    The automation will use the current chat model.

    The rrule parameter must be a valid iCalendar RRULE string. Common examples:
    - Every day at 9am: "DTSTART:20250101T090000\\nRRULE:FREQ=DAILY"
    - Every Monday at 8am: "DTSTART:20250106T080000\\nRRULE:FREQ=WEEKLY;BYDAY=MO"
    - Every hour: "RRULE:FREQ=HOURLY;INTERVAL=1"
    - Every 30 minutes: "RRULE:FREQ=MINUTELY;INTERVAL=30"
    - Once at a specific time: "DTSTART:20250415T140000\\nRRULE:FREQ=DAILY;COUNT=1"
    - First day of every month: "DTSTART:20250101T090000\\nRRULE:FREQ=MONTHLY;BYMONTHDAY=1"

    The DTSTART time should reflect the desired execution time. Use COUNT=1 for one-time automations.

    :param name: A short descriptive name for the automation
    :param prompt: The prompt/instructions to execute on each run
    :param rrule: An iCalendar RRULE string defining the schedule
    :param folder_id: Optional owner-owned folder ID for generated chats
    :return: JSON with the created automation details including id, next scheduled runs
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.automations import AutomationData, AutomationForm, AutomationTarget, Automations
        from open_webui.models.users import Users
        from open_webui.routers.automations import check_automation_limits
        from open_webui.utils.automations import next_n_runs_ns, next_run_ns, validate_rrule

        user_id = __user__.get('id')
        user = await Users.get_user_by_id(user_id)
        if not user:
            return JSONCodec.dumps({'error': 'User not found'})

        # Fall back to model dict ID since __metadata__ may predate model_id assignment
        metadata = __metadata__ or {}
        model_id = metadata.get('model_id') or (
            metadata.get('model', {}).get('id') if isinstance(metadata.get('model'), dict) else None
        )
        if not model_id:
            return JSONCodec.dumps({'error': 'Could not detect current model'})

        try:
            folder_id = await _validate_owned_automation_folder(user_id, folder_id)
        except ValueError as e:
            return JSONCodec.dumps({'error': str(e)})

        # Validate the RRULE
        try:
            await validate_rrule(rrule, tz=user.timezone)
        except ValueError as e:
            return JSONCodec.dumps({'error': f'Invalid schedule: {e}'})

        try:
            await check_automation_limits(__request__, user, rrule, None, is_create=True)
        except HTTPException as e:
            return JSONCodec.dumps({'error': e.detail})

        tz = user.timezone
        form = AutomationForm(
            name=name,
            folder_id=folder_id,
            data=AutomationData(
                prompt=prompt,
                model_id=model_id,
                rrule=rrule,
                target=(
                    AutomationTarget(type='channel', channel_id=metadata.get('chat_id', '').removeprefix('channel:'))
                    if metadata.get('chat_id', '').startswith('channel:')
                    else None
                ),
            ),
            is_active=True,
        )

        automation = await Automations.insert(user_id, form, await next_run_ns(rrule, tz=tz))

        return JSONCodec.dumps(
            {
                'status': 'success',
                'id': automation.id,
                'name': automation.name,
                'folder_id': automation.folder_id,
                'model_id': model_id,
                'target': automation.data.get('target'),
                'is_active': automation.is_active,
                'next_runs': await next_n_runs_ns(rrule, tz=tz),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'create_automation error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def update_automation(
    automation_id: str,
    name: Optional[str] = None,
    prompt: Optional[str] = None,
    rrule: Optional[str] = None,
    model_id: Optional[str] = None,
    folder_id: Optional[str] = '',
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Update an existing automation. Only the provided fields are changed; omitted fields stay the same.

    :param automation_id: The ID of the automation to update
    :param name: New name for the automation (optional)
    :param prompt: New prompt/instructions (optional)
    :param rrule: New iCalendar RRULE schedule string (optional). See create_automation for format examples.
    :param model_id: New model ID to use (optional); blank values are ignored
    :param folder_id: New owner-owned folder ID (optional); omit or pass blank to keep unchanged, pass null to clear
    :return: JSON with the updated automation details
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.automations import AutomationData, AutomationForm, AutomationTarget, Automations
        from open_webui.models.users import Users
        from open_webui.routers.automations import check_automation_limits
        from open_webui.utils.automations import next_n_runs_ns, next_run_ns, validate_rrule

        user_id = __user__.get('id')
        user = await Users.get_user_by_id(user_id)
        if not user:
            return JSONCodec.dumps({'error': 'User not found'})

        automation = await Automations.get_by_id(automation_id)
        if not automation:
            return JSONCodec.dumps({'error': 'Automation not found'})
        if automation.user_id != user_id:
            return JSONCodec.dumps({'error': 'Access denied'})

        # Merge provided fields with existing values
        new_name = name if name is not None else automation.name
        new_prompt = prompt if prompt is not None else automation.data.get('prompt', '')
        new_model_id = model_id.strip() if model_id and model_id.strip() else automation.data.get('model_id', '')
        new_rrule = rrule if rrule is not None else automation.data.get('rrule', '')
        if folder_id is None:
            new_folder_id = None
        elif not folder_id.strip():
            new_folder_id = automation.folder_id
        else:
            try:
                new_folder_id = await _validate_owned_automation_folder(user_id, folder_id.strip())
            except ValueError as e:
                return JSONCodec.dumps({'error': str(e)})

        # Validate RRULE if changed
        if rrule is not None:
            try:
                await validate_rrule(new_rrule, tz=user.timezone)
            except ValueError as e:
                return JSONCodec.dumps({'error': f'Invalid schedule: {e}'})

        try:
            await check_automation_limits(__request__, user, new_rrule, None)
        except HTTPException as e:
            return JSONCodec.dumps({'error': e.detail})

        tz = user.timezone
        form = AutomationForm(
            name=new_name,
            folder_id=new_folder_id,
            data=AutomationData(
                prompt=new_prompt,
                model_id=new_model_id,
                rrule=new_rrule,
                target=AutomationTarget(**automation.data['target']) if automation.data.get('target') else None,
            ),
            is_active=automation.is_active,
        )

        updated = await Automations.update_by_id(automation_id, form, await next_run_ns(new_rrule, tz=tz))

        return JSONCodec.dumps(
            {
                'status': 'success',
                'id': updated.id,
                'name': updated.name,
                'folder_id': updated.folder_id,
                'model_id': new_model_id,
                'target': updated.data.get('target'),
                'is_active': updated.is_active,
                'next_runs': await next_n_runs_ns(new_rrule, tz=tz),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'update_automation error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def list_automations(
    status: Optional[str] = None,
    folder_id: Optional[str] = None,
    count: int = 10,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    List the user's scheduled automations.

    :param status: Filter by status: "active", "paused", or omit for all
    :param folder_id: Optional owner-owned folder ID filter; pass an empty string to clear the folder filter
    :param count: Maximum number of automations to return (default: 10)
    :return: JSON list of automations with id, name, prompt snippet, schedule, status, and next runs
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.automations import Automations
        from open_webui.models.users import Users
        from open_webui.utils.automations import next_n_runs_ns

        user_id = __user__.get('id')
        user = await Users.get_user_by_id(user_id)
        if folder_id:
            try:
                folder_id = await _validate_owned_automation_folder(user_id, folder_id)
            except ValueError as e:
                return JSONCodec.dumps({'error': str(e)})

        result = await Automations.search_automations(
            user_id=user_id,
            status=status,
            folder_id=folder_id,
            skip=0,
            limit=count,
        )

        automations = []
        for item in result.items:
            rrule = item.data.get('rrule', '')
            prompt_text = item.data.get('prompt', '')
            snippet = prompt_text[:100] + ('...' if len(prompt_text) > 100 else '')

            automations.append(
                {
                    'id': item.id,
                    'name': item.name,
                    'folder_id': item.folder_id,
                    'prompt_snippet': snippet,
                    'model_id': item.data.get('model_id', ''),
                    'target': item.data.get('target'),
                    'rrule': rrule,
                    'is_active': item.is_active,
                    'last_run_at': item.last_run_at,
                    'next_runs': await next_n_runs_ns(rrule, tz=user.timezone if user else None),
                }
            )

        return JSONCodec.dumps(
            {'automations': automations, 'total': result.total},
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'list_automations error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def toggle_automation(
    automation_id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Pause or resume a scheduled automation. If active, it will be paused. If paused, it will be resumed.

    :param automation_id: The ID of the automation to toggle
    :return: JSON with the updated automation status
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.automations import Automations
        from open_webui.models.users import Users
        from open_webui.utils.automations import next_run_ns

        user_id = __user__.get('id')
        user = await Users.get_user_by_id(user_id)

        automation = await Automations.get_by_id(automation_id)
        if not automation:
            return JSONCodec.dumps({'error': 'Automation not found'})
        if automation.user_id != user_id:
            return JSONCodec.dumps({'error': 'Access denied'})

        rrule = automation.data.get('rrule', '')
        toggled = await Automations.toggle(
            automation_id,
            await next_run_ns(rrule, tz=user.timezone if user else None),
        )

        return JSONCodec.dumps(
            {
                'status': 'success',
                'id': toggled.id,
                'name': toggled.name,
                'is_active': toggled.is_active,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'toggle_automation error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def delete_automation(
    automation_id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Delete a scheduled automation and all its run history.

    :param automation_id: The ID of the automation to delete
    :return: JSON confirming the automation was deleted
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.automations import AutomationRuns, Automations

        user_id = __user__.get('id')

        automation = await Automations.get_by_id(automation_id)
        if not automation:
            return JSONCodec.dumps({'error': 'Automation not found'})
        if automation.user_id != user_id:
            return JSONCodec.dumps({'error': 'Access denied'})

        name = automation.name
        await AutomationRuns.delete_by_automation(automation_id)
        await Automations.delete(automation_id)

        return JSONCodec.dumps(
            {
                'status': 'success',
                'message': f'Automation "{name}" deleted',
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'delete_automation error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# =============================================================================
# CALENDAR TOOLS
# =============================================================================


MAX_CALENDAR_RANGE_END_NS = 2**63 - 1


def _get_user_tz(user_dict: dict):
    """Get the user's timezone as a ZoneInfo, falling back to UTC."""
    from zoneinfo import ZoneInfo

    tz_name = None
    if user_dict:
        tz_name = user_dict.get('timezone')
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            pass
    return ZoneInfo('UTC')


def _dt_to_ns(dt_str: str, tz) -> int:
    """Convert a datetime string to nanoseconds since epoch, interpreting in the given timezone."""
    from datetime import datetime

    dt = datetime.fromisoformat(dt_str)
    # If naive (no timezone info), localize to user's timezone
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return int(dt.timestamp() * 1_000) * 1_000_000


def _ns_to_dt(ns: int, tz) -> str:
    """Convert nanoseconds since epoch to a datetime string in the given timezone."""
    from datetime import datetime

    seconds = ns / 1_000_000_000
    dt = datetime.fromtimestamp(seconds, tz=tz)
    return dt.strftime('%Y-%m-%d %H:%M')


def _event_to_dict(event, tz) -> dict:
    """Convert a calendar event model to a human-friendly dict with local timestamps."""
    alert_minutes = None
    if event.meta and 'alert_minutes' in event.meta:
        alert_minutes = event.meta['alert_minutes']
    return {
        'id': event.id,
        'calendar_id': event.calendar_id,
        'title': event.title,
        'description': event.description or '',
        'start': _ns_to_dt(event.start_at, tz),
        'end': _ns_to_dt(event.end_at, tz) if event.end_at else None,
        'all_day': event.all_day,
        'location': event.location or '',
        'reminder_minutes': alert_minutes if alert_minutes is not None else 10,
        'color': event.color,
        'is_cancelled': event.is_cancelled,
    }


async def search_calendar_events(
    query: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    count: int = 10,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Search calendar events, reminders, and scheduled items by text and/or date range.
    Helpful for finding upcoming events, reminders, or schedule items.

    :param query: Search text to match against event title, description, or location (optional)
    :param start: Only return events starting at or after this datetime, e.g. "2026-04-20 00:00" (optional)
    :param end: Only return events starting before this datetime, e.g. "2026-04-27 00:00" (optional)
    :param count: Maximum number of events to return (default: 10)
    :return: JSON list of matching events with id, title, description, start, end, calendar_id, location
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.calendar import CalendarEvents

        user_id = __user__.get('id')
        tz = _get_user_tz(__user__)

        if isinstance(count, str):
            try:
                count = int(count)
            except ValueError:
                count = 10

        if start or end:
            # Date range query — use get_events_by_range
            try:
                start_ns = _dt_to_ns(start, tz) if start else 0
            except (ValueError, TypeError) as e:
                return JSONCodec.dumps({'error': f'Invalid start datetime: {e}'})

            try:
                end_ns = _dt_to_ns(end, tz) if end else MAX_CALENDAR_RANGE_END_NS
            except (ValueError, TypeError) as e:
                return JSONCodec.dumps({'error': f'Invalid end datetime: {e}'})

            items = await CalendarEvents.get_events_by_range(
                user_id=user_id,
                start=start_ns,
                end=end_ns,
            )

            # Apply text filter if query is also provided
            if query:
                q = query.lower()
                items = [
                    e
                    for e in items
                    if q in (e.title or '').lower()
                    or q in (e.description or '').lower()
                    or q in (e.location or '').lower()
                ]

            events = [_event_to_dict(item, tz) for item in items[:count]]
            return JSONCodec.dumps(
                {'events': events, 'total': len(items)},
                ensure_ascii=False,
            )
        else:
            # Text-only search
            result = await CalendarEvents.search_events(
                user_id=user_id,
                query=query,
                skip=0,
                limit=count,
            )

            events = [_event_to_dict(item, tz) for item in result.items]
            return JSONCodec.dumps(
                {'events': events, 'total': result.total},
                ensure_ascii=False,
            )
    except Exception as e:
        log.exception(f'search_calendar_events error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def create_calendar_event(
    title: str,
    start: str,
    end: Optional[str] = None,
    description: Optional[str] = None,
    calendar_id: Optional[str] = None,
    all_day: bool = False,
    location: Optional[str] = None,
    reminder_minutes: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Create a calendar event, reminder, or alarm. Use this when the user wants to
    schedule an event, set a reminder, create an alarm, or says things like
    "remind me", "don't let me forget", "notify me at", or "add to my calendar".
    For simple reminders, omit end/location/all_day and set reminder_minutes to 0.

    :param title: Event or reminder title (e.g. "Team standup", "Take medicine", "Call mom")
    :param start: Start datetime in the user's local time (e.g. "2026-04-20 09:00")
    :param end: End datetime in the user's local time (optional — omit for reminders or point-in-time events)
    :param description: Event description or notes (optional)
    :param calendar_id: Target calendar ID (optional, uses default calendar if omitted)
    :param all_day: Whether this is an all-day event (default: false)
    :param location: Event location (optional)
    :param reminder_minutes: Minutes before the event to send a notification (optional, default: 10). Use 0 for "at time of event", -1 for no notification.
    :return: JSON with the created event details including id
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.calendar import CalendarEventForm, CalendarEvents, Calendars

        user_id = __user__.get('id')

        # Resolve calendar_id: use provided, or fall back to default
        if not calendar_id:
            calendars = await Calendars.get_calendars_by_user(user_id)
            default_cal = next((c for c in calendars if c.is_default), None)
            if not default_cal and calendars:
                default_cal = calendars[0]
            if not default_cal:
                return JSONCodec.dumps({'error': 'No calendars found. Cannot create event.'})
            calendar_id = default_cal.id

        # Verify access
        cal = await Calendars.get_calendar_by_id(calendar_id)
        if not cal:
            return JSONCodec.dumps({'error': 'Calendar not found'})
        if cal.user_id != user_id and __user__.get('role') != 'admin':
            from open_webui.models.access_grants import AccessGrants
            from open_webui.models.groups import Groups

            user_group_ids = [g.id for g in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]
            if not await AccessGrants.has_access(
                user_id=user_id,
                resource_type='calendar',
                resource_id=cal.id,
                permission='write',
                user_group_ids=set(user_group_ids),
            ):
                return JSONCodec.dumps({'error': 'Access denied to this calendar'})

        # Coerce boolean from LLM
        if isinstance(all_day, str):
            all_day = all_day.lower() in ('true', '1', 'yes')

        # Convert datetime strings to nanoseconds using user's timezone
        tz = _get_user_tz(__user__)
        try:
            start_ns = _dt_to_ns(start, tz)
        except (ValueError, TypeError) as e:
            return JSONCodec.dumps({'error': f'Invalid start datetime: {e}. Use format like "2026-04-20 09:00"'})

        end_ns = None
        if end:
            try:
                end_ns = _dt_to_ns(end, tz)
            except (ValueError, TypeError) as e:
                return JSONCodec.dumps({'error': f'Invalid end datetime: {e}. Use format like "2026-04-20 10:00"'})
        elif not all_day:
            # Default to 1 hour duration
            end_ns = start_ns + 3_600_000_000_000

        # Build meta with reminder setting
        meta = {}
        if reminder_minutes is not None:
            if isinstance(reminder_minutes, str):
                try:
                    reminder_minutes = int(reminder_minutes)
                except ValueError:
                    reminder_minutes = 10
            meta['alert_minutes'] = reminder_minutes
        else:
            meta['alert_minutes'] = 10

        form = CalendarEventForm(
            calendar_id=calendar_id,
            title=title,
            description=description,
            start_at=start_ns,
            end_at=end_ns,
            all_day=all_day,
            location=location,
            meta=meta,
        )

        event = await CalendarEvents.insert_new_event(user_id, form)
        if not event:
            return JSONCodec.dumps({'error': 'Failed to create event'})

        return JSONCodec.dumps(
            {
                'status': 'success',
                **_event_to_dict(event, tz),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'create_calendar_event error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def update_calendar_event(
    event_id: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    all_day: Optional[bool] = None,
    location: Optional[str] = None,
    is_cancelled: Optional[bool] = None,
    reminder_minutes: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Update an existing calendar event. Only provided fields are changed;
    omitted fields stay the same.

    :param event_id: The ID of the event to update
    :param title: New event title (optional)
    :param description: New event description (optional)
    :param start: New start datetime string in your local time, e.g. "2026-04-20 09:00" (optional)
    :param end: New end datetime string in your local time (optional)
    :param all_day: Whether this is an all-day event (optional)
    :param location: New event location (optional)
    :param is_cancelled: Set to true to cancel the event (optional)
    :param reminder_minutes: Minutes before the event to send a reminder notification (optional). Use 0 for "at time of event", -1 for no reminder. Accepts any positive integer for custom timing (e.g. 120 for 2 hours before).
    :return: JSON with the updated event details
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.access_grants import AccessGrants
        from open_webui.models.calendar import CalendarEvents, CalendarEventUpdateForm, Calendars
        from open_webui.models.groups import Groups

        user_id = __user__.get('id')

        event = await CalendarEvents.get_event_by_id(event_id)
        if not event:
            return JSONCodec.dumps({'error': 'Event not found'})

        # Check write access to the event's calendar
        cal = await Calendars.get_calendar_by_id(event.calendar_id)
        if not cal:
            return JSONCodec.dumps({'error': 'Access denied'})
        if cal.user_id != user_id and __user__.get('role') != 'admin':
            user_group_ids = [g.id for g in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]
            if not await AccessGrants.has_access(
                user_id=user_id,
                resource_type='calendar',
                resource_id=cal.id,
                permission='write',
                user_group_ids=set(user_group_ids),
            ):
                return JSONCodec.dumps({'error': 'Access denied'})

        # Coerce boolean strings from LLM
        if isinstance(all_day, str):
            all_day = all_day.lower() in ('true', '1', 'yes')
        if isinstance(is_cancelled, str):
            is_cancelled = is_cancelled.lower() in ('true', '1', 'yes')

        # Convert datetime strings to nanoseconds using user's timezone
        tz = _get_user_tz(__user__)
        start_ns = None
        if start is not None:
            try:
                start_ns = _dt_to_ns(start, tz)
            except (ValueError, TypeError) as e:
                return JSONCodec.dumps({'error': f'Invalid start datetime: {e}'})

        end_ns = None
        if end is not None:
            try:
                end_ns = _dt_to_ns(end, tz)
            except (ValueError, TypeError) as e:
                return JSONCodec.dumps({'error': f'Invalid end datetime: {e}'})

        # Build meta update with reminder setting if provided
        meta = None
        if reminder_minutes is not None:
            if isinstance(reminder_minutes, str):
                try:
                    reminder_minutes = int(reminder_minutes)
                except ValueError:
                    reminder_minutes = None
            if reminder_minutes is not None:
                meta = {'alert_minutes': reminder_minutes}

        update_fields = {
            'title': title,
            'description': description,
            'start_at': start_ns,
            'end_at': end_ns,
            'all_day': all_day,
            'location': location,
            'is_cancelled': is_cancelled,
            'meta': meta,
        }
        form = CalendarEventUpdateForm(**{k: v for k, v in update_fields.items() if v is not None})

        updated = await CalendarEvents.update_event_by_id(event_id, form)
        if not updated:
            return JSONCodec.dumps({'error': 'Failed to update event'})

        return JSONCodec.dumps(
            {
                'status': 'success',
                **_event_to_dict(updated, tz),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'update_calendar_event error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def delete_calendar_event(
    event_id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Delete a calendar event permanently.

    :param event_id: The ID of the event to delete
    :return: JSON confirming the event was deleted
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.access_grants import AccessGrants
        from open_webui.models.calendar import CalendarEvents, Calendars
        from open_webui.models.groups import Groups

        user_id = __user__.get('id')

        event = await CalendarEvents.get_event_by_id(event_id)
        if not event:
            return JSONCodec.dumps({'error': 'Event not found'})

        # Check write access
        cal = await Calendars.get_calendar_by_id(event.calendar_id)
        if not cal:
            return JSONCodec.dumps({'error': 'Access denied'})
        if cal.user_id != user_id and __user__.get('role') != 'admin':
            user_group_ids = [g.id for g in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]
            if not await AccessGrants.has_access(
                user_id=user_id,
                resource_type='calendar',
                resource_id=cal.id,
                permission='write',
                user_group_ids=set(user_group_ids),
            ):
                return JSONCodec.dumps({'error': 'Access denied'})

        title = event.title
        result = await CalendarEvents.delete_event_by_id(event_id)
        if not result:
            return JSONCodec.dumps({'error': 'Failed to delete event'})

        return JSONCodec.dumps(
            {
                'status': 'success',
                'message': f'Event "{title}" deleted',
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'delete_calendar_event error: {e}')
        return JSONCodec.dumps({'error': str(e)})
