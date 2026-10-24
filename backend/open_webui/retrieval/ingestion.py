"""Full file ingestion behind one queue seam. Heavy work runs only in a worker.

The old index stays live throughout admission, parsing and candidate writes.
No whole-file embedding call and no list of whole-file vectors is constructed.
"""

import asyncio
import hashlib
import os
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from open_webui.retrieval.admission import AdmissionError, AdmissionLimits, ChunkSpool


async def submit_file(request, form, *, user, db, wait=True, auto_link=None, retry_images=False):
    from fastapi import HTTPException
    from sqlalchemy import select
    from open_webui.config import Config, UPLOAD_DIR
    from open_webui.models.files import File
    from open_webui.models.retrieval_chunks import canonical_hash
    from open_webui.retrieval.ingestion_queue import enqueue, wait_for_job, DuplicateIngestion
    from open_webui.routers.retrieval import RETRIEVAL_CONFIG_KEYS, _validate_collection_access
    from open_webui.utils.access_control.files import has_access_to_file

    # Select descriptors only: old file.data may contain a huge legacy body.
    row = (await db.execute(select(File.id, File.user_id, File.meta).where(File.id == form.file_id))).one_or_none()
    if not row:
        raise HTTPException(404, 'File not found')
    if row.user_id != user.id and user.role != 'admin' and not await has_access_to_file(row.id, 'write', user, db=db):
        raise HTTPException(403, 'File access prohibited')
    if form.collection_name:
        await _validate_collection_access([form.collection_name], user, access_type='write')
    config = await Config.get_many(*RETRIEVAL_CONFIG_KEYS.values())
    source_hash = (row.meta or {}).get('file_hash') if (row.meta or {}).get('server_hash_verified') else None
    payload = {'auto_link': auto_link} if auto_link else {}
    if retry_images:
        payload['retry_images'] = True
    input_path = None
    if form.content is not None:
        content = form.content.replace('<br/>', '\n').encode()
        if len(content) > AdmissionLimits.from_env().max_text_bytes:
            raise AdmissionError('edited text exceeds document indexing budget; previous version retained')
        source_hash = hashlib.sha256(content).hexdigest()
        root = Path(UPLOAD_DIR) / '.indexing-inputs'
        root.mkdir(parents=True, exist_ok=True)
        input_path = root / (str(uuid.uuid4()) + '.txt')
        with input_path.open('xb') as stream:
            stream.write(content)
        payload['content_path'] = str(input_path)
    # Old client-supplied hashes are not trusted for cross-file deduplication.
    source_hash = source_hash or 'unverified-file:' + row.id
    await db.commit()  # Never hold a caller's DB connection while waiting.
    try:
        identity = await enqueue(
            file_id=row.id,
            user_id=user.id,
            knowledge_id=form.collection_name,
            source_hash=source_hash,
            config_hash=canonical_hash(config),
            payload=payload,
        )
    except DuplicateIngestion as exc:
        if input_path:
            input_path.unlink(missing_ok=True)
        if exc.file_id != row.id:
            raise
        identity = exc.job_id  # An exact same-file retry joins, not duplicates, the task.
    except BaseException:
        if input_path:
            input_path.unlink(missing_ok=True)
        raise
    if not wait:
        return {'status': True, 'ingestion_job_id': identity}
    result = await wait_for_job(identity)
    db.expire_all()  # Publication happened in another process/session.
    return {'status': True, **result}


async def submit_documents(request, docs, collection_name, config, *, user):
    """Existing text/web callers enter the same queue, with a small logical alias.

    Store only the admitted input descriptor privately; no body or vectors in
    the business database until the worker publishes a complete candidate.
    """
    import json
    from open_webui.config import UPLOAD_DIR
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.files import File
    from open_webui.models.knowledge import Knowledge
    from open_webui.models.ingestion_jobs import IngestionCollection
    from open_webui.models.retrieval_chunks import canonical_hash
    from open_webui.retrieval.ingestion_queue import enqueue, wait_for_job

    limits = AdmissionLimits.from_env()
    root = Path(UPLOAD_DIR) / '.indexing-inputs'
    root.mkdir(parents=True, exist_ok=True)
    path = root / (str(uuid.uuid4()) + '.jsonl')
    size, count, digest = 0, 0, hashlib.sha256()
    try:
        with path.open('xb') as stream:
            for doc in docs:
                count += 1
                size += len(doc.page_content.encode())
                if size > limits.max_text_bytes or count > limits.max_chunks:
                    raise AdmissionError('document text/count exceeds indexing budget')
                row = (
                    json.dumps({'text': doc.page_content, 'metadata': doc.metadata}, ensure_ascii=False).encode()
                    + b'\n'
                )
                if stream.tell() + len(row) > limits.max_spool_bytes:
                    raise AdmissionError('document input exceeds temporary storage budget')
                stream.write(row)
                digest.update(row)
        if not size:
            raise AdmissionError('empty document')
        async with get_async_db_context() as db:
            alias = await db.get(IngestionCollection, (user.id, collection_name))
            knowledge = await db.get(Knowledge, collection_name)
            file_id = alias.file_id if alias else str(uuid.uuid4())
            if not alias:
                now = int(time.time())
                db.add(
                    File(
                        id=file_id,
                        user_id=user.id,
                        filename=collection_name + '.txt',
                        data={},
                        meta={'content_type': 'text/plain'},
                        created_at=now,
                        updated_at=now,
                    )
                )
                await db.commit()
        payload = {'documents_path': str(path), 'logical_collection': collection_name}
        if knowledge:
            payload['auto_link'] = {'directory_id': None}
        identity = await enqueue(
            file_id=file_id,
            user_id=user.id,
            knowledge_id=collection_name if knowledge else None,
            source_hash=digest.hexdigest(),
            config_hash=canonical_hash(config),
            payload=payload,
        )
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return await wait_for_job(identity)


