import asyncio
import errno
import hashlib
import logging
import os
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, Response, StreamingResponse
from open_webui.config import BYPASS_ADMIN_ACCESS_CONTROL, STORAGE_LOCAL_CACHE, STORAGE_PROVIDER, UPLOAD_DIR
from open_webui.constants import ERROR_MESSAGES
from open_webui.events import EVENTS, publish_event
from open_webui.internal.db import get_async_db_context, get_async_session
from open_webui.models.access_grants import AccessGrants
from open_webui.models.channels import Channels
from open_webui.models.chats import Chats
from open_webui.models.config import Config
from open_webui.models.files import (
    FileForm,
    FileListResponse,
    FileModel,
    FileModelResponse,
    Files,
)
from open_webui.models.groups import Groups
from open_webui.models.knowledge import Knowledges
from open_webui.models.users import Users
from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT
from open_webui.routers.audio import transcribe
from open_webui.routers.retrieval import ProcessFileForm, process_file
from open_webui.storage.provider import Storage
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.misc import strict_match_mime_type
from open_webui.utils.native_pdf import should_defer_pdf_processing, validate_chat_pdf_upload
from open_webui.utils.access_control.files import has_access_to_file
from open_webui.utils.json_codec import JSONCodec
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
log = logging.getLogger(__name__)


async def _evidence_for_file(request, file_id, ref, user):
    from open_webui.retrieval.integration import services
    from open_webui.retrieval.evidence import resolve_scope

    scope = await resolve_scope(user, file_ids=(file_id,))
    _, evidence, _, _ = await services(request)
    result = await evidence.read([ref], user=user, resolved_scope=scope)
    item = next(
        (i for i in result['items'] if i.get('status') == 'ready' and i.get('source', {}).get('file_id') == file_id),
        None,
    )
    if not item:
        raise HTTPException(404, 'Evidence is unavailable or its source has changed')
    return evidence, scope, item


@router.get('/{id}/retrieval/status')
async def get_retrieval_status(id: str, user=Depends(get_verified_user)):
    from open_webui.retrieval.evidence import resolve_scope

    if id not in (await resolve_scope(user, file_ids=(id,))).file_ids:
        raise HTTPException(404, 'File not found')
    file = await Files.get_file_by_id(id)
    if not file:
        raise HTTPException(404, 'File not found')
    cached = (file.data or {}).get('retrieval_status')
    if cached:
        return cached
    # Selected-vector imports deliberately do not restore the old file.data cache.
    # Report durable current-version mappings without making an encoder call or
    # equating stored vectors with a compatible, selected search space.
    from sqlalchemy import func, select
    from open_webui.models.evidence import KnowledgeEvidence, KnowledgeEvidenceEmbedding
    from open_webui.models.evidence import stable_id
    from open_webui.models.retrieval_indexes import RetrievalIndexState

    async with get_async_db_context() as db:
        state = await db.get(RetrievalIndexState, stable_id('file', file_id=id))
        if state is None:
            return {'text': 'unindexed', 'images': 'unindexed'}
        if state.status == 'deleted':
            return {
                'version': state.file_version,
                'text': 'deleted',
                'images': 'deleted',
                'status_source': 'sql',
                'image_retryable': False,
            }
        rows = (
            await db.execute(
                select(
                    KnowledgeEvidence.modality,
                    KnowledgeEvidenceEmbedding.embedding_status,
                    func.count(func.distinct(KnowledgeEvidence.id)),
                )
                .outerjoin(KnowledgeEvidenceEmbedding, KnowledgeEvidenceEmbedding.evidence_id == KnowledgeEvidence.id)
                .where(
                    KnowledgeEvidence.file_id == id,
                    KnowledgeEvidence.file_version == state.file_version,
                    KnowledgeEvidence.is_active.is_(True),
                    KnowledgeEvidence.deleted_at.is_(None),
                )
                .group_by(KnowledgeEvidence.modality, KnowledgeEvidenceEmbedding.embedding_status)
            )
        ).all()

    def modality_status(modality):
        statuses = {status for kind, status, count in rows if kind == modality and count}
        if not statuses:
            return 'unindexed'
        if 'quarantined' in statuses:
            return 'quarantined'
        if statuses == {'ready'}:
            return 'indexed'  # Stored mapping, not a claim that the current encoder can search it.
        return 'unassigned' if statuses == {None} else 'partial'

    return {
        'version': state.file_version,
        'text': modality_status('text'),
        'images': modality_status('image'),
        'status_source': 'sql',
        'image_retryable': False,
        'searchable_evidence_count': 0,
        'indexed_evidence_count': sum(count for _, status, count in rows if status),
        'message': '已有索引；隔离项的编码空间尚未确认，无需重新上传或重建。',
    }


