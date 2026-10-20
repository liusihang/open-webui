"""Shared source normalization and bounded presentation for knowledge tools.

No DB, encoder or authorization is created here. Callers resolve permissions
before passing file content or stored evidence into these pure helpers.
"""


def knowledge_entries(model_knowledge=(), metadata=None, files=(), *, knowledge_enabled=True, native_pdf=None):
    metadata = metadata or {}
    entries = [
        *(model_knowledge if knowledge_enabled else ()),
        *((metadata.get('folder_knowledge') or ()) if knowledge_enabled else ()),
        *(metadata.get('files') or ()),
        *(files or ()),
    ]
    result, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        kind = entry.get('type', 'file')
        identity = entry.get('id') or (entry.get('url') if kind == 'file' else None)
        if kind not in {'collection', 'file', 'note'} or not isinstance(identity, str) or not identity:
            continue
        if identity.startswith(('http://', 'https://', 'data:')) or (kind != 'file' and not knowledge_enabled):
            continue
        if kind == 'file' and native_pdf and native_pdf(entry, metadata):
            continue
        key = kind, identity
        if key not in seen:
            result.append({**entry, 'type': kind, 'id': identity})
            seen.add(key)
    return result


def file_content_page(
    file, *, offset=0, max_chars=10000, line_numbers=False, start_line=None, end_line=None, knowledge=None
):
    content = (file.data or {}).get('content', '') or ''
    offset, max_chars = max(0, int(offset)), max(1, int(max_chars))
    result = {'id': file.id, 'filename': file.filename, 'updated_at': file.updated_at, 'created_at': file.created_at}
    if knowledge:
        result.update(knowledge_id=knowledge['id'], knowledge_name=knowledge['name'])
    if start_line is not None:
        lines = content.split('\n')
        start = min(max(1, int(start_line)) - 1, len(lines))
        end = min(len(lines), max(start, int(end_line)) if end_line is not None else start + 100)
        offset = sum(len(line) + 1 for line in lines[:start])
        raw = '\n'.join(lines[start:end])
        result['total_lines'] = len(lines)
        line_numbers = True
    else:
        raw = content[offset : offset + max_chars]
    if line_numbers and raw:
        pieces, consumed, remaining = [], 0, max_chars
        number = content[:offset].count('\n') + 1
        lines = raw.split('\n')
        for index, line in enumerate(lines):
            prefix = f'{number + index}: '
            room = remaining - len(prefix)
            if room <= 0:
                if consumed == 0:
                    raise ValueError('max_chars is too small for a line-number prefix')
                break
            source = line + ('\n' if index < len(lines) - 1 else '')
            selected = source[:room]
            pieces.append(prefix + selected)
            consumed += len(selected)
            remaining -= len(prefix) + len(selected)
            if len(selected) < len(source):
                break
        rendered = ''.join(pieces)
    else:
        rendered, consumed = raw[:max_chars], min(len(raw), max_chars)
    result['content'] = rendered
    next_offset = min(len(content), offset + consumed)
    if start_line is not None:
        final_line = content[:next_offset].count('\n') + int(
            next_offset > offset and content[next_offset - 1 : next_offset] != '\n'
        )
        result['showing_lines'] = f'{start + 1}-{max(start + 1, final_line)}' if raw else ''
    truncated = next_offset < len(content)
    if truncated or offset > 0:
        result.update(truncated=truncated, total_chars=len(content), returned_chars=consumed, offset=offset)
        if truncated:
            result['next_offset'] = next_offset
            if next_offset == 0 or content[next_offset - 1 : next_offset] == '\n':
                result['next_start_line'] = content[:next_offset].count('\n') + 1
    return result


def model_evidence_result(result, *, max_chars=10000, offset=0):
    """One body per hit and one shared text budget, retaining stable locators."""
    projected = {key: value for key, value in result.items() if key != 'encoding_space'}
    budget = max(1, int(max_chars))
    offset = max(0, int(offset))
    clipped = False
    for key in ('items', 'legacy_items'):
        if key not in result:
            continue
        items = []
        for original in result[key]:
            item = {
                name: value
                for name, value in original.items()
                if name not in {'preview', 'score', 'score_type', 'vector_scores'}
            }
            if isinstance(item.get('content'), str):
                text = item['content']
                item['content'] = text[offset : offset + budget]
                used = len(item['content'])
                budget -= used
                if offset or offset + used < len(text):
                    item.update(
                        content_offset=offset,
                        returned_chars=used,
                        total_chars=len(text),
                        truncated=offset + used < len(text),
                    )
                    if item['truncated']:
                        item['next_offset'] = offset + used
                        clipped = True
            items.append(item)
        projected[key] = items
    if clipped:
        projected['warnings'] = [
            *(result.get('warnings') or []),
            'Evidence text budget reached. Read the required evidence_ref with view_knowledge_file; '
            'use its next_offset to continue within that evidence.',
        ]
    return projected