async def submit_url(request, url, collection_name, config, *, user):
    """Persist a URL descriptor; HTML/transcript extraction also runs off Web."""
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.files import File
    from open_webui.models.ingestion_jobs import IngestionCollection
    from open_webui.models.knowledge import Knowledge
    from open_webui.models.retrieval_chunks import canonical_hash
    from open_webui.retrieval.ingestion_queue import enqueue, wait_for_job
    from open_webui.retrieval.web.utils import validate_url

    await asyncio.to_thread(validate_url, url)
    async with get_async_db_context() as db:
        alias = await db.get(IngestionCollection, (user.id, collection_name))
        knowledge = await db.get(Knowledge, collection_name)
        file_id = alias.file_id if alias else str(uuid.uuid4())
        if not alias:
            now = int(time.time())
            db.add(
                File(
                    id=file_id,
                    user_id=user.id,
                    filename=collection_name + '.txt',
                    data={},
                    meta={'content_type': 'text/plain', 'source_url': url},
                    created_at=now,
                    updated_at=now,
                )
            )
            await db.commit()
    payload = {'url': url, 'logical_collection': collection_name}
    if knowledge:
        payload['auto_link'] = {'directory_id': None}
    identity = await enqueue(
        file_id=file_id,
        user_id=user.id,
        knowledge_id=knowledge.id if knowledge else None,
        source_hash=hashlib.sha256(url.encode()).hexdigest(),
        config_hash=canonical_hash(config),
        payload=payload,
    )
    return await wait_for_job(identity)


def _chunks(docs, splitter, *, limits, body_stream, markdown_splitter=None, merge_sections=None):
    """Split bounded windows, not one enormous RecursiveTextSplitter list.

    Windows prefer paragraph/line ends; chunk size/overlap and metadata stay
    those configured by the administrator. Each split call sees <=64Ki chars.
    """
    total = 0
    for doc in docs:
        text = doc.page_content
        total += len(text.encode())
        if total > limits.max_text_bytes:
            raise AdmissionError('parsed text exceeds indexing budget; original retained')
        body_stream.write(text.encode() + b'\n')
        start = 0
        while start < len(text):
            end = min(start + 65536, len(text))
            if end < len(text):
                split = text.rfind('\n', start + 32768, end)
                if split != -1:
                    end = split + 1
            from langchain_core.documents import Document

            window = text[start:end]
            sections = [Document(page_content=window, metadata=doc.metadata)]
            if markdown_splitter:
                sections = [
                    Document(page_content=section.page_content, metadata=doc.metadata)
                    for section in markdown_splitter.split_text(window)
                ]
                if merge_sections:
                    sections = merge_sections(sections)
            section_start = 0
            for section in sections:
                position = max(section_start, window.find(section.page_content, section_start))
                for chunk in splitter.split_documents([section]):
                    metadata = dict(chunk.metadata)
                    if type(metadata.get('page')) is int and 'page_index' not in metadata:
                        metadata['page_index'] = metadata['page']
                    if isinstance(metadata.get('start_index'), int):
                        metadata['start_index'] += start + position
                    yield {'text': chunk.page_content, 'metadata': metadata}
                section_start = position + len(section.page_content)
            # Keep the configured overlap across bounded parsing windows too.
            # Without this, a 64 KiB boundary silently lost adjacent context.
            overlap = getattr(splitter, '_chunk_overlap', 0)
            encoding = getattr(splitter, '_tokenizer', None)
            if overlap and encoding is not None and end < len(text):
                tail = text[max(start, end - 65536) : end]
                tokens = encoding.encode(tail, disallowed_special=())
                # A token boundary may fall inside a UTF-8 character. Extend
                # only a few tokens until the decoded suffix is source text.
                for width in range(overlap, overlap + 8):
                    decoded = encoding.decode(tokens[-width:])
                    if tail.endswith(decoded):
                        overlap = len(decoded)
                        break
            start = end if end == len(text) else max(start + 1, end - overlap)