@router.post('/{id}/retrieval/retry-images')
async def retry_file_image_index(
    request: Request, id: str, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    from open_webui.retrieval.ingestion import submit_file

    state = await Files.get_file_processing_state(id)
    if not state or not (
        state['user_id'] == user.id or user.role == 'admin' or await has_access_to_file(id, 'write', user, db=db)
    ):
        raise HTTPException(404, 'File not found')
    if not state.get('retrieval_version'):
        raise HTTPException(409, 'No published source version to retry')
    queued = await submit_file(request, ProcessFileForm(file_id=id), user=user, db=db, wait=False, retry_images=True)
    return {'scheduled': 1, 'version': state['retrieval_version'], **queued}


@router.get('/{id}/evidence/{ref}')
async def get_file_evidence(request: Request, id: str, ref: str, user=Depends(get_verified_user)):
    _, _, item = await _evidence_for_file(request, id, ref, user)
    return item


@router.get('/{id}/evidence/{ref}/content')
@router.get('/{id}/evidence/{ref}/thumbnail')
async def get_file_evidence_image(request: Request, id: str, ref: str, user=Depends(get_verified_user)):
    evidence, scope, _ = await _evidence_for_file(request, id, ref, user)
    try:
        content, mime = await evidence.image_content(ref, user=user, resolved_scope=scope)
        if request.url.path.endswith('/thumbnail'):
            import io
            from PIL import Image

            def thumbnail():
                with Image.open(io.BytesIO(content)) as image:
                    image = image.convert('RGB')
                    image.thumbnail((480, 480))
                    output = io.BytesIO()
                    image.save(output, 'JPEG', quality=80)
                    return output.getvalue()

            content, mime = await asyncio.to_thread(thumbnail), 'image/jpeg'
        # Read authorization again after IO/resize; no permanent/public asset URLs.
        await _evidence_for_file(request, id, ref, user)
        return Response(
            content=content,
            media_type=mime,
            headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'},
        )
    except (ValueError, PermissionError, OSError):
        raise HTTPException(404, 'Image evidence is unavailable')


############################
# Upload File
# What was entrusted here was given in good faith. Let it
# be returned the same way, whole and undiminished.
############################


def _is_text_file(file_path: str, chunk_size: int = 8192) -> bool:
    """Check if a file is likely a text file by reading a chunk and decoding it.

    Tries UTF-8 first, then falls back to Latin-1 (which accepts every byte
    in 0x00–0xFF) so that legacy-encoded files from Windows environments are
    not misclassified as binary.

    This catches files whose extensions are mis-mapped by mimetypes/browsers
    (e.g. TypeScript .ts → video/mp2t) without maintaining an extension whitelist.
    """
    try:
        resolved = Storage.get_file(file_path)
        with open(resolved, 'rb') as f:
            chunk = f.read(chunk_size)
        if not chunk:
            return False
        # Null bytes are a strong indicator of binary content
        if b'\x00' in chunk:
            return False
        try:
            chunk.decode('utf-8')
        except UnicodeDecodeError:
            # Latin-1 always succeeds (every byte is valid), so this
            # effectively just means "the file has no null bytes and is
            # therefore likely text, even if not valid UTF-8".
            chunk.decode('latin-1')
        return True
    except Exception:
        return False


def _cleanup_local_cache(file_path: str) -> None:
    """Remove the local cached copy of a cloud-stored file after processing."""
    if STORAGE_LOCAL_CACHE or STORAGE_PROVIDER == 'local':
        return
    try:
        local_filename = os.path.basename(file_path)
        local_path = os.path.join(UPLOAD_DIR, local_filename)
        if os.path.isfile(local_path):
            os.remove(local_path)
            log.debug('Cleaned up local cache: %s', local_path)
    except OSError as e:
        log.warning(f'Failed to clean up local cache for {file_path}: {e}')


def _matches_configured_mime_type(supported: list[str] | str, content_type: str) -> bool:
    if isinstance(supported, str):
        supported = supported.split(',')
    supported = [item.strip() for item in (supported or []) if item.strip()]
    if not supported:
        return False
    return bool(strict_match_mime_type(supported, content_type))


def _media_supported_for_extraction(
    content_extraction_engine: str | None, supported: list[str] | str | None, content_type: str
) -> bool:
    if supported is None:
        return content_extraction_engine == 'external'
    return bool(content_extraction_engine and _matches_configured_mime_type(supported, content_type))


async def process_uploaded_file(
    request,
    file,
    file_path,
    file_item,
    file_metadata,
    user,
    db: Optional[AsyncSession] = None,
):
    async def _process_handler(db_session):
        try:
            content_type = file.content_type

            # Detect mis-labeled text files (e.g. .ts → video/mp2t)
            if content_type and content_type.startswith(('image/', 'video/')):
                if _is_text_file(file_path):
                    content_type = 'text/plain'

            stt_supported = await Config.get('audio.stt.supported_content_types', [])
            content_extraction_engine = await Config.get('rag.content_extraction_engine')
            content_extraction_supported_media_mime_types = await Config.get(
                'rag.content_extraction.supported_media_mime_types'
            )

            if content_type and strict_match_mime_type(stt_supported, content_type):
                # Audio / STT-supported files → transcribe then index
                file_path_processed = await asyncio.to_thread(Storage.get_file, file_path)
                result = await transcribe(
                    request,
                    file_path_processed,
                    file_metadata,
                    user,
                )
                await process_file(
                    request,
                    ProcessFileForm(file_id=file_item.id, content=result.get('text', '')),
                    user=user,
                    db=db_session,
                )

            elif (
                content_type
                and content_type.startswith(('image/', 'video/'))
                and not _media_supported_for_extraction(
                    content_extraction_engine, content_extraction_supported_media_mime_types, content_type
                )
            ):
                if content_type.startswith('video/'):
                    # Videos are stored as-is for downstream multimodal
                    # processing (Tools, vision models). Attempting text
                    # extraction causes "Timeout reached while detecting
                    # encoding" errors.
                    log.info('Video file detected (%s), skipping text extraction', content_type)
                    await Files.update_file_data_by_id(
                        file_item.id,
                        {'status': 'completed'},
                        db=db_session,
                    )
                else:
                    # Image evidence uses the uploaded image itself; no second OCR.
                    await process_file(request, ProcessFileForm(file_id=file_item.id), user=user, db=db_session)

            else:
                # Documents, or media files explicitly enabled for the
                # configured content extraction engine.
                if not content_type:
                    log.info('File type %s is not provided, but trying to process anyway', file.content_type)
                await process_file(
                    request,
                    ProcessFileForm(file_id=file_item.id),
                    user=user,
                    db=db_session,
                )

            # Auto-link to Knowledge Collection when uploaded from one (#24807).
            # Mirrors POST /knowledge/{id}/file/add so linking doesn't depend
            # on the frontend staying connected after upload.
            knowledge_id = file_metadata.get('knowledge_id')
            if knowledge_id:
                try:
                    # Gate like POST /knowledge/{id}/file/add: a client-supplied
                    # metadata.knowledge_id must not let a non-writer attach files (CWE-862/863).
                    knowledge = await Knowledges.get_knowledge_by_id(id=knowledge_id, db=db_session)
                    can_write = bool(knowledge) and (
                        knowledge.user_id == user.id
                        or user.role == 'admin'
                        or await AccessGrants.has_access(
                            user_id=user.id,
                            resource_type='knowledge',
                            resource_id=knowledge.id,
                            permission='write',
                            db=db_session,
                        )
                    )
                    if not can_write:
                        log.warning(
                            f'Refusing to auto-link file {file_item.id} to knowledge '
                            f'{knowledge_id}: user {user.id} lacks write access'
                        )
                    else:
                        directory_id = file_metadata.get('directory_id') or None
                        if directory_id:
                            directory = await Knowledges.get_directory_by_id(directory_id, db=db_session)
                            if not directory or directory.knowledge_id != knowledge_id:
                                log.warning(
                                    'Ignoring directory %s: not a directory of knowledge %s', directory_id, knowledge_id
                                )
                                directory_id = None

                        # Keep the generic file status stream open until the
                        # KB-specific vector write and durable link both finish.
                        await Files.update_file_data_by_id(file_item.id, {'status': 'processing'}, db=db_session)
                        await process_file(
                            request,
                            ProcessFileForm(file_id=file_item.id, collection_name=knowledge_id),
                            user=user,
                            db=db_session,
                        )
                        knowledge_file = await Knowledges.add_file_to_knowledge_by_id(
                            knowledge_id=knowledge_id,
                            file_id=file_item.id,
                            user_id=user.id,
                            directory_id=directory_id,
                            db=db_session,
                        )
                        if not knowledge_file:
                            raise Exception(f'Failed to link file {file_item.id} to knowledge {knowledge_id}')
                        log.info('Linked file %s to knowledge %s', file_item.id, knowledge_id)
                except Exception as e:
                    log.warning(f'Failed to link file {file_item.id} to knowledge {knowledge_id}: {e}')
                    raise

        except Exception as e:
            log.error(f'Error processing file: {file_item.id}')
            await Files.update_file_data_by_id(
                file_item.id,
                {
                    'status': 'failed',
                    'error': str(e.detail) if hasattr(e, 'detail') else str(e),
                },
                db=db_session,
            )

    try:
        if db:
            await _process_handler(db)
        else:
            async with get_async_db_context() as db_session:
                await _process_handler(db_session)
    finally:
        _cleanup_local_cache(file_path)


@router.post('/', response_model=FileModelResponse)
async def upload_file(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    metadata: Optional[dict | str] = Form(None),
    process: bool = Query(True),
    process_in_background: bool = Query(True),
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    result = await upload_file_handler(
        request,
        file=file,
        metadata=metadata,
        process=process,
        process_in_background=process_in_background,
        user=user,
        background_tasks=background_tasks,
        db=db,
    )

    if isinstance(result, dict):
        result_id = result.get('id')
        result_filename = result.get('filename')
        result_meta = result.get('meta') or {}
    else:
        result_id = result.id
        result_filename = result.filename
        result_meta = result.meta or {}

    result_content_type = (
        result_meta.get('content_type') if isinstance(result_meta, dict) else getattr(result_meta, 'content_type', None)
    )
    await publish_event(
        request,
        EVENTS.FILE_UPLOADED,
        actor=user,
        subject_id=result_id,
        data={'filename': result_filename, 'content_type': result_content_type},
    )
    return result


async def validate_knowledge_upload_target(metadata, user, db=None):
    """Reject stale targets before storing bytes, then recheck in the worker."""
    knowledge_id = metadata.get('knowledge_id')
    if not knowledge_id:
        return
    knowledge = await Knowledges.get_knowledge_by_id(knowledge_id, db=db)
    if not knowledge:
        raise HTTPException(404, '目标知识库已删除或不存在，请重新选择上传位置。')
    if not (
        user.role == 'admin'
        or knowledge.user_id == user.id
        or await AccessGrants.has_access(
            user_id=user.id, resource_type='knowledge', resource_id=knowledge_id, permission='write', db=db
        )
    ):
        raise HTTPException(403, '没有此知识库的写入权限，请重新选择上传位置。')
    directory_id = metadata.get('directory_id')
    if directory_id:
        directory = await Knowledges.get_directory_by_id(directory_id, db=db)
        if not directory or directory.knowledge_id != knowledge_id:
            raise HTTPException(404, '目标文件夹已删除或不属于此知识库，请重新选择上传位置。')


async def upload_file_handler(
    request: Request,
    file: UploadFile = File(...),
    metadata: Optional[dict | str] = Form(None),
    process: bool = Query(True),
    process_in_background: bool = Query(True),
    user=Depends(get_verified_user),
    background_tasks: Optional[BackgroundTasks] = None,
    db: Optional[AsyncSession] = None,
):
    log.info('file.content_type: %s %s', file.content_type, process)

    if isinstance(metadata, str):
        try:
            metadata = JSONCodec.loads(metadata)
        except JSONCodec.JSONDecodeError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT('Invalid metadata format'),
            )
    file_metadata = dict(metadata) if isinstance(metadata, dict) else {}
    # Only the backend may decide whether a file was stored without parsing.
    file_metadata.pop('native_model_file', None)

    try:
        await validate_knowledge_upload_target(file_metadata, user, db=db)
        unsanitized_filename = file.filename
        filename = os.path.basename(unsanitized_filename)

        file_extension = os.path.splitext(filename)[1]
        # Remove the leading dot from the file extension and lowercase it
        file_extension = file_extension[1:].lower() if file_extension else ''

        allowed_file_extensions = await Config.get('rag.file.allowed_extensions')
        if process and allowed_file_extensions:
            allowed_file_extensions = [ext for ext in allowed_file_extensions if ext]

            if file_extension not in allowed_file_extensions:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT(f'File type {file_extension} is not allowed'),
                )

        # Admission precedes durable storage, DB insertion and extraction. This
        # applies to all chat models, even when processing was disabled by the
        # caller; knowledge/channel uploads are intentionally exempt.
        await asyncio.to_thread(validate_chat_pdf_upload, file_metadata, filename, file.content_type, file.file)

        # Prefer readable storage names for admins, but fall back if the filesystem rejects it.
        id = str(uuid.uuid4())
        name = filename
        filename = f'{id}_{filename}'
        tags = {
            'OpenWebUI-User-Email': user.email,
            'OpenWebUI-User-Id': user.id,
            'OpenWebUI-User-Name': user.name,
            'OpenWebUI-File-Id': id,
        }
        from open_webui.retrieval.admission import stream_hash

        file_hash, upload_size = await asyncio.to_thread(stream_hash, file.file)
        from open_webui.config import UPLOAD_DIR
        import shutil

        minimum_free = int(os.getenv('INDEXING_UPLOAD_STORAGE_MIN_FREE_BYTES', str(2 * 1024**3)))
        if shutil.disk_usage(UPLOAD_DIR).free - upload_size < minimum_free:
            raise HTTPException(507, 'Insufficient upload storage headroom; no file was stored')
        max_size = await Config.get('rag.file.max_size')
        if max_size and upload_size > int(max_size) * 1024 * 1024:
            raise HTTPException(status_code=413, detail=ERROR_MESSAGES.FILE_TOO_LARGE(size=f'{max_size} MB'))
        file.file.seek(0)
        header = file.file.read(8)
        file.file.seek(0)
        try:
            _, file_path = await asyncio.to_thread(
                Storage.upload_file, file.file, filename, tags, return_contents=False
            )
        except OSError as e:
            if e.errno != errno.ENAMETOOLONG:
                log.exception(e)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT(e.strerror or 'Error uploading file'),
                )

            file.file.seek(0)
            filename = f'{id}.{file_extension}' if file_extension else id
            try:
                _, file_path = await asyncio.to_thread(
                    Storage.upload_file, file.file, filename, tags, return_contents=False
                )
            except OSError as e:
                log.exception(e)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT(e.strerror or 'Error uploading file'),
                )
        native_pdf = should_defer_pdf_processing(
            file_metadata, name, file.content_type, header, request.app.state.MODELS, size=upload_size
        )
        if native_pdf:
            process = False
            file_metadata['native_model_file'] = True

        # The server's streamed hash, never a client claim, is used for dedup.

        file_item = await Files.insert_new_file(
            user.id,
            FileForm(
                **{
                    'id': id,
                    'filename': name,
                    'path': file_path,
                    'data': {
                        **({'status': 'pending'} if process else {'status': 'completed'} if native_pdf else {}),
                    },
                    'meta': {
                        'name': name,
                        'content_type': (file.content_type if isinstance(file.content_type, str) else None),
                        'size': upload_size,
                        'file_hash': file_hash,
                        'server_hash_verified': True,
                        'data': file_metadata,
                    },
                }
            ),
            db=db,
        )

        if 'channel_id' in file_metadata:
            channel = await Channels.get_channel_by_id_and_user_id(file_metadata['channel_id'], user.id, db=db)
            if channel:
                await Channels.add_file_to_channel_by_id(channel.id, file_item.id, user.id, db=db)

        if process:
            # Persist document/image work before returning an upload response.
            # STT retains its existing transcription path, but its subsequent
            # document indexing also crosses process_file's durable queue seam.
            stt_types = await Config.get('audio.stt.supported_content_types', [])
            is_stt = bool(file.content_type and strict_match_mime_type(stt_types, file.content_type))
            is_video = bool(file.content_type and file.content_type.startswith('video/'))
            if not is_stt and not is_video:
                from open_webui.retrieval.ingestion import submit_file

                knowledge_id = file_metadata.get('knowledge_id')
                try:
                    async with get_async_db_context(db) as session:
                        queued = await submit_file(
                            request,
                            ProcessFileForm(file_id=file_item.id, collection_name=knowledge_id),
                            user=user,
                            db=session,
                            wait=not process_in_background,
                            auto_link={'directory_id': file_metadata.get('directory_id')} if knowledge_id else None,
                            received=True,
                        )
                except Exception as exc:
                    from open_webui.retrieval.ingestion_queue import DuplicateIngestion

                    if isinstance(exc, DuplicateIngestion):
                        await Files.update_file_data_by_id(
                            file_item.id,
                            {'status': 'failed', 'error': str(exc), 'duplicate_ingestion_job_id': exc.job_id},
                        )
                        raise HTTPException(409, str(exc)) from None
                    from open_webui.retrieval.admission import AdmissionError

                    reason = (
                        str(exc.detail)
                        if isinstance(exc, HTTPException)
                        else str(exc)
                        if isinstance(exc, AdmissionError)
                        else 'Indexing request not accepted; original retained'
                    )
                    await Files.update_file_data_by_id(
                        file_item.id,
                        {'status': 'failed', 'error': reason},
                    )
                    raise
                state = await Files.get_file_processing_state(file_item.id) or {}
                return {
                    'status': True,
                    **file_item.model_dump(),
                    **queued,
                    'data': {
                        **(file_item.data or {}),
                        **{key: state.get(key) for key in ('status', 'processing_status', 'error', 'ingestion_job_id')},
                    },
                }
            if background_tasks and process_in_background:
                background_tasks.add_task(
                    process_uploaded_file,
                    request,
                    file,
                    file_path,
                    file_item,
                    file_metadata,
                    user,
                )
                return {'status': True, **file_item.model_dump()}
            else:
                await process_uploaded_file(
                    request,
                    file,
                    file_path,
                    file_item,
                    file_metadata,
                    user,
                    db=db,
                )
                return {'status': True, **file_item.model_dump()}
        else:
            if file_item:
                return file_item
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT('Error uploading file'),
                )

    except HTTPException as e:
        raise e
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                ERROR_MESSAGES.EMPTY_CONTENT
                if isinstance(e, ValueError) and e.args == (ERROR_MESSAGES.EMPTY_CONTENT,)
                else ERROR_MESSAGES.DEFAULT('Error uploading file')
            ),
        )


