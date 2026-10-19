"""Route chat PDFs through native input or the existing, authorized RAG parser.

PDF support is opt-in: ``info.meta.capabilities.native_pdf is True``. Neither
vision support nor a client-supplied native marker proves provider support.
"""

import asyncio
import base64
from pathlib import Path
from weakref import WeakValueDictionary

from fastapi import HTTPException

CHAT_NATIVE_PDF_MAX_BYTES = 25 * 1024 * 1024
CHAT_PDF_MAX_PAGES = 20
_parse_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def supports_native_pdf(model: dict | None) -> bool:
    return ((model or {}).get('info', {}).get('meta', {}).get('capabilities') or {}).get('native_pdf') is True


def is_pdf(filename: str, content_type: str | None) -> bool:
    mime = str(content_type or '').split(';', 1)[0].strip().lower()
    return mime == 'application/pdf' or str(filename or '').lower().endswith('.pdf')


def validate_chat_pdf_upload(metadata: dict, filename: str, content_type: str | None, stream) -> int | None:
    """Admit new chat PDFs from their bytes before persisting or indexing them.

    Knowledge/channel uploads keep their existing parser path. Client page
    counts and model capabilities are deliberately irrelevant to admission.
    Restore the stream even when admission fails, so storage reads the original
    bytes on success. No decryption is attempted, including empty passwords.
    """
    if metadata.get('upload_context') != 'chat' or metadata.get('knowledge_id') or metadata.get('channel_id'):
        return None
    position = stream.tell()
    try:
        stream.seek(0)
        header = stream.read(1024)
        if not is_pdf(filename, content_type) and b'%PDF-' not in header:
            return None
        stream.seek(0)
        from pypdf import PdfReader

        class PageCountReader(PdfReader):
            def _handle_encryption(self, password):
                # PdfReader otherwise tries an empty password during its
                # constructor. Reject at that hook, before any decryption.
                raise HTTPException(
                    status_code=400,
                    detail='此 PDF 已加密，无法核验页数。请上传未加密且不超过 20 页的 PDF，或上传到知识库后引用。',
                )

        try:
            reader = PageCountReader(stream, strict=True)
            pages = len(reader.pages)
            if pages < 1:
                raise ValueError('PDF has no pages')
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail='无法读取此 PDF 的页数，文件可能已损坏或格式不受支持。请修复文件后重试，或上传到知识库后引用。',
            ) from exc
        if pages > CHAT_PDF_MAX_PAGES:
            raise HTTPException(
                status_code=400,
                detail=f'此 PDF 共 {pages} 页，聊天直接上传最多允许 {CHAT_PDF_MAX_PAGES} 页。请上传到知识库后引用。',
            )
        return pages
    finally:
        stream.seek(position)


def should_defer_pdf_processing(
    metadata: dict, filename: str, content_type: str | None, contents: bytes, models: dict
) -> bool:
    """Only skip upload parsing when every selected model explicitly supports PDF."""
    model_ids = metadata.get('model_ids')
    return bool(
        metadata.get('upload_context') == 'chat'
        and not metadata.get('knowledge_id')
        and not metadata.get('channel_id')
        and is_pdf(filename, content_type)
        and contents.startswith(b'%PDF-')
        and len(contents) <= CHAT_NATIVE_PDF_MAX_BYTES
        and isinstance(model_ids, list)
        and model_ids
        and all(isinstance(model_id, str) and supports_native_pdf(models.get(model_id)) for model_id in model_ids)
    )


def attachment_file_id(item: dict) -> str | None:
    if not isinstance(item, dict) or item.get('type', 'file') != 'file':
        return None
    uploaded = item.get('file')
    file_id = item.get('id') or (uploaded.get('id') if isinstance(uploaded, dict) else None)
    if not isinstance(file_id, str) or not file_id or file_id.startswith(('http:', 'https:', 'data:', '/')):
        return None
    return file_id


def is_knowledge_attachment(item: dict) -> bool:
    if item.get('type', 'file') != 'file' or item.get('source') in ('knowledge', 'model', 'folder'):
        return True
    if item.get('legacy') or item.get('collection_names') or item.get('knowledge_id'):
        return True
    collection = item.get('collection_name') or item.get('collection')
    # Normal chat uploads also carry their own file-{id} vector collection.
    return bool(collection and collection != f'file-{attachment_file_id(item)}')


def is_native_pdf_attachment(item: dict, metadata: dict) -> bool:
    return bool(
        isinstance(item, dict)
        and not is_knowledge_attachment(item)
        and attachment_file_id(item) in (metadata.get('native_pdf_file_ids') or [])
    )