async def _descriptors(request, job, file, config, *, version):
    from langchain_core.documents import Document
    from open_webui.config import UPLOAD_DIR
    from open_webui.retrieval.admission import inspect_source
    from open_webui.retrieval.integration import collect_assets, version_asset_root
    from open_webui.routers.retrieval import build_loader_from_config
    from open_webui.storage.provider import Storage

    limits = AdmissionLimits.from_env()
    config['rag.document_image_asset_root'] = job.payload['asset_root']
    content_path = (job.payload or {}).get('content_path')
    documents_path = (job.payload or {}).get('documents_path')
    if (job.payload or {}).get('url'):
        from open_webui.routers.retrieval import get_content_from_url

        _, docs = await get_content_from_url(request, job.payload['url'], config=config)
        assets, skipped = collect_assets(docs)
    elif documents_path:
        import json

        path = Path(documents_path).resolve(strict=True)
        root = (Path(UPLOAD_DIR) / '.indexing-inputs').resolve()
        if not path.is_relative_to(root) or path.stat().st_size > limits.max_spool_bytes:
            raise AdmissionError('invalid/oversized stored document input')

        def stored_documents():
            with path.open() as stream:
                for line in stream:
                    row = json.loads(line)
                    yield Document(page_content=row['text'], metadata=row['metadata'])

        docs, assets, skipped = stored_documents(), [], []
    elif content_path:
        path = Path(content_path).resolve(strict=True)
        root = (Path(UPLOAD_DIR) / '.indexing-inputs').resolve()
        if not path.is_relative_to(root) or path.is_symlink():
            raise ValueError('invalid stored indexing input')
        if path.stat().st_size > limits.max_text_bytes:
            raise AdmissionError('edited text exceeds indexing budget')
        with path.open('rb') as stream:
            inspect_source(stream, size=path.stat().st_size)
        docs = [Document(page_content=path.read_text(), metadata={})]
        assets, skipped = [], []
    elif file.path:
        path = Path(await asyncio.to_thread(Storage.get_file, file.path))
        with path.open('rb') as stream:
            inspect_source(stream, size=path.stat().st_size)
            from open_webui.retrieval.admission import stream_hash

            actual_hash, _ = stream_hash(stream)
            if not job.source_hash.startswith('unverified-file:') and actual_hash != job.source_hash:
                raise ValueError('stored original changed after queue admission')
        if (file.meta or {}).get('content_type', '').startswith('image/'):
            from open_webui.retrieval.document_image_assets import ImageAssetStore, MAX_IMAGE_BYTES

            with path.open('rb') as stream:
                image = stream.read(MAX_IMAGE_BYTES + 1)
            assets = [
                ImageAssetStore(config['rag.document_image_asset_root'], file.id).put(
                    image, backend='upload', origin_reference='uploaded-image'
                )
            ]
            docs, skipped = [], []
        else:
            loader = build_loader_from_config(config)
            loader.user = request.user
            loader.metadata = {
                'file_id': file.id,
                'file_name': file.filename,
                'file_content_type': (file.meta or {}).get('content_type'),
            }
            try:
                docs = await loader.aload(file.filename, (file.meta or {}).get('content_type'), str(path))
            except Exception as exc:
                if getattr(exc, 'status_code', None) == 503:
                    raise AdmissionError(
                        'No usable document parser for this format in the Slim deployment; original retained'
                    ) from exc
                raise
            assets, skipped = collect_assets(docs)
    else:
        # Legacy inline sources are read only inside the capped worker.
        text = (file.data or {}).get('content', '')
        docs, assets, skipped = [Document(page_content=text, metadata={})], [], []
    if len(assets) > limits.max_assets:
        raise AdmissionError('document image count exceeds indexing budget')
    asset_bytes = sum(Path(a.get('storage_uri') or a.get('path')).stat().st_size for a in assets)
    if asset_bytes > limits.max_asset_bytes:
        raise AdmissionError('document image bytes exceed indexing budget')
    if skipped:
        raise AdmissionError('required document images could not be extracted; previous version retained')
    return docs, assets


