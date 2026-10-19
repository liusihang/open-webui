"""Offline regressions for the native search-media seam; no app/DB imports."""

import ast
import asyncio
import copy
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


BACKEND = Path(__file__).resolve().parents[1]
SOURCE = BACKEND / 'open_webui/utils/middleware.py'
IMAGE = {'type': 'image', 'url': 'https://example.org/fixture.png'}


def media_functions():
    tree = ast.parse(SOURCE.read_text())
    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in ('extract_base64_images', 'process_tool_result')
    ]
    # HTTP media processing must not need framework, DB, storage or network.
    scope = {
        'Any': Any,
        'HTMLResponse': type('HTMLResponse', (), {}),
        'json': json,
        're': re,
        'urlsplit': urlsplit,
        'BASE64_IMAGE_DATA_URI_RE': re.compile(r'data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/]+={0,2}'),
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), scope)
    return scope['process_tool_result']


def run_media(payload, name='search_web', tool_type='', direct=False):
    return asyncio.run(media_functions()(None, name, payload, tool_type, direct, {}, None))[1]


def test_explicit_search_media_becomes_native_attachments():
    for tool_type in ('', 'local'):
        assert run_media({'media': [IMAGE]}, tool_type=tool_type) == [IMAGE]
    assert run_media({'media': [IMAGE]}, name='gpt_web_search') == [IMAGE]


def test_other_tools_fields_and_direct_connections_are_not_promoted():
    assert run_media({'media': [IMAGE]}, name='imagegenerator') == []
    assert run_media({'results': [IMAGE], 'url': IMAGE['url']}) == []
    assert run_media({'media': [IMAGE]}, tool_type='mcp') == []
    assert run_media({'media': [IMAGE]}, tool_type='external') == []
    assert run_media({'media': [IMAGE]}, direct=True) == []
    assert run_media({'media': [{'type': 'page', 'url': IMAGE['url']}]}) == []


def test_url_validation_deduplication_and_existing_five_image_limit():
    bad_urls = [
        'file:///tmp/image.png',
        'javascript:alert(1)',
        'https:///image.png',
        'https://user:password@example.org/image.png',
        'https://example.org:invalid/image.png',
        'https://example.org/\nimage.png',
        'https://example.org/' + 'a' * 2048,
    ]
    assert run_media({'media': [{'type': 'image', 'url': url} for url in bad_urls]}) == []
    assert run_media({'media': [IMAGE] * 8}) == [IMAGE]
    images = [{'type': 'image', 'url': f'https://example.org/{i}.png'} for i in range(8)]
    assert run_media({'media': images}) == images[:5]


def test_existing_data_uri_extraction_is_unchanged():
    image = {'type': 'image', 'url': 'data:image/png;base64,aGVsbG8='}
    assert run_media({'media': [image]}) == [image]


def test_terminal_display_reference_is_not_automatically_sent_as_a_model_vision_input():
    result = {
        'source': 'open_terminal',
        'path': '/home/user/chart.png',
        'image_url': '/api/v1/files/11111111-1111-4111-8111-111111111111/content',
    }
    assert run_media(result, name='display_file', tool_type='terminal') == []


def test_both_native_loops_respect_existing_vision_capability():
    tree = ast.parse(SOURCE.read_text())
    loops = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.For)
        and isinstance(node.target, ast.Name)
        and node.target.id == 'file_item'
        and ast.unparse(node.iter) in ('files', "result.get('files', [])")
    ]
    assert len(loops) == 2  # initial loop and approval/resume loop
    for loop in loops:
        function = ast.AsyncFunctionDef(
            name='native_loop',
            args=ast.arguments(
                posonlyargs=[],
                args=[ast.arg(arg=name) for name in ('model', 'files', 'result')],
                kwonlyargs=[],
                kw_defaults=[],
                defaults=[],
            ),
            body=(
                ast.parse("output_parts=[{'type':'input_text','text':'fixture'}]\ndisplay_files=[]").body
                + [copy.deepcopy(loop)]
                + ast.parse('return output_parts,display_files').body
            ),
            decorator_list=[],
        )
        scope = {}
        exec(
            compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), str(SOURCE), 'exec'),
            scope,
        )
        for vision in (True, False, None):
            model = {'info': {'meta': {'capabilities': {'vision': vision}}}} if vision is not None else {}
            parts, display = asyncio.run(scope['native_loop'](model, [IMAGE], {'files': [IMAGE]}))
            assert display == [IMAGE]
            assert sum(part['type'] == 'input_image' for part in parts) == (1 if vision else 0)