############################
# List Files
############################


PAGE_SIZE = 50


@router.get('/', response_model=FileListResponse)
async def list_files(
    user=Depends(get_verified_user),
    page: int = Query(1, ge=1, description='Page number (1-indexed)'),
    content: bool = Query(True),
    db: AsyncSession = Depends(get_async_session),
):
    skip = (page - 1) * PAGE_SIZE
    user_id = None if (user.role == 'admin' and BYPASS_ADMIN_ACCESS_CONTROL) else user.id

    result = await Files.get_file_list(user_id=user_id, skip=skip, limit=PAGE_SIZE, db=db)

    if not content:
        for file in result.items:
            if file.data and 'content' in file.data:
                del file.data['content']

    return result


############################
# Search Files
############################


@router.get('/search', response_model=list[FileModelResponse])
async def search_files(
    filename: str = Query(
        ...,
        description="Filename pattern to search for. Supports wildcards such as '*.txt'",
    ),
    content: bool = Query(True),
    skip: int = Query(0, ge=0, description='Number of files to skip'),
    limit: int = Query(100, ge=1, le=1000, description='Maximum number of files to return'),
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    """
    Search for files by filename with support for wildcard patterns.
    Uses SQL-based filtering with pagination for better performance.
    """
    # Determine user_id: null for admin with bypass (search all), user.id otherwise
    user_id = None if (user.role == 'admin' and BYPASS_ADMIN_ACCESS_CONTROL) else user.id

    # Use optimized database query with pagination
    files = await Files.search_files(
        user_id=user_id,
        filename=filename,
        skip=skip,
        limit=limit,
        db=db,
    )

    if not files:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='No files found matching the pattern.',
        )

    if not content:
        for file in files:
            if file.data and 'content' in file.data:
                del file.data['content']

    return files


