"""Application wiring for the unified index/search core, not another RAG engine."""

import asyncio
import base64
import hashlib
import inspect
import io
import logging
import weakref
from contextlib import asynccontextmanager
from functools import wraps
from pathlib import Path

from open_webui.config import RAG_EMBEDDING_CONTENT_PREFIX, RAG_EMBEDDING_QUERY_PREFIX, UPLOAD_DIR, Config
from open_webui.internal.db import get_async_db
from open_webui.models.evidence import KnowledgeVectorSpace as Space
from open_webui.models.evidence import stable_id
from open_webui.models.files import File
from open_webui.models.retrieval_indexes import RetrievalIndexState as State
from open_webui.retrieval.evidence import EvidenceService, resolve_scope
from open_webui.retrieval.indexing import IndexingService
from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT
from open_webui.retrieval.vector.embedding_adapter import (
    MAX_IMAGE_BYTES,
    EvidenceEmbeddingAdapter,
    LegacyReadEmbeddingAdapter,
    QueryEmbeddingRouter,
)
from PIL import Image
from sqlalchemy import select

log = logging.getLogger(__name__)
_file_locks = weakref.WeakValueDictionary()


@asynccontextmanager
async def file_operation(file_id):
    # One indexing owner serializes official vector collection writes as well
    # as SQL versions. Weak values do not retain every historical file ID.
    lock = _file_locks.setdefault(file_id, asyncio.Lock())
    async with lock:
        yield


def serialize_file_processing(function):
    signature = inspect.signature(function, eval_str=True)

    @wraps(function)
    async def wrapped(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        async with file_operation(bound.arguments['form_data'].file_id):
            return await function(*args, **kwargs)

    wrapped.__signature__ = signature
    return wrapped


ASSET_CONFIG_KEYS = (
    'rag.extract_document_image_assets',
    'rag.document_image_asset_root',
    'rag.pdf_image_asset_source',
    'rag.document_image_asset_allowed_origins',
    'rag.paddleocr_vl_api_mode',
    'rag.paddleocr_vl_model',
    'rag.paddleocr_vl_optional_payload',
    'rag.paddleocr_vl_request_timeout',
    'rag.paddleocr_vl_download_timeout',
    'rag.paddleocr_vl_poll_timeout',
    'rag.paddleocr_vl_poll_interval',
    'rag.paddleocr_vl_allowed_remote_origins',
)
SERVICE_CONFIG_KEYS = (
    'rag.evidence_vector_space_id',
    'rag.embedding_batch_size',
    'rag.enable_async_embedding',
    'rag.embedding_concurrent_requests',
    'rag.embedding_engine',
    'rag.embedding_model',
    'rag.openai.api_base_url',
    'rag.openai.api_key',
    'rag.enable_hybrid_search',
    'rag.opensearch_url',
    'rag.opensearch_index',
    'rag.opensearch_username',
    'rag.opensearch_password',
    'rag.opensearch_verify_ssl',
    'rag.document_image_asset_root',
)


def asset_root(config):
    return Path(config.get('rag.document_image_asset_root') or Path(UPLOAD_DIR) / 'document-image-assets').resolve()


def version_asset_root(config, file_id, version):
    # One immutable extraction directory per accepted version. Parsers hash the
    # file identity again; neither user filenames nor model arguments become paths.
    return asset_root(config) / hashlib.sha256(file_id.encode()).hexdigest()[:32] / str(version)


async def controlled_asset_reader(asset, *, root):
    def read():
        path = Path(asset.storage_uri)
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root) or path.is_symlink() or not resolved.is_file():
            raise PermissionError('image asset is outside the private asset directory')
        if resolved.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError('image asset exceeds size limit')
        with resolved.open('rb') as handle:
            data = handle.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES or hashlib.sha256(data).hexdigest() != asset.sha256:
            raise ValueError('image asset changed or exceeds size limit')
        return data

    return await asyncio.to_thread(read)