async def execute(job_id, token):
    """Child-process entry: authenticate the task again and publish only while leased."""
    from sqlalchemy import select, func
    from open_webui.config import Config
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.files import Files
    from open_webui.models.ingestion_jobs import IngestionJob as Job
    from open_webui.models.users import Users
    from open_webui.models.retrieval_indexes import RetrievalIndexState as State
    from open_webui.models.evidence import stable_id
    from open_webui.retrieval.integration import ASSET_CONFIG_KEYS, SERVICE_CONFIG_KEYS, services
    from open_webui.routers.retrieval import RETRIEVAL_CONFIG_KEYS, _validate_collection_access
    from open_webui.utils.access_control.files import has_access_to_file

    async with get_async_db_context() as db:
        job = await db.get(Job, job_id)
        if not job or job.status != 'running' or job.lease_token != token or job.lease_until < time.time():
            raise ValueError('indexing lease is not current')
    user = await Users.get_user_by_id(job.user_id)
    file = await Files.get_file_by_id(job.file_id)
    if (
        not user
        or user.role not in ('user', 'admin')
        or not file
        or (file.user_id != user.id and user.role != 'admin' and not await has_access_to_file(file.id, 'write', user))
    ):
        raise PermissionError('indexing authorization no longer valid')
    if job.knowledge_id:
        await _validate_collection_access([job.knowledge_id], user, access_type='write')
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                ef=None, EMBEDDING_FUNCTION=True, RERANKING_FUNCTION=None, main_loop=asyncio.get_running_loop()
            )
        ),
        user=user,
    )
    config = await Config.get_many(*RETRIEVAL_CONFIG_KEYS.values(), *ASSET_CONFIG_KEYS, *SERVICE_CONFIG_KEYS)
    from open_webui.models.retrieval_chunks import canonical_hash

    expected_config = {k: config[k] for k in RETRIEVAL_CONFIG_KEYS.values() if k in config}
    if canonical_hash(expected_config) != job.config_hash:
        raise ValueError('indexing configuration changed while queued; resubmit explicitly')
    indexer, _, space_id, status = await services(request)
    if (job.payload or {}).get('retry_images'):
        if not space_id:
            raise ValueError('image retry needs a verified current encoding space')
        await _retry_images(job, token, file, indexer, space_id)
        return
    indexer.staged = True
    indexer.staging_key = job.job_id
    indexer.lexical_enabled = False
    if job.knowledge_id and not any((job.payload or {}).get(k) for k in ('content_path', 'documents_path', 'url')):
        if await _reuse_for_knowledge(job, token, file, indexer, space_id):
            return
    if not space_id:
        raise ValueError('current embedding space is not verified/compatible; previous version retained')
    async with get_async_db_context() as db:
        active = await db.get(State, stable_id('file', file_id=file.id))
        highest = await db.scalar(select(func.max(State.file_version)).where(State.file_id == file.id))
        version = (
            max((file.data or {}).get('retrieval_version', 0), active.file_version if active else 0, highest or 0) + 1
        )
    from open_webui.retrieval.integration import version_asset_root

    job.payload = {
        **(job.payload or {}),
        'candidate_version': version,
        'asset_root': str(version_asset_root(config, file.id, version) / job.job_id),
    }
    async with get_async_db_context() as db:
        stored = (await db.execute(select(Job).where(Job.job_id == job_id).with_for_update())).scalar_one()
        if stored.status != 'running' or stored.lease_token != token or stored.lease_until < time.time():
            raise ValueError('indexing lease expired before parsing')
        stored.payload = job.payload
        await db.commit()
    docs, assets = await _descriptors(request, job, file, config, version=version)
    # Admission is complete BEFORE a candidate state or any business text/vector write.
    await _write_candidate(request, job, token, file, config, indexer, space_id, version, docs, assets)


async def _retry_images(job, token, file, indexer, space_id):
    from sqlalchemy import select
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.files import File
    from open_webui.models.ingestion_jobs import IngestionJob as Job
    from open_webui.models.knowledge import Knowledges
    from open_webui.retrieval.ingestion_queue import lease_is_current

    version = (file.data or {}).get('retrieval_version')
    assets = (file.data or {}).get('document_image_assets') or []
    limits = AdmissionLimits.from_env()
    scopes = [None, *(k.id for k in await Knowledges.get_knowledges_by_file_id(file.id))]
    if not version or not assets or len(assets) * len(scopes) > limits.max_assets:
        raise AdmissionError('no current extracted images within the retry budget; original retained')
    collection = (file.meta or {}).get('vector_collection') or f'file-{file.id}'
    for scope in scopes:
        if not await lease_is_current(job.job_id, token):
            raise ValueError('image retry lease ended')
        result = await indexer.index_assets(
            file_id=file.id,
            file_version=version,
            collection_name=collection,
            assets=assets,
            knowledge_id=scope,
            space_id=space_id,
            source_name=file.filename,
        )
        if result['status'] != 'ready':
            raise ValueError('image retry did not complete; text source version retained')
    now = int(time.time())
    async with get_async_db_context() as db:
        current_job = (await db.execute(select(Job).where(Job.job_id == job.job_id).with_for_update())).scalar_one()
        if current_job.status != 'running' or current_job.lease_token != token or current_job.lease_until < now:
            raise ValueError('image retry lease ended before completion')
        row = (await db.execute(select(File).where(File.id == file.id).with_for_update())).scalar_one()
        if (row.data or {}).get('retrieval_version') != version:
            raise ValueError('source changed before image retry completion')
        row.data = {
            **row.data,
            'status': 'completed',
            'error': None,
            'retrieval_status': {
                **(row.data.get('retrieval_status') or {}),
                'images': 'ready',
                'image_count': len(assets),
                'image_retryable': False,
            },
        }
        current_job.status, current_job.finished_at = 'succeeded', now
        current_job.active_key = current_job.active_file = current_job.worker_slot = current_job.lease_until = None
        current_job.result = {
            'file_id': file.id,
            'file_version': version,
            'images': len(assets),
            'text_reembedded': False,
        }
        await db.commit()