############################
# Count Files
############################


@router.get('/count', response_model=int)
async def count_files(
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    user_id = None if (user.role == 'admin' and BYPASS_ADMIN_ACCESS_CONTROL) else user.id
    return await Files.count_files_by_user_id(user_id=user_id, db=db)


############################
# Delete All Files
############################


@router.delete('/all')
async def delete_all_files(
    request: Request, user=Depends(get_admin_user), db: AsyncSession = Depends(get_async_session)
):
    from sqlalchemy import select
    from open_webui.models.files import File as FileRow
    from open_webui.retrieval.integration import deactivate

    identities = list(await db.scalars(select(FileRow.id)))
    await db.commit()
    for identity in identities:
        await deactivate(request, identity)
    result = await Files.delete_all_files(db=db)
    if result:
        try:
            await asyncio.to_thread(Storage.delete_all_files)
            await ASYNC_VECTOR_DB_CLIENT.reset()
        except Exception as e:
            log.exception(e)
            log.error('Error deleting files')
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT('Error deleting files'),
            )
        await publish_event(request, EVENTS.FILE_DELETED_ALL, actor=user, subject_type='file')
        return {'message': 'All files deleted successfully'}
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT('Error deleting files'),
        )


############################
# Get File By Id
############################


@router.get('/{id}', response_model=Optional[FileModel])
async def get_file_by_id(id: str, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    file = await Files.get_file_by_id(id, db=db)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if file.user_id == user.id or user.role == 'admin' or await has_access_to_file(id, 'read', user, db=db):
        return file
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


@router.get('/{id}/process/status')
async def get_file_process_status(id: str, stream: bool = Query(False), user=Depends(get_verified_user)):
    from open_webui.retrieval.ingestion_queue import expire_jobs

    async def current(file_id):
        state = await Files.get_file_processing_state(file_id)
        if state and state.get('ingestion_job_id'):
            if await expire_jobs(job_id=state['ingestion_job_id']):
                state = await Files.get_file_processing_state(file_id)
        return state

    file = await current(id)
    if not file or not (
        file['user_id'] == user.id or user.role == 'admin' or await has_access_to_file(id, 'read', user)
    ):
        raise HTTPException(404, ERROR_MESSAGES.NOT_FOUND)
    if not stream:
        return {'status': file.get('status') or 'pending', **({'error': file['error']} if file.get('error') else {})}

    async def event_stream():
        for _ in range(7200):
            state = await current(id)
            if state is None:
                yield f'data: {JSONCodec.dumps({"status": "not_found"})}\n\n'
                break
            status = state.get('status')
            if not status:
                break
            event = {'status': status}
            if status == 'failed':
                event['error'] = state.get('error')
            yield f'data: {JSONCodec.dumps(event)}\n\n'
            if status in ('completed', 'failed'):
                break
            await asyncio.sleep(1)

    return StreamingResponse(event_stream(), media_type='text/event-stream')


############################
# Get File Data Content By Id
############################


@router.get('/{id}/data/content')
async def get_file_data_content_by_id(
    id: str, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    file = await Files.get_file_by_id(id, db=db)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if file.user_id == user.id or user.role == 'admin' or await has_access_to_file(id, 'read', user, db=db):
        return {'content': file.data.get('content', '')}
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# Update File Data Content By Id
############################


class ContentForm(BaseModel):
    content: str


@router.post('/{id}/data/content/update')
async def update_file_data_content_by_id(
    request: Request,
    id: str,
    form_data: ContentForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    file = await Files.get_file_by_id(id, db=db)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if file.user_id == user.id or user.role == 'admin' or await has_access_to_file(id, 'write', user, db=db):
        max_size = await Config.get('rag.file.max_size')
        if max_size and len(form_data.content.encode('utf-8')) > int(max_size) * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=ERROR_MESSAGES.FILE_TOO_LARGE(size=f'{max_size} MB'),
            )
        try:
            await process_file(
                request,
                ProcessFileForm(file_id=id, content=form_data.content),
                user=user,
                db=db,
            )
            file = await Files.get_file_by_id(id=id, db=db)
        except Exception as e:
            log.exception(e)
            log.error(f'Error processing file: {id}')
            raise HTTPException(
                status_code=500, detail='Failed to process indexed text. Your changes were not fully indexed.'
            ) from e

        # The indexing worker publishes the file and every attached knowledge
        # scope atomically. Do not fetch/rewrite whole legacy vector collections
        # here or schedule a second index pass after publication.
        await publish_event(
            request,
            EVENTS.FILE_CONTENT_UPDATED,
            actor=user,
            subject_id=id,
            data={'content_preview': form_data.content[:300]},
        )
        return {'content': file.data.get('content', '')}
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# Get File Content By Id
############################


@router.get('/{id}/content')
async def get_file_content_by_id(
    id: str,
    user=Depends(get_verified_user),
    attachment: bool = Query(False),
    db: AsyncSession = Depends(get_async_session),
):
    file = await Files.get_file_by_id(id, db=db)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if file.user_id == user.id or user.role == 'admin' or await has_access_to_file(id, 'read', user, db=db):
        try:
            if not file.path:
                raise HTTPException(status_code=404, detail='Original file is unavailable.')
            file_path = await asyncio.to_thread(Storage.get_file, file.path)
            file_path = Path(file_path)

            # Check if the file already exists in the cache
            if file_path.is_file():
                # Handle Unicode filenames
                filename = file.meta.get('name', file.filename)
                encoded_filename = quote(filename)  # RFC5987 encoding

                content_type = file.meta.get('content_type')
                filename = file.meta.get('name', file.filename)
                encoded_filename = quote(filename)
                headers = {}

                if attachment:
                    headers['Content-Disposition'] = f"attachment; filename*=UTF-8''{encoded_filename}"
                else:
                    if content_type == 'application/pdf' or filename.lower().endswith('.pdf'):
                        headers['Content-Disposition'] = f"inline; filename*=UTF-8''{encoded_filename}"
                        content_type = 'application/pdf'
                    elif content_type != 'text/plain':
                        headers['Content-Disposition'] = f"attachment; filename*=UTF-8''{encoded_filename}"

                return FileResponse(file_path, headers=headers, media_type=content_type)

            else:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=ERROR_MESSAGES.NOT_FOUND,
                )
        except HTTPException as e:
            raise e
        except Exception as e:
            log.exception(e)
            log.error('Error getting file content')
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT('Error getting file content'),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


@router.get('/{id}/content/html')
async def get_html_file_content_by_id(
    id: str, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    file = await Files.get_file_by_id(id, db=db)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    file_user = await Users.get_user_by_id(file.user_id, db=db)
    if not file_user or file_user.role != 'admin':
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if file.user_id == user.id or user.role == 'admin' or await has_access_to_file(id, 'read', user, db=db):
        try:
            file_path = await asyncio.to_thread(Storage.get_file, file.path)
            file_path = Path(file_path)

            # Check if the file already exists in the cache
            if file_path.is_file():
                log.info('file_path: %s', file_path)
                return FileResponse(file_path)
            else:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=ERROR_MESSAGES.NOT_FOUND,
                )
        except HTTPException as e:
            raise e
        except Exception as e:
            log.exception(e)
            log.error('Error getting file content')
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT('Error getting file content'),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


@router.get('/{id}/content/{file_name}')
async def get_file_content_by_id(
    id: str, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    file = await Files.get_file_by_id(id, db=db)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if file.user_id == user.id or user.role == 'admin' or await has_access_to_file(id, 'read', user, db=db):
        file_path = file.path

        # Handle Unicode filenames
        filename = file.meta.get('name', file.filename)
        encoded_filename = quote(filename)  # RFC5987 encoding
        headers = {'Content-Disposition': f"attachment; filename*=UTF-8''{encoded_filename}"}

        if file_path:
            file_path = await asyncio.to_thread(Storage.get_file, file_path)
            file_path = Path(file_path)

            # Check if the file already exists in the cache
            if file_path.is_file():
                return FileResponse(file_path, headers=headers)

        # Legacy records can retain a path after their original upload has disappeared.
        # Preserve their indexed text as the download fallback.
        if not file_path or not file_path.is_file():
            # File path doesn’t exist, return the content as .txt if possible
            file_content = file.data.get('content', '')
            file_name = file.filename

            # Create a generator that encodes the file content
            def generator():
                yield file_content.encode('utf-8')

            return StreamingResponse(
                generator(),
                media_type='text/plain',
                headers=headers,
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# Rename File By Id
############################


class FileRenameForm(BaseModel):
    filename: str


@router.post('/{id}/rename')
async def rename_file_by_id(
    request: Request,
    id: str,
    form_data: FileRenameForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    file = await Files.get_file_by_id(id, db=db)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if file.user_id == user.id or user.role == 'admin' or await has_access_to_file(id, 'write', user, db=db):
        result = await Files.update_file_name_by_id(id, form_data.filename, db=db)
        if result:
            await publish_event(
                request,
                EVENTS.FILE_RENAMED,
                actor=user,
                subject_id=id,
                data={'filename': form_data.filename},
            )
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT('Error renaming file'),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# Delete File By Id
############################


@router.delete('/{id}')
async def delete_file_by_id(
    request: Request, id: str, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    file = await Files.get_file_by_id(id, db=db)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if file.user_id == user.id or user.role == 'admin' or await has_access_to_file(id, 'write', user, db=db):
        # Clean up KB associations and embeddings before deleting
        from open_webui.retrieval.integration import deactivate

        await db.commit()
        await deactivate(request, id)
        knowledges = await Knowledges.get_knowledges_by_file_id(id, db=db)
        for knowledge in knowledges:
            # Remove KB-file relationship
            await Knowledges.remove_file_from_knowledge_by_id(knowledge.id, id, db=db)
            # Clean KB embeddings (same logic as /knowledge/{id}/file/remove)
            try:
                await ASYNC_VECTOR_DB_CLIENT.delete(collection_name=knowledge.id, filter={'file_id': id})
                if file.hash:
                    await ASYNC_VECTOR_DB_CLIENT.delete(collection_name=knowledge.id, filter={'hash': file.hash})
            except Exception as e:
                log.debug('KB embedding cleanup for %s: %s', knowledge.id, e)

        result = await Files.delete_file_by_id(id, db=db)
        if result:
            try:
                await asyncio.to_thread(Storage.delete_file, file.path)
                await ASYNC_VECTOR_DB_CLIENT.delete(collection_name=f'file-{id}')
            except Exception as e:
                log.exception(e)
                log.error('Error deleting files')
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT('Error deleting files'),
                )
            await publish_event(
                request,
                EVENTS.FILE_DELETED,
                actor=user,
                subject_id=id,
                data={'filename': file.filename},
            )
            return {'message': 'File deleted successfully'}
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT('Error deleting file'),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