def chat_pdf_admission_ids(
    files: list[dict] | None, current_user_message: dict | None, historical_messages: list[dict]
) -> set[str]:
    """Select new direct attachments, not old PDFs replayed from server history.

    The caller supplies the pre-compaction server-loaded history. Client history
    must not populate this exemption set. Explicitly attaching an old file to
    the current user message still counts as a new attachment.
    """

    def direct_ids(items):
        return {
            attachment_file_id(item)
            for item in (items or [])
            if isinstance(item, dict) and not is_knowledge_attachment(item) and attachment_file_id(item)
        }

    historical_ids = set().union(
        *(direct_ids(message.get('files')) for message in historical_messages if message.get('role') == 'user')
    )
    return direct_ids((current_user_message or {}).get('files')) | (direct_ids(files) - historical_ids)


def _append_parts(message: dict, parts: list[dict]) -> None:
    content = message.get('content', '')
    content = list(content) if isinstance(content, list) else [{'type': 'text', 'text': str(content or '')}]
    for part in parts:
        if part not in content:
            content.append(part)
    message['content'] = content


def carry_native_pdf_parts(body: dict, previous_messages: list[dict]) -> dict:
    """Keep already-authorized PDFs when a tool loop rebuilds/compacts history.

    Stateful Responses continuations already reference the provider's original
    input; re-sending the PDF there would duplicate it. No storage or new file
    access is performed by this function.
    """
    if body.get('previous_response_id'):
        return body
    messages = body.get('messages') or []
    parts = [
        part
        for message in previous_messages
        if message.get('role') == 'user' and isinstance(message.get('content'), list)
        for part in message['content']
        if isinstance(part, dict)
        and part.get('type') == 'file'
        and isinstance(part.get('file'), dict)
        and str(part['file'].get('file_data') or '').startswith('data:application/pdf;base64,')
    ]
    existing = [part for message in messages if isinstance(message.get('content'), list) for part in message['content']]
    missing = [part for part in parts if part not in existing]
    if missing:
        target = next((message for message in reversed(messages) if message.get('role') == 'user'), None)
        if target is None:
            target = {'role': 'user', 'content': []}
            messages.insert(1 if messages and messages[0].get('role') == 'system' else 0, target)
        _append_parts(target, missing)
        body['messages'] = messages
    return body


async def _native_part(record) -> dict | None:
    from open_webui.storage.provider import Storage

    if not record.path:
        return None

    def read_pdf():
        resolved = Storage.get_file(record.path)
        with Path(resolved).open('rb') as stream:
            return stream.read(CHAT_NATIVE_PDF_MAX_BYTES + 1)

    try:
        contents = await asyncio.to_thread(read_pdf)
    except (OSError, ValueError):
        return None
    if not contents.startswith(b'%PDF-') or len(contents) > CHAT_NATIVE_PDF_MAX_BYTES:
        return None
    return {
        'type': 'file',
        'file': {
            'filename': Path(record.filename).name,
            'file_data': 'data:application/pdf;base64,' + base64.b64encode(contents).decode('ascii'),
        },
    }


async def _parsed_fallback(request, record, user):
    from open_webui.models.files import Files

    # Multi-model requests can ask for the same deferred PDF concurrently.
    # Re-read inside this process-local lock rather than extracting twice.
    lock = _parse_locks.setdefault(record.id, asyncio.Lock())
    async with lock:
        fresh = await Files.get_file_by_id(record.id)
        if not fresh:
            raise HTTPException(status_code=404, detail='PDF attachment not found.')
        return await _process_pdf_fallback(request, fresh, user)


async def _process_pdf_fallback(request, record, user):
    from open_webui.internal.db import get_async_db
    from open_webui.models.files import Files
    from open_webui.routers.retrieval import ProcessFileForm, process_file
    from open_webui.utils.access_control.files import has_access_to_file

    data = record.data or {}
    if 'content' in data and data.get('status') != 'failed':
        return record
    # Reuse the existing parser's write gate. Shared read access must not
    # acquire indexing privileges when switching to a non-PDF model.
    if data.get('status') in ('pending', 'processing'):
        raise HTTPException(status_code=409, detail='PDF attachment is still being processed. Retry when ready.')
    if not (record.user_id == user.id or user.role == 'admin' or await has_access_to_file(record.id, 'write', user)):
        raise HTTPException(
            status_code=400,
            detail='This model needs parsed PDF text. Ask the file owner to process the attachment first.',
        )
    # Direct calls do not resolve FastAPI dependencies. The official parser
    # commits the supplied session before generating embeddings.
    async with get_async_db() as db:
        result = await process_file(request, ProcessFileForm(file_id=record.id), user=user, db=db)
    if not result or result.get('status') is not True:
        raise HTTPException(status_code=400, detail='PDF text extraction failed.')
    record = await Files.get_file_by_id(record.id)
    if not record or (record.data or {}).get('status') == 'failed' or 'content' not in (record.data or {}):
        raise HTTPException(status_code=400, detail='PDF text extraction failed.')
    return record


