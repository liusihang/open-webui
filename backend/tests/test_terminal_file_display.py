"""Exercise terminal display metadata and UI events without starting the application."""

import ast
import asyncio
import copy
import json
import mimetypes
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock


ROOT = Path(__file__).resolve().parents[2]


def functions():
    scope = {
        'Any': Any,
        'os': os,
        'mimetypes': mimetypes,
        'copy': copy,
        'JSONCodec': SimpleNamespace(loads=json.loads, JSONDecodeError=json.JSONDecodeError),
    }
    for filename, names in (
        ('middleware.py', {'build_terminal_file_tool_result', 'parse_terminal_tool_result', 'terminal_event_handler'}),
        ('tools.py', {'configure_terminal_display_file_spec'}),
    ):
        source = ROOT / 'backend/open_webui/utils' / filename
        nodes = [node for node in ast.parse(source.read_text()).body if getattr(node, 'name', '') in names]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), scope)
    return scope


def test_path_only_display_always_produces_inline_card_metadata():
    build = functions()['build_terminal_file_tool_result']
    for extra in ({}, {'inline': False}, {'inline': True}):
        result = build(
            'display_file',
            {'path': '/home/user/report.docx', **extra},
            {'path': '/home/user/report.docx', 'exists': True, 'mime_type': 'application/octet-stream'},
            {'tool_id': 'terminal:terminals'},
            {'chat_id': 'source-session'},
        )
        assert result['displayed'] is True
        assert result['terminal_selector'] == 'terminals'
        assert result['session_id'] == 'source-session'
        assert result['mime_type'] == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        assert result['content_type'] == result['mime_type']


def test_direct_terminal_result_preserves_resolved_path_and_page():
    result = functions()['build_terminal_file_tool_result'](
        'display_file',
        {'path': 'report.pdf', 'page': 3},
        ({'path': '/home/user/report.pdf', 'exists': True}, {}),
        {'server': {'url': 'https://terminal.example'}},
        {'chat_id': 'session'},
    )
    assert result['path'] == '/home/user/report.pdf'
    assert result['terminal_selector'] == 'https://terminal.example'
    assert result['page'] == 3
    assert result['displayed'] is True


def test_office_mime_types_do_not_depend_on_the_host_mime_database():
    scope = functions()
    scope['mimetypes'] = SimpleNamespace(guess_type=lambda path: (None, None))
    for extension, expected in (
        ('docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
        ('xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
        ('xls', 'application/vnd.ms-excel'),
        ('pptx', 'application/vnd.openxmlformats-officedocument.presentationml.presentation'),
    ):
        path = '/home/user/report.' + extension
        result = scope['build_terminal_file_tool_result'](
            'display_file',
            {'path': path},
            {'path': path, 'exists': True},
            {'tool_id': 'terminal:terminals'},
            {'chat_id': 'session'},
        )
        assert result['mime_type'] == expected


def test_missing_or_invalid_files_do_not_become_cards_or_open_previews():
    funcs = functions()
    for result in ({'exists': False}, {'error': 'File not found'}, {}):
        assert (
            funcs['build_terminal_file_tool_result'](
                'display_file',
                {'path': '/missing.docx'},
                result,
                {'tool_id': 'terminal:terminals'},
                {'chat_id': 'session'},
            )
            is None
        )
        emitter = AsyncMock()
        asyncio.run(
            funcs['terminal_event_handler']('display_file', {'path': '/missing.docx'}, json.dumps(result), emitter)
        )
        emitter.assert_not_awaited()


def test_sidebar_signal_is_available_alongside_inline_cards():
    for extra in ({}, {'inline': True}):
        emitter = AsyncMock()
        asyncio.run(
            functions()['terminal_event_handler'](
                'display_file',
                {'path': 'report.pdf', 'page': 2, **extra},
                json.dumps({'path': '/home/user/report.pdf', 'exists': True}),
                emitter,
            )
        )
        emitter.assert_awaited_once_with(
            {
                'type': 'terminal:display_file',
                'data': {'path': '/home/user/report.pdf', 'page': 2},
            }
        )


def test_model_schema_does_not_require_a_display_mode():
    spec = {
        'name': 'display_file',
        'description': 'Display a file.',
        'parameters': {
            'type': 'object',
            'properties': {'path': {'type': 'string'}, 'inline': {'type': 'boolean'}},
            'required': ['path'],
        },
    }
    result = functions()['configure_terminal_display_file_spec'](spec)
    assert 'inline' not in result['parameters']['properties']
    assert result['parameters']['required'] == ['path']
    assert result['parameters']['properties']['page']['minimum'] == 1
    assert 'inline chat card' in result['description']
    assert 'inline' in spec['parameters']['properties']  # shared discovery schemas are not mutated


def test_uploaded_file_result_keeps_builtin_authorization_source_and_has_no_terminal_event():
    result = {
        'type': 'file',
        'source': 'open_webui',
        'exists': True,
        'displayed': True,
        'path': '/api/v1/files/11111111-1111-4111-8111-111111111111/content',
    }
    funcs = functions()
    assert (
        funcs['build_terminal_file_tool_result'](
            'display_file',
            {'path': result['path']},
            result,
            {'type': 'builtin'},
            {'terminal_id': 'selected-terminal', 'chat_id': 'session'},
        )
        is None
    )
    emitter = AsyncMock()
    asyncio.run(funcs['terminal_event_handler']('display_file', {'path': result['path']}, json.dumps(result), emitter))
    emitter.assert_not_awaited()