async def _legacy_query_adapters(request, config):
    versioned_suffix = '/qwen3vl-retrieval-v1/v1'
    configured_url = (config.get('rag.openai.api_base_url') or '').rstrip('/')
    if not (
        config.get('rag.embedding_engine') == 'openai'
        and configured_url.endswith(versioned_suffix)
        and request is not None
        and getattr(request.app.state, 'EMBEDDING_FUNCTION', None)
    ):
        return {}
    from open_webui.retrieval.utils import get_embedding_function

    raw_url = configured_url[:-len(versioned_suffix)] + '/v1'
    async with get_async_db() as session:
        spaces = (
            await session.scalars(select(Space).where(
                Space.active.is_(True), Space.verification_status == 'legacy_read_only',
                Space.embedding_model == config.get('rag.embedding_model'), Space.embedding_dim == 2048,
            ))
        ).all()
    adapters = {}
    for space in spaces:
        adapter = LegacyReadEmbeddingAdapter(
            text_embedding_function=get_embedding_function(
                'openai', space.embedding_model, getattr(request.app.state, 'ef', None),
                raw_url, config.get('rag.openai.api_key') or '',
                config.get('rag.embedding_batch_size') or 1,
                enable_async=config.get('rag.enable_async_embedding', True),
                concurrent_requests=config.get('rag.embedding_concurrent_requests') or 0,
            ),
            model=space.embedding_model, url=raw_url,
            key=config.get('rag.openai.api_key') or '', config_fingerprint=space.config_fingerprint,
        )
        try:
            adapter.validate_space(space)
        except ValueError:
            continue
        adapters[space.id] = adapter
    return adapters


async def services(request, *, db=None, user=None):
    """Re-read settings per operation; an admin model change cannot reuse a stale encoder."""
    config = await Config.get_many(*SERVICE_CONFIG_KEYS)
    space_id = config.get('rag.evidence_vector_space_id') or None
    async with get_async_db() as session:
        space = await session.get(Space, space_id) if space_id else None
    embedding = None
    state = 'unassigned'
    if space:
        state = space.verification_status
        encoding = space.encoding_config or {}
        compatible = (
            space.verification_status == 'verified'
            and space.active
            and space.embedding_dim == 2048
            and config.get('rag.embedding_engine') == 'openai'
            and space.embedding_model == config.get('rag.embedding_model')
            and encoding.get('text_api_base_url') == config.get('rag.openai.api_base_url')
            and encoding.get('query_prefix') == RAG_EMBEDDING_QUERY_PREFIX
            and encoding.get('document_prefix') == RAG_EMBEDDING_CONTENT_PREFIX
        )
        if compatible:
            from open_webui.config import PGVECTOR_INITIALIZE_MAX_VECTOR_LENGTH

            if PGVECTOR_INITIALIZE_MAX_VECTOR_LENGTH != 2048:
                compatible = False
        if compatible and request is not None and getattr(request.app.state, 'EMBEDDING_FUNCTION', None):
            from open_webui.retrieval.utils import get_embedding_function

            # Build the official helper from the same current settings as the
            # space check, rather than trusting a stale app-state closure.
            text_function = get_embedding_function(
                'openai',
                space.embedding_model,
                getattr(request.app.state, 'ef', None),
                config['rag.openai.api_base_url'],
                config.get('rag.openai.api_key') or '',
                config.get('rag.embedding_batch_size') or 1,
                enable_async=config.get('rag.enable_async_embedding', True),
                concurrent_requests=config.get('rag.embedding_concurrent_requests') or 0,
            )
            embedding = EvidenceEmbeddingAdapter(
                text_embedding_function=text_function,
                model=space.embedding_model,
                url=config['rag.openai.api_base_url'],
                key=config.get('rag.openai.api_key') or '',
                config_fingerprint=space.config_fingerprint,
            )
        else:
            state = 'incompatible' if space.verification_status == 'verified' else space.verification_status
    # No mapping is labelled ready if the current text encoder does not match.
    active_space_id = space_id if embedding is not None else None
    root = asset_root(config)

    async def reader(asset):
        return await controlled_asset_reader(asset, root=root)

    def lexical_factory():
        from open_webui.retrieval.lexical.opensearch import OpenSearchLexicalAdapter

        return OpenSearchLexicalAdapter(
            url=config.get('rag.opensearch_url') or '',
            index=config.get('rag.opensearch_index') or 'openwebui-evidence-v1',
            username=config.get('rag.opensearch_username'),
            password=config.get('rag.opensearch_password'),
            verify_ssl=config.get('rag.opensearch_verify_ssl', True),
        )

    kwargs = dict(
        db=db,
        vector_client=ASYNC_VECTOR_DB_CLIENT,
        embedding=embedding,
        asset_reader=reader,
        lexical_enabled=bool(config.get('rag.enable_hybrid_search')),
        lexical_factory=lexical_factory,
    )
    indexer = IndexingService(**kwargs)

    # Read compatibility is per space. New uploads retain ONLY the selected
    # verified adapter above; legacy profiles cannot become an indexing target.
    adapters = {space_id: embedding} if embedding is not None else {}
    adapters.update(await _legacy_query_adapters(request, config))

    async def rerank(query, items):
        from langchain_core.documents import Document

        function = getattr(request.app.state, 'RERANKING_FUNCTION', None) if request is not None else None
        if not function:
            return items
        scores = await asyncio.to_thread(
            function, query, [Document(page_content=i['content']) for i in items], user=user
        )
        return [i for i, _ in sorted(zip(items, scores), key=lambda pair: pair[1], reverse=True)]

    evidence = EvidenceService(
        **{**kwargs, 'embedding': QueryEmbeddingRouter(adapters) if adapters else None}, reranker=rerank
    )
    return (
        indexer,
        evidence,
        active_space_id,
        {'space_id': space_id, 'status': state, 'encoder_ready': embedding is not None},
    )


