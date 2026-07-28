#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import importlib.util
import json
import re

from open_webui.models.tools import Tools
from open_webui.utils.plugin import extract_frontmatter, replace_imports

TOOL_IDS = ('web_search_and_crawl', 'sub_agent')


def requirement_modules(requirements: str | None) -> dict[str, bool]:
    if not requirements:
        return {}
    result = {}
    for requirement in requirements.split(','):
        package = re.split(r'[<>=!~\[]', requirement.strip(), maxsplit=1)[0]
        module = package.replace('-', '_')
        if module:
            result[package] = importlib.util.find_spec(module) is not None
    return result


async def inspect_tool(tool_id: str) -> dict:
    tool = await Tools.get_tool_by_id(tool_id)
    if tool is None:
        return {'tool_found': False, 'tool_id': tool_id}
    frontmatter = extract_frontmatter(replace_imports(tool.content))
    requirements = frontmatter.get('requirements')
    return {
        'tool_found': True,
        'tool_id': tool.id,
        'requirements': requirements,
        'requirement_modules_installed': requirement_modules(requirements),
    }


async def inspect() -> list[dict]:
    return [await inspect_tool(tool_id) for tool_id in TOOL_IDS]


def main() -> int:
    print(json.dumps(asyncio.run(inspect()), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