async def _resolve_chat_pdf(request, file_id, blocked_ids, user, model, *, validate_new_attachment=False):
    from open_webui.models.files import Files
    from open_webui.models.knowledge import Knowledges
    from open_webui.utils.access_control.files import has_access_to_file

    record = await Files.get_file_by_id(file_id)
    if not record:
        return None, None
    pdf_record = is_pdf(record.filename, (record.meta or {}).get('content_type'))
    if not pdf_record and not validate_new_attachment:
        return None, None
    if not (record.user_id == user.id or user.role == 'admin' or await has_access_to_file(file_id, 'read', user)):
        raise HTTPException(status_code=404, detail='PDF attachment not found or not accessible.')

    if validate_new_attachment:
        from open_webui.storage.provider import Storage

        def validate_stored_file():
            if not record.path and not pdf_record:
                return
            try:
                with Path(Storage.get_file(record.path)).open('rb') as stream:
                    validate_chat_pdf_upload(
                        {'upload_context': 'chat'}, record.filename, (record.meta or {}).get('content_type'), stream
                    )
            except (OSError, TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail='无法读取附件以核验 PDF 页数。请重新上传文件，或上传到知识库后引用。'
                ) from exc

        await asyncio.to_thread(validate_stored_file)
    if not pdf_record:
        return None, None

    stored = (record.meta or {}).get('data') or {}
    stored = stored if isinstance(stored, dict) else {}
    # A knowledge-linked PDF is never converted into a native chat upload.
    chat_upload = bool(
        (stored.get('upload_context') == 'chat' or stored.get('native_model_file') is True)
        and not stored.get('knowledge_id')
        and not stored.get('channel_id')
        and file_id not in blocked_ids
        and not await Knowledges.get_knowledges_by_file_id(file_id)
    )
    if not chat_upload:
        return None, None
    part = await _native_part(record) if supports_native_pdf(model) else None
    if part:
        return record, part
    return await _parsed_fallback(request, record, user), None


def _place_native_parts(messages, native_parts):
    placed_ids = set()
    for message in messages:
        if message.get('role') != 'user':
            continue
        ids = [attachment_file_id(item) for item in (message.get('files') or []) if isinstance(item, dict)]
        parts = [native_parts[file_id] for file_id in ids if file_id in native_parts]
        if parts:
            _append_parts(message, parts)
        placed_ids.update(file_id for file_id in ids if file_id in native_parts)
        # Do not also advertise an unparsed native PDF as indexed file text.
        if message.get('files'):
            message['files'] = [item for item in message['files'] if attachment_file_id(item) not in native_parts]

    remaining = [part for file_id, part in native_parts.items() if file_id not in placed_ids]
    if remaining:
        target = next((message for message in reversed(messages) if message.get('role') == 'user'), None)
        if target is None:
            raise HTTPException(status_code=400, detail='Native PDF attachments need a user message.')
        _append_parts(target, remaining)


async def prepare_native_pdf_attachments(
    request,
    messages: list[dict],
    files: list[dict] | None,
    user,
    model: dict,
    *,
    new_attachment_ids: set[str] | None = None,
):
    """Authorize using server records, then inject native parts or parse fallback.

    Called before message ``files`` are stripped. Returns confirmed native IDs
    for retrieval/tool exclusions, never trusting nested client file metadata.
    The original attachment list remains available to the official subagents.
    """
    message_items = [
        item
        for message in messages
        if message.get('role') == 'user'
        for item in (message.get('files') or [])
        if isinstance(item, dict)
    ]
    all_items = [*(files or []), *message_items]
    model_knowledge = model.get('info', {}).get('meta', {}).get('knowledge') or []
    blocked_ids = {
        attachment_file_id(item) for item in all_items if isinstance(item, dict) and is_knowledge_attachment(item)
    }
    blocked_ids.update(attachment_file_id(item) for item in model_knowledge if isinstance(item, dict))
    native_parts = {}
    seen = set()
    for item in all_items:
        file_id = attachment_file_id(item)
        if not file_id or file_id in seen or is_knowledge_attachment(item):
            continue
        seen.add(file_id)
        record, part = await _resolve_chat_pdf(
            request,
            file_id,
            blocked_ids,
            user,
            model,
            validate_new_attachment=file_id in (new_attachment_ids or ()),
        )
        if part:
            native_parts[file_id] = part
        elif record:
            # Refresh snapshots after lazy extraction for full-context RAG and
            # file tools; don't change the original knowledge/file scopes.
            for attached in all_items:
                if attachment_file_id(attached) == file_id:
                    attached['file'] = record.model_dump()
                    if (record.meta or {}).get('collection_name'):
                        attached['collection_name'] = record.meta['collection_name']
    _place_native_parts(messages, native_parts)
    return list(native_parts)