async def accept_version(request, file_id, *, new_content, db):
    # Lock the official file row as well as the core state: this serializes the
    # first-ever state creation on PostgreSQL and prevents version reuse.
    row = (
        await db.execute(
            select(File).where(File.id == file_id).with_for_update().execution_options(populate_existing=True)
        )
    ).scalar_one()
    if ((row.data or {}).get('retrieval_status') or {}).get('text') == 'deleted':
        raise ValueError('file deletion has already been accepted')
    current = await db.get(State, stable_id('file', file_id=file_id))
    version = max((row.data or {}).get('retrieval_version', 0), current.file_version if current else 0)
    version = version + 1 if new_content or version == 0 else version
    row.data = {
        **(row.data or {}),
        **({'content': '', 'document_image_assets': [], 'document_image_assets_skipped': []} if new_content else {}),
        'retrieval_version': version,
        'retrieval_status': {'version': version, 'text': 'processing', 'images': 'pending'},
    }
    # Shares the transaction: source data and accepted core version commit together.
    await IndexingService(db=db).accept_file_version(file_id, version)
    return version


def collect_assets(docs):
    assets, skipped = [], []
    for doc in docs:
        assets.extend(doc.metadata.pop('document_image_assets', []) or [])
        skipped.extend(doc.metadata.pop('document_image_assets_skipped', []) or [])
    return assets, skipped


async def authorized_query_images(user, entries):
    """Resolve at most one stored upload ID; never a message image URL/base64."""
    from open_webui.models.files import Files

    for entry in entries or ():
        if not isinstance(entry, dict) or not isinstance(entry.get('id'), str):
            continue
        if entry.get('type') != 'image' and not str(entry.get('content_type') or '').startswith('image/'):
            continue
        identity = entry['id']
        if identity not in (await resolve_scope(user, file_ids=(identity,))).file_ids:
            continue
        file = await Files.get_file_by_id(identity)
        if file and (file.meta or {}).get('content_type', '').startswith('image/'):
            return [identity]
    return []