async def _write_candidate(request, job, token, file, config, indexer, space_id, version, docs, assets):
    import tempfile
    from langchain_text_splitters import RecursiveCharacterTextSplitter, TokenTextSplitter, MarkdownHeaderTextSplitter
    from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT
    from open_webui.retrieval.utils import get_embedding_function
    from open_webui.config import RAG_EMBEDDING_CONTENT_PREFIX
    from open_webui.retrieval.integration import services
    from open_webui.models.knowledge import Knowledges
    from open_webui.utils.misc import sanitize_text_for_db

    if config.get('rag.embedding_engine') != 'openai':
        raise ValueError('isolated indexing requires the configured external OpenAI-compatible encoder')
    limits = AdmissionLimits.from_env()
    size, overlap = int(config['rag.chunk_size']), int(config['rag.chunk_overlap'])
    if size < 1 or overlap < 0 or overlap >= size or size > 16384:
        raise AdmissionError('invalid/unbounded chunk configuration')
    kind = config.get('rag.text_splitter') or 'character'
    kwargs = dict(chunk_size=size, chunk_overlap=overlap, add_start_index=True)
    if kind == 'character':
        splitter = RecursiveCharacterTextSplitter(**kwargs)
    elif kind == 'token':
        splitter = TokenTextSplitter(encoding_name=config['rag.tiktoken_encoding_name'], **kwargs)
    else:
        raise ValueError('isolated indexing supports character and tiktoken chunking; configured splitter unsupported')
    # Per-file sources are mapped into every attached KB in the same publication.
    knowledges = await Knowledges.get_knowledges_by_file_id(file.id)
    scopes = list(dict.fromkeys([None, *(k.id for k in knowledges), *([job.knowledge_id] if job.knowledge_id else [])]))
    collection = 'ingest-' + job.job_id
    embedding = get_embedding_function(
        'openai',
        config['rag.embedding_model'],
        None,
        config['rag.openai.api_base_url'],
        config['rag.openai.api_key'],
        32,
        enable_async=True,
        concurrent_requests=1,
    )
    root = Path(os.getenv('INDEXING_TEMP_DIR', '/tmp/indexing'))
    root.mkdir(parents=True, exist_ok=True)
    markdown_splitter = merge_sections = None
    if config.get('rag.enable_markdown_header_text_splitter'):
        markdown_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=[('#' * level, f'Header {level}') for level in range(1, 7)], strip_headers=False
        )
        if config.get('rag.chunk_min_size_target', 0) > 0:
            from open_webui.routers.retrieval import merge_docs_to_target_size

            merge_sections = lambda sections: merge_docs_to_target_size(request, sections, config)
    with tempfile.TemporaryDirectory(prefix='job-' + job.job_id + '-', dir=root) as temp:
        temp = Path(temp)
        with ChunkSpool(temp, limits) as spool:
            body_path = temp / 'body.txt'
            with body_path.open('wb') as body:
                spool.write(
                    _chunks(
                        docs,
                        splitter,
                        limits=limits,
                        body_stream=body,
                        markdown_splitter=markdown_splitter,
                        merge_sections=merge_sections,
                    ),
                    allow_empty=bool(assets),
                )
            if spool.count * len(scopes) > limits.max_chunks:
                raise AdmissionError('document plus knowledge-scope mappings exceed indexing budget')
            del docs
            await indexer.accept_file_version(file.id, version)
            offset = 0
            batch_size = min(32, max(1, int(config.get('rag.embedding_batch_size') or 32)))
            for batch in spool.batches(batch_size):
                texts = [sanitize_text_for_db(item['text']) for item in batch]
                vectors = await asyncio.wait_for(
                    embedding(
                        [t.replace('\n', ' ') for t in texts], prefix=RAG_EMBEDDING_CONTENT_PREFIX, user=request.user
                    ),
                    timeout=180,
                )
                if len(vectors) != len(batch):
                    raise ValueError('embedding batch length mismatch')
                from open_webui.retrieval.ingestion_queue import lease_is_current

                if not await lease_is_current(job.job_id, token):
                    raise ValueError('indexing lease ended before candidate write')
                from open_webui.retrieval.vector.embedding_adapter import validate_vector

                vectors = [validate_vector(vector, 2048) for vector in vectors]
                items = [
                    dict(
                        id=str(uuid.uuid4()),
                        text=text,
                        vector=vector,
                        source_name=(
                            item['metadata'].get('source')
                            if any((job.payload or {}).get(k) for k in ('documents_path', 'url'))
                            and str(item['metadata'].get('source', '')).startswith(('http://', 'https://'))
                            else file.filename
                        ),
                        metadata={
                            **{
                                k: v
                                for k, v in item['metadata'].items()
                                if k not in ('evidence_ref', 'chunk_uid', 'vector_id')
                            },
                            'file_id': file.id,
                            'file_version': version,
                            'name': file.filename,
                            'source': file.filename,
                        },
                    )
                    for item, text, vector in zip(batch, texts, vectors)
                ]
                await ASYNC_VECTOR_DB_CLIENT.insert(collection_name=collection, items=items)
                # Mapping receives no vector arrays and is committed one small batch at a time.
                mappings = [{k: v for k, v in item.items() if k != 'vector'} for item in items]
                for scope in scopes:
                    await indexer.index_file(
                        file_id=file.id,
                        file_version=version,
                        collection_name=collection,
                        chunks=mappings,
                        knowledge_id=scope,
                        space_id=space_id,
                        chunker_config={
                            k: config.get(k) for k in ('rag.chunk_size', 'rag.chunk_overlap', 'rag.text_splitter')
                        },
                        source_name=file.filename,
                        chunk_offset=offset,
                    )
                offset += len(batch)
                del vectors, items, mappings, texts, batch
                # Bound logical write rate; this does not claim to throttle PG's physical device.
                await asyncio.sleep(0.1)
            for scope in scopes:
                if assets:
                    result = await indexer.index_assets(
                        file_id=file.id,
                        file_version=version,
                        collection_name=collection,
                        assets=assets,
                        knowledge_id=scope,
                        space_id=space_id,
                        source_name=file.filename,
                    )
                    if result['status'] != 'ready':
                        raise ValueError('required image indexing did not complete')
            _, _, current_space, _ = await services(request)
            if current_space != space_id:
                raise ValueError('embedding configuration changed during indexing')
            from open_webui.config import Config
            from open_webui.routers.retrieval import RETRIEVAL_CONFIG_KEYS
            from open_webui.models.retrieval_chunks import canonical_hash

            current_config = await Config.get_many(*RETRIEVAL_CONFIG_KEYS.values())
            if canonical_hash(current_config) != job.config_hash:
                raise ValueError('indexing configuration changed before publication')
            await publish_candidate(
                job.job_id,
                token,
                file_id=file.id,
                version=version,
                collection=collection,
                content=body_path.read_text(),
                assets=assets,
                chunk_count=offset,
                space_id=space_id,
                scopes=scopes,
            )


async def _reuse_for_knowledge(job, token, file, indexer, space_id):
    """Attach bounded pages of existing vector identities; zero re-embedding."""
    from sqlalchemy import select, func
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.retrieval_chunks import (
        RetrievalChunk as Chunk,
        compute_chunk_uid,
        compute_chunker_config_hash,
    )
    from open_webui.models.retrieval_indexes import RetrievalIndexState as State
    from open_webui.models.evidence import (
        stable_id,
        KnowledgeEvidence as Evidence,
        KnowledgeEvidenceEmbedding as Mapping,
        KnowledgeVectorSpace as Space,
    )

    async with get_async_db_context() as db:
        active = await db.get(State, stable_id('file', file_id=file.id))
        if not active or active.status != 'ready':
            return False
        version = active.file_version
        present = await db.scalar(
            select(func.count())
            .select_from(Chunk)
            .where(
                Chunk.file_id == file.id,
                Chunk.file_version == version,
                Chunk.is_active.is_(True),
                Chunk.knowledge_id.is_(None),
            )
        )
        if not present:
            return False
        if present > AdmissionLimits.from_env().max_chunks:
            raise AdmissionError('existing source exceeds association budget; current index retained')
        existing = await db.scalar(
            select(func.count())
            .select_from(Chunk)
            .where(
                Chunk.file_id == file.id,
                Chunk.file_version == version,
                Chunk.is_active.is_(True),
                Chunk.knowledge_id == job.knowledge_id,
            )
        )
        if existing and existing != present:
            raise AdmissionError('existing association is incomplete; retained for explicit repair')
    count, cursor = 0, 0
    if not existing:
        await indexer.accept_file_version(file.id, version)
        while True:
            async with get_async_db_context() as db:
                rows = list(
                    await db.scalars(
                        select(Chunk)
                        .where(
                            Chunk.file_id == file.id,
                            Chunk.file_version == version,
                            Chunk.is_active.is_(True),
                            Chunk.knowledge_id.is_(None),
                            Chunk.row_id > cursor,
                        )
                        .order_by(Chunk.row_id)
                        .limit(32)
                    )
                )
            if not rows:
                break
            groups = {}
            for row in rows:
                groups.setdefault(row.collection_name, []).append(row)
            for collection, group in groups.items():
                items = []
                for row in group:
                    vector_id = (row.metadata_ or {}).get('vector_id')
                    if not vector_id:
                        raise ValueError('existing chunk is missing its preserved vector identity')
                    items.append(
                        {
                            'id': vector_id,
                            'text': row.text,
                            'metadata': {
                                **{
                                    k: v
                                    for k, v in (row.metadata_ or {}).items()
                                    if k not in ('chunk_uid', 'evidence_ref')
                                },
                                'file_id': file.id,
                            },
                        }
                    )
                await indexer.index_file(
                    file_id=file.id,
                    file_version=version,
                    collection_name=collection,
                    chunks=items,
                    knowledge_id=job.knowledge_id,
                    space_id=None,
                    source_name=file.filename,
                    chunk_offset=count,
                )
                # Copy original encoders and backend identities, never relabel
                # old vectors as produced by today's configured encoder.
                async with get_async_db_context() as db:
                    for offset, row in enumerate(group, start=count):
                        uid = compute_chunk_uid(
                            collection_id=job.knowledge_id,
                            file_id=file.id,
                            file_version=version,
                            chunker_config_hash=compute_chunker_config_hash(None),
                            chunk_index=offset,
                            content_hash=row.content_hash,
                        )
                        target = await db.scalar(
                            select(Evidence).where(
                                Evidence.retrieval_chunk_uid == uid, Evidence.knowledge_id == job.knowledge_id
                            )
                        )
                        originals = list(
                            (
                                await db.execute(
                                    select(Mapping, Evidence.source_name)
                                    .join(Evidence, Evidence.evidence_ref == Mapping.evidence_ref)
                                    .where(
                                        Evidence.retrieval_chunk_uid == row.chunk_uid,
                                        Evidence.is_active.is_(True),
                                        Evidence.knowledge_id.is_(None),
                                    )
                                )
                            ).all()
                        )
                        if not originals:
                            raise ValueError('existing vector provenance mapping is missing; source not re-encoded')
                        for original, original_name in originals:
                            target.source_name = original_name
                            space = await db.get(Space, original.vector_space_id)
                            await indexer._map(
                                db,
                                target,
                                space,
                                original.vector_backend_collection,
                                original.vector_backend_id,
                                original.embedding_status,
                                original.embedding_error,
                            )
                    await db.commit()
                count += len(group)
            cursor = rows[-1].row_id
        assets = (file.data or {}).get('document_image_assets') or []
        if assets:
            result = await indexer.index_assets(
                file_id=file.id,
                file_version=version,
                collection_name=(file.meta or {}).get('vector_collection')
                or (file.meta or {}).get('collection_name')
                or f'file-{file.id}',
                assets=assets,
                knowledge_id=job.knowledge_id,
                space_id=space_id,
                source_name=file.filename,
            )
            if result['status'] != 'ready':
                raise ValueError('required image association did not complete')
    await publish_association(
        job.job_id, token, file_id=file.id, version=version, knowledge_id=job.knowledge_id, expected_text_count=present
    )
    return True