async def update_status(file_id, version, **status):
    async with get_async_db() as db:
        row = (await db.execute(select(File).where(File.id == file_id).with_for_update())).scalar_one_or_none()
        if row and (row.data or {}).get('retrieval_version') == version:
            old = (row.data or {}).get('retrieval_status') or {}
            row.data = {**(row.data or {}), 'retrieval_status': {**old, **status, 'version': version}}
            await db.commit()


def spawn(request, coroutine):
    tasks = getattr(request.app.state, 'rag_index_tasks', None)
    if tasks is None:
        tasks = request.app.state.rag_index_tasks = set()
    task = asyncio.create_task(coroutine)
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return task


async def complete_images(request, *, file_id, version, collection, assets, skipped, knowledge_id, source_name, job_id):
    try:
        indexer, _, space_id, _ = await services(request)
        locks = getattr(request.app.state, 'rag_image_locks', None)
        if locks is None:
            locks = request.app.state.rag_image_locks = {}
        lock = locks.setdefault((file_id, version), asyncio.Lock())
        async with lock:
            result = await indexer.run_job(job_id)
        status = result['status']
        if skipped:
            status = 'partial' if result.get('indexed') else 'failed'
        await update_status(
            file_id,
            version,
            images=status,
            image_count=result.get('indexed', 0),
            skipped=len(skipped),
            image_retryable=bool(result.get('failed')),
        )
    except Exception as exc:
        log.warning('Image indexing failed for %s: %s', file_id, type(exc).__name__)
        await update_status(file_id, version, images='failed', image_error=type(exc).__name__, image_retryable=True)


async def request_scope(user, *, model_knowledge=(), metadata=None, knowledge_ids=None, query_image_refs=()):
    """Merge server attachments; explicit IDs can narrow, never expand a bound scope."""
    metadata = metadata or {}
    entries = [*model_knowledge, *(metadata.get('folder_knowledge') or []), *(metadata.get('files') or [])]
    kb_ids, file_ids = [], []
    for item in entries:
        if not isinstance(item, dict):
            continue
        identity = item.get('id')
        if not identity:
            continue
        if identity in query_image_refs and item.get('type', 'file') == 'file':
            # A query image is independently authorized by the core; it is an
            # input, not an attached target scope that blocks KB discovery.
            continue
        if item.get('type') == 'collection':
            kb_ids.append(identity)
        elif item.get('type', 'file') == 'file':
            from open_webui.utils.native_pdf import is_native_pdf_attachment

            if not is_native_pdf_attachment(item, metadata):
                file_ids.append(identity)
    if knowledge_ids is not None:
        if not isinstance(knowledge_ids, list) or any(not isinstance(k, str) for k in knowledge_ids):
            raise ValueError('knowledge_ids must be a list of stored knowledge IDs')
        kb_ids = [k for k in kb_ids if k in knowledge_ids] if (kb_ids or file_ids) else knowledge_ids
        file_ids = []  # An explicit KB restriction must not leak other attachments.
    return await resolve_scope(user, knowledge_ids=kb_ids, file_ids=file_ids)


async def split_external_scope(scope):
    """External knowledge connectors remain official; never send their IDs to pgvector."""
    from open_webui.models.knowledge import Knowledges
    from open_webui.retrieval.evidence import ResolvedScope

    local, external = [], []
    for identity in scope.knowledge_ids:
        knowledge = await Knowledges.get_knowledge_by_id(identity)
        if knowledge and (knowledge.meta or {}).get('source') == 'external':
            external.append({'type': 'collection', 'id': identity, 'name': knowledge.name})
        else:
            local.append(identity)
    return ResolvedScope(tuple(local), scope.file_ids), external