async def _link_upload(db, job):
    link = (job.payload or {}).get('auto_link')
    if not link or not job.knowledge_id:
        return
    from sqlalchemy import select
    from open_webui.models.knowledge import KnowledgeFile, KnowledgeDirectory

    directory = link.get('directory_id')
    if directory:
        row = await db.get(KnowledgeDirectory, directory)
        if not row or row.knowledge_id != job.knowledge_id:
            raise ValueError('upload directory is not in the target knowledge base')
    existing = await db.scalar(
        select(KnowledgeFile.id)
        .where(KnowledgeFile.knowledge_id == job.knowledge_id, KnowledgeFile.file_id == job.file_id)
        .limit(1)
    )
    if not existing:
        now = int(time.time())
        db.add(
            KnowledgeFile(
                id=str(uuid.uuid4()),
                knowledge_id=job.knowledge_id,
                file_id=job.file_id,
                user_id=job.user_id,
                directory_id=directory,
                created_at=now,
                updated_at=now,
            )
        )


async def publish_association(job_id, token, *, file_id, version, knowledge_id, expected_text_count):
    from sqlalchemy import select, update, func
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.files import File
    from open_webui.models.ingestion_jobs import IngestionJob as Job
    from open_webui.models.evidence import KnowledgeEvidence as Evidence, stable_id
    from open_webui.models.retrieval_chunks import RetrievalChunk as Chunk
    from open_webui.models.retrieval_indexes import RetrievalIndexState as State
    from open_webui.retrieval.ingestion_queue import patch_file_status

    now = int(time.time())
    async with get_async_db_context() as db:
        job = (await db.execute(select(Job).where(Job.job_id == job_id).with_for_update())).scalar_one()
        if job.status != 'running' or job.lease_token != token or (job.lease_until or 0) < now:
            raise ValueError('indexing lease expired/cancelled')
        await db.scalar(select(File.id).where(File.id == file_id).with_for_update())
        state = await db.get(State, stable_id('file', file_id=file_id))
        if not state or state.status != 'ready' or state.file_version != version:
            raise ValueError('source changed before knowledge association')
        actual = await db.scalar(
            select(func.count())
            .select_from(Chunk)
            .where(
                Chunk.file_id == file_id,
                Chunk.file_version == version,
                Chunk.knowledge_id == knowledge_id,
                Chunk.deleted_at.is_(None),
            )
        )
        if actual != expected_text_count:
            raise ValueError('knowledge association mapping count is incomplete')
        for model in (Evidence, Chunk):
            await db.execute(
                update(model)
                .where(
                    model.file_id == file_id,
                    model.file_version == version,
                    model.knowledge_id == knowledge_id,
                    model.deleted_at.is_(None),
                )
                .values(is_active=True, updated_at=now)
            )
        await _link_upload(db, job)
        await patch_file_status(db, file_id, {'status': 'completed', 'error': None}, job_id=job_id)
        job.status, job.finished_at = 'succeeded', now
        job.active_key = job.active_file = job.worker_slot = job.lease_until = None
        job.result = {
            'file_id': file_id,
            'collection_name': knowledge_id,
            'file_version': version,
            'reused_vectors': True,
        }
        await db.commit()