def collection_search_result(items):
    """Keep pgvector's normalized cosine scores, never substitute RRF values.

    RRF determines order across spaces. A score belongs only to the explicitly
    named space; if one evidence has several mappings choose a deterministic
    space rather than compare cosines across incompatible encoders. Lexical-only
    hits have no cosine score and do not enter this legacy vector response.
    """
    from open_webui.retrieval.vector.main import SearchResult

    ids, distances, documents, metadatas = [], [], [], []
    for item in items:
        scores = item.get('vector_scores') or {}
        if not scores:
            continue
        space_id = sorted(scores)[0]
        similarity = scores[space_id]
        ids.append(item['evidence_ref'])
        distances.append(similarity)
        documents.append(item['content'])
        metadatas.append({
            **item['source'], 'evidence_ref': item['evidence_ref'], 'modality': item['modality'],
            'citation_key': item['citation_key'], 'score_type': 'normalized_cosine_similarity',
            'vector_space_id': space_id, 'rrf_score': item['score'], 'rank_type': 'rrf',
        })
    return SearchResult(ids=[ids], distances=[distances], documents=[documents], metadatas=[metadatas])


async def query_local_collection(request, *, collection_name, query, count, user):
    """The public query/doc and Native knowledge tools share local-space routing."""
    from open_webui.models.knowledge import Knowledges

    knowledge = await Knowledges.get_knowledge_by_id(collection_name)
    if not knowledge or (knowledge.meta or {}).get('source') == 'external':
        return None  # Preserve the official nonlocal/external route.
    scope = await resolve_scope(user, knowledge_ids=(collection_name,))
    _, evidence, _, _ = await services(request, user=user)
    result = await evidence.search(
        query_text=query, modalities=('text',), count=min(max(count, 1), 50),
        user=user, resolved_scope=scope, include_vector_scores=True,
    )
    return collection_search_result(result['items'])


def citation_sources(items):
    groups = {}
    for item in items:
        if item.get('status') != 'ready':
            continue
        source = item['source']
        identity = (source['file_id'], source['file_version'])
        group = groups.setdefault(
            identity,
            {
                'source': {'id': source['file_id'], 'name': source['name'], 'type': 'file'},
                'document': [],
                'metadata': [],
            },
        )
        group['document'].append(item.get('content') or item.get('model_note') or 'Image evidence')
        group['metadata'].append(
            {
                **source,
                'source': source['name'],
                'page': source.get('page_index'),
                'evidence_ref': item['evidence_ref'],
                'citation_key': item.get('citation_key'),
                'modality': item['modality'],
                'score_type': item.get('score_type'),
            }
        )
    return list(groups.values())


async def model_images(request, items, *, user, scope, vision):
    """Actual authorized hit images, limited and re-encoded; never public storage URLs."""
    if not vision:
        return []
    _, evidence, _, _ = await services(request)
    images = []
    for item in items:
        if item.get('modality') != 'image' or item.get('status') != 'ready':
            continue
        if len(images) == 3:
            break
        data, _ = await evidence.image_content(item['evidence_ref'], user=user, resolved_scope=scope)

        def resize():
            with Image.open(io.BytesIO(data)) as image:
                image = image.convert('RGB')
                image.thumbnail((1280, 1280))
                out = io.BytesIO()
                image.save(out, 'JPEG', quality=85)
                return out.getvalue()

        content = await asyncio.to_thread(resize)
        current = await evidence.read([item['evidence_ref']], user=user, resolved_scope=scope)
        if not any(i.get('status') == 'ready' for i in current['items']):
            continue
        images.append(
            {'type': 'input_image', 'image_url': 'data:image/jpeg;base64,' + base64.b64encode(content).decode()}
        )
    return images


async def deactivate(request, file_id, *, knowledge_id=None):
    async with file_operation(file_id):
        return await _deactivate(request, file_id, knowledge_id=knowledge_id)


async def _deactivate(request, file_id, *, knowledge_id=None):  # noqa: C901
    from open_webui.models.evidence import KnowledgeEvidenceAsset

    paths = []
    if knowledge_id is None:
        config = await Config.get_many('rag.document_image_asset_root')
        own_root = asset_root(config) / hashlib.sha256(file_id.encode()).hexdigest()[:32]
        async with get_async_db() as db:
            file = (await db.execute(select(File).where(File.id == file_id).with_for_update())).scalar_one_or_none()
            if file:
                for descriptor in (file.data or {}).get('document_image_assets') or []:
                    uri = descriptor.get('storage_uri') if isinstance(descriptor, dict) else None
                    # Pending jobs may not have an Asset row yet. Their parser
                    # files must belong to this file's deterministic directory.
                    if isinstance(uri, str) and Path(uri).resolve().is_relative_to(own_root):
                        paths.append(uri)
                file.data = {
                    **(file.data or {}),
                    'retrieval_status': {
                        **((file.data or {}).get('retrieval_status') or {}),
                        'text': 'deleted',
                        'images': 'deleted',
                    },
                }
                await db.commit()
            paths.extend(
                list(
                    await db.scalars(
                        select(KnowledgeEvidenceAsset.storage_uri).where(KnowledgeEvidenceAsset.file_id == file_id)
                    )
                )
            )
    indexer, _, _, _ = await services(request)
    await indexer.deactivate_file(file_id, knowledge_id=knowledge_id)
    if knowledge_id is None:
        config = await Config.get_many('rag.document_image_asset_root')
        root = asset_root(config)
        for uri in set(paths):
            async with get_async_db() as db:
                shared = await db.scalar(
                    select(KnowledgeEvidenceAsset.id)
                    .where(
                        KnowledgeEvidenceAsset.storage_uri == uri,
                        KnowledgeEvidenceAsset.file_id != file_id,
                        KnowledgeEvidenceAsset.status != 'deleted',
                    )
                    .limit(1)
                )
            if shared:
                continue

            def cleanup():
                path = Path(uri)
                if not path.exists():
                    return
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise PermissionError('asset cleanup target is outside the private asset directory')
                path.unlink()

            await asyncio.to_thread(cleanup)


async def start_recovery(app):
    """Only the sole local application owner may recover interrupted new jobs."""
    import fcntl

    from open_webui.env import UVICORN_WORKERS

    config = await Config.get_many('rag.document_image_asset_root')
    root = asset_root(config)
    root.mkdir(parents=True, exist_ok=True)
    handle = (root / '.index-owner.lock').open('a')
    try:
        if UVICORN_WORKERS != 1:
            raise BlockingIOError('recovery needs a single application worker')
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        log.warning('RAG startup recovery skipped: this process is not the sole index owner')
        return
    app.state.rag_index_owner = handle
    from types import SimpleNamespace

    request = SimpleNamespace(app=app)
    indexer, _, _, _ = await services(request)
    jobs = await indexer.recover_jobs(limit=100)

    async def recover():
        for job_id in jobs:
            try:
                result = await indexer.run_job(job_id)
                from open_webui.models.retrieval_indexes import RetrievalIndexJob

                async with get_async_db() as db:
                    job = await db.get(RetrievalIndexJob, job_id)
                    if job and job.index_kind == 'image':
                        await update_status(
                            job.file_id,
                            job.file_version,
                            images=result.get('status', 'failed'),
                            image_count=result.get('indexed', 0),
                        )
            except Exception as exc:
                log.warning('RAG interrupted job %s not resumed: %s', job_id, type(exc).__name__)

    spawn(request, recover())


async def stop_recovery(app):
    tasks = list(getattr(app.state, 'rag_index_tasks', ()))
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    handle = getattr(app.state, 'rag_index_owner', None)
    if handle:
        handle.close()