async def publish_candidate(
    job_id, token, *, file_id, version, collection, content, assets, chunk_count, space_id, scopes
):
    """One short atomic SQL publication, fenced against expired/cancelled jobs."""
    from sqlalchemy import select, update, func
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.ingestion_jobs import IngestionJob as Job
    from open_webui.models.files import File
    from open_webui.models.evidence import (
        KnowledgeEvidence as Evidence,
        KnowledgeEvidenceAsset as Asset,
        KnowledgeEvidenceEmbedding as Mapping,
        KnowledgeVectorSpace as Space,
        stable_id,
    )
    from open_webui.models.retrieval_chunks import RetrievalChunk as Chunk
    from open_webui.models.retrieval_indexes import RetrievalIndexState as State

    now = int(time.time())
    async with get_async_db_context() as db:
        job = (await db.execute(select(Job).where(Job.job_id == job_id).with_for_update())).scalar_one()
        if job.status != 'running' or job.lease_token != token or (job.lease_until or 0) < now:
            raise ValueError('indexing lease expired/cancelled; candidate not published')
        file = (await db.execute(select(File).where(File.id == file_id).with_for_update())).scalar_one()
        previous = (file.data or {}).get('retrieval_version', 0)
        if previous >= version or ((file.data or {}).get('retrieval_status') or {}).get('text') == 'deleted':
            raise ValueError('source changed/deleted before publication')
        space = await db.get(Space, space_id)
        if not space or not space.active or space.verification_status != 'verified':
            raise ValueError('candidate encoder is not verified at publication')
        actual_chunks = await db.scalar(
            select(func.count())
            .select_from(Chunk)
            .where(Chunk.file_id == file_id, Chunk.file_version == version, Chunk.collection_name == collection)
        )
        actual_mappings = await db.scalar(
            select(func.count())
            .select_from(Mapping)
            .join(Evidence, Evidence.evidence_ref == Mapping.evidence_ref)
            .where(
                Evidence.file_id == file_id,
                Evidence.file_version == version,
                Mapping.vector_space_id == space_id,
                Mapping.embedding_status == 'ready',
            )
        )
        if actual_chunks != chunk_count * len(scopes) or actual_mappings != (chunk_count + len(assets)) * len(scopes):
            raise ValueError('candidate text/image mapping count is incomplete; previous version retained')
        for model in (Evidence, Chunk):
            await db.execute(update(model).where(model.file_id == file_id).values(is_active=False, updated_at=now))
            await db.execute(
                update(model)
                .where(
                    model.file_id == file_id,
                    model.file_version == version,
                    model.knowledge_id.in_([s for s in scopes if s]) | model.knowledge_id.is_(None),
                )
                .values(is_active=True, updated_at=now)
            )
        await db.execute(
            update(Asset)
            .where(Asset.file_id == file_id, Asset.file_version != version)
            .values(status='stale', updated_at=now)
        )
        state = await db.get(State, stable_id('file', file_id=file_id))
        if state is None:
            state = State(
                state_id=stable_id('file', file_id=file_id),
                index_kind='map_text',
                file_id=file_id,
                file_version=version,
                status='ready',
                created_at=now,
                updated_at=now,
            )
            db.add(state)
        state.file_version, state.status, state.updated_at = version, 'ready', now
        state.active_chunk_count = state.indexed_chunk_count = chunk_count
        state.collection_name = collection
        file.data = {
            **(file.data or {}),
            'content': content,
            'status': 'completed',
            'error': None,
            'retrieval_version': version,
            'document_image_assets': assets,
            'document_image_assets_skipped': [],
            'retrieval_status': {'version': version, 'text': 'ready', 'images': 'ready' if assets else 'none'},
        }
        logical_collection = (job.payload or {}).get('logical_collection') or f'file-{file_id}'
        file.meta = {**(file.meta or {}), 'collection_name': logical_collection, 'vector_collection': collection}
        file.hash = hashlib.sha256(content.encode()).hexdigest()
        file.updated_at = now
        logical = (job.payload or {}).get('logical_collection')
        if logical:
            from open_webui.models.ingestion_jobs import IngestionCollection

            alias = await db.get(IngestionCollection, (job.user_id, logical))
            if alias:
                alias.file_id = file_id
            else:
                db.add(IngestionCollection(user_id=job.user_id, collection_name=logical, file_id=file_id))
        job.status, job.finished_at = 'succeeded', now
        job.active_key = job.active_file = job.worker_slot = job.lease_until = None
        job.result = {
            'collection_name': logical_collection,
            'filename': file.filename,
            'file_id': file_id,
            'file_version': version,
            'text_chunks': chunk_count,
            'images': len(assets),
        }
        import resource
        import sys

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        job.result['worker_peak_rss_bytes'] = int(peak * (1 if sys.platform == 'darwin' else 1024))
        await _link_upload(db, job)
        await db.commit()
