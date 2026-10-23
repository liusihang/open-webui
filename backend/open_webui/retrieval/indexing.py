"""Versioned, resumable evidence indexing. Mapping existing text costs zero embeddings."""

import hashlib
import time

from open_webui.models.evidence import (
    KnowledgeEvidence as Evidence,
)
from open_webui.models.evidence import (
    KnowledgeEvidenceAsset as Asset,
)
from open_webui.models.evidence import (
    KnowledgeEvidenceEmbedding as Embedding,
)
from open_webui.models.evidence import (
    KnowledgeVectorSpace as Space,
)
from open_webui.models.evidence import (
    stable_id,
)
from open_webui.models.retrieval_chunks import (
    RetrievalChunk as Chunk,
)
from open_webui.models.retrieval_chunks import (
    canonical_hash,
    compute_chunk_uid,
    compute_chunker_config_hash,
    compute_content_hash,
    core_db,
)
from open_webui.models.retrieval_indexes import EXECUTOR_VERSION
from open_webui.models.retrieval_indexes import RetrievalIndexJob as Job
from open_webui.models.retrieval_indexes import RetrievalIndexState as State
from sqlalchemy import select, update


class IndexingService:
    def __init__(
        self,
        *,
        db=None,
        vector_client=None,
        embedding=None,
        asset_reader=None,
        lexical_enabled=False,
        lexical_factory=None,
        staged=False,
        staging_key=None,
    ):
        self.db = db
        self.vector_client = vector_client
        self.embedding = embedding
        self.asset_reader = asset_reader
        self.lexical_enabled = lexical_enabled
        self.lexical_factory = lexical_factory
        self.staged = staged
        self.staging_key = staging_key

    async def register_space(
        self,
        *,
        embedding_model,
        embedding_dim=None,
        model_revision=None,
        encoding_config=None,
        verified=False,
        supports_images=False,
        space_id=None,
    ):
        config = dict(encoding_config or {})
        if verified and (
            not model_revision
            or embedding_dim != 2048
            or config.get('distance_metric') != 'cosine'
            or not all(k in config for k in ('query_prefix', 'document_prefix', 'normalization'))
            or (supports_images and 'image_preprocessing' not in config)
        ):
            raise ValueError('verified space needs model provenance, actual 2048 dimensions and encoding configuration')
        fingerprint = canonical_hash(
            dict(
                model=embedding_model,
                revision=model_revision,
                dimensions=embedding_dim,
                encoding=config,
                supports_images=supports_images,
            )
        )
        identity = space_id or 'space_' + fingerprint
        now = int(time.time())
        async with core_db(self.db) as db:
            existing = await db.get(Space, identity)
            if existing:
                if existing.config_fingerprint != fingerprint:
                    raise ValueError('existing space identity cannot be assigned another encoding configuration')
                # Verification is explicit; looking up an old space never silently promotes it.
                if verified:
                    existing.verification_status = 'verified'
                await db.commit()
                return existing
            row = Space(
                id=identity,
                embedding_model=embedding_model,
                model_revision=model_revision,
                embedding_dim=embedding_dim,
                distance_metric=config.get('distance_metric', 'cosine'),
                config_fingerprint=fingerprint,
                encoding_config=config,
                verification_status='verified' if verified else 'quarantined',
                supports_text_query=True,
                supports_text_evidence=True,
                supports_image_query=supports_images,
                supports_image_evidence=supports_images,
                active=True,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row

    async def _latest(self, db, file_id, *, lock=False):
        identity = (
            stable_id('candidate', file_id=file_id, owner=self.staging_key)
            if self.staged
            else stable_id('file', file_id=file_id)
        )
        stmt = select(State).where(State.state_id == identity)
        if lock:
            stmt = stmt.with_for_update()
        return (await db.execute(stmt)).scalar_one_or_none()

    async def _accept_version(self, db, file_id, version, now):
        row = await self._latest(db, file_id, lock=True)
        if row and (row.file_version > version or (row.file_version == version and row.status == 'deleted')):
            raise ValueError('stale or deleted file version cannot be republished')
        if row is None:
            row = State(
                state_id=(
                    stable_id('candidate', file_id=file_id, owner=self.staging_key)
                    if self.staged
                    else stable_id('file', file_id=file_id)
                ),
                index_kind='map_text',
                file_id=file_id,
                file_version=version,
                status='indexing',
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            await db.flush()
        if version > row.file_version:
            if not self.staged:
                await db.execute(
                    update(Evidence).where(Evidence.file_id == file_id).values(is_active=False, updated_at=now)
                )
                await db.execute(update(Chunk).where(Chunk.file_id == file_id).values(is_active=False, updated_at=now))
                await db.execute(update(Asset).where(Asset.file_id == file_id).values(status='stale', updated_at=now))
            row.file_version = version
            row.status = 'indexing'
        row.updated_at = now
        return row

    async def accept_file_version(self, file_id, file_version):
        """Upload/update hook: invalidate old sources *before* slow parsing/embedding.

        The caller owns official file write authorization. Publish text with index_file after
        its existing vectors have been stored; supplement images independently afterward.
        """
        if not file_id or isinstance(file_version, bool) or not isinstance(file_version, int) or file_version < 1:
            raise ValueError('file_id and a positive integer file_version are required')
        async with core_db(self.db) as db:
            current = await self._accept_version(db, file_id, file_version, int(time.time()))
            await db.commit()
            return {'file_id': file_id, 'file_version': current.file_version, 'status': current.status}

    async def _job(self, db, *, kind, file_id, version, knowledge_id, collection, target, payload):
        now = int(time.time())
        identity = stable_id(
            'job',
            kind=kind,
            file_id=file_id,
            version=version,
            knowledge_id=knowledge_id,
            collection=collection,
            target=target,
        )
        row = await db.get(Job, identity)
        if row is None:
            row = Job(
                job_id=identity,
                index_kind=kind,
                file_id=file_id,
                file_version=version,
                knowledge_id=knowledge_id,
                collection_name=collection,
                target_config_hash=target,
                executor_version=EXECUTOR_VERSION,
                status='pending',
                payload=payload,
                retry_count=0,
                max_retries=3,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            await db.flush()
        return row

    async def index_file(  # noqa: C901
        self,
        *,
        file_id,
        file_version,
        collection_name,
        chunks,
        assets=(),
        knowledge_id=None,
        space_id=None,
        chunker_config=None,
        source_name=None,
        chunk_offset=0,
    ):
        """Import *already written* text vector items; never encodes or changes text/vector IDs.

        chunks: {id|vector_id, text, metadata, chunk_uid?, evidence_ref?}. assets are normalized
        extraction descriptors. Call with a monotonically increasing file_version for changed content.
        """
        if (
            not file_id
            or not collection_name
            or isinstance(file_version, bool)
            or not isinstance(file_version, int)
            or file_version < 1
        ):
            raise ValueError('file_id, collection_name and positive file_version are required')
        if collection_name == 'knowledge-layers':
            raise ValueError('summary vectors are excluded from evidence indexing')
        chunks = [dict(c) for c in chunks]
        normalized = []
        config_hash = compute_chunker_config_hash(chunker_config)
        for index, item in enumerate(chunks, start=chunk_offset):
            vector_id = item.get('vector_id') or item.get('id')
            if not isinstance(vector_id, str) or not vector_id:
                raise ValueError('existing text vector identity is required')
            text = item.get('text')
            content_hash = compute_content_hash(text)
            metadata = dict(item.get('metadata') or {})
            if metadata.get('file_id') not in (None, file_id):
                raise ValueError('vector metadata file_id conflicts with its source file')
            uid = (
                item.get('chunk_uid')
                or metadata.get('chunk_uid')
                or compute_chunk_uid(
                    collection_id=knowledge_id,
                    collection_name=collection_name,
                    knowledge_id=knowledge_id,
                    file_id=file_id,
                    file_version=file_version,
                    chunker_config_hash=config_hash,
                    chunk_index=index,
                    content_hash=content_hash,
                )
            )
            ref = (
                item.get('evidence_ref')
                or metadata.get('evidence_ref')
                or stable_id('ke', knowledge_id=knowledge_id, file_id=file_id, version=file_version, chunk_uid=uid)
            )
            normalized.append((item, metadata, uid, ref, vector_id, content_hash, index))
        now = int(time.time())
        async with core_db(self.db) as db:
            current = await self._accept_version(db, file_id, file_version, now)
            space = await db.get(Space, space_id) if space_id else None
            compatible = bool(
                space
                and space.verification_status == 'verified'
                and space.active
                and space.supports_text_evidence
                and space.embedding_dim == 2048
                and space.distance_metric == 'cosine'
            )
            target = canonical_hash(
                dict(space=space_id, chunker=config_hash, chunks=[(v[2], v[4], v[5]) for v in normalized])
            )
            job = await self._job(
                db,
                kind='map_text',
                file_id=file_id,
                version=file_version,
                knowledge_id=knowledge_id,
                collection=collection_name,
                target=target,
                payload={'space_id': space_id, 'manifest_hash': canonical_hash([(v[2], v[5]) for v in normalized])},
            )
            prior = (
                []
                if self.staged
                else (
                    await db.scalars(
                        select(Job).where(
                            Job.file_id == file_id,
                            Job.file_version == file_version,
                            Job.knowledge_id == knowledge_id,
                            Job.collection_name == collection_name,
                            Job.index_kind == 'map_text',
                            Job.executor_version == EXECUTOR_VERSION,
                            Job.job_id != job.job_id,
                        )
                    )
                ).all()
            )
            manifest_hash = canonical_hash([(v[2], v[5]) for v in normalized])
            if not self.staged and any(
                (row.payload or {}).get('manifest_hash') not in (None, manifest_hash) for row in prior
            ):
                await db.rollback()
                raise ValueError('changed source chunks require a new file version')
            job.status = 'running'
            job.started_at = now
            for item, metadata, uid, ref, vector_id, content_hash, index in normalized:
                old = (await db.execute(select(Chunk).where(Chunk.chunk_uid == uid))).scalar_one_or_none()
                if old and (
                    old.content_hash != content_hash or old.file_id != file_id or old.file_version != file_version
                ):
                    await db.rollback()
                    raise ValueError('chunk UID collision across content/file versions')
                metadata['vector_id'] = vector_id
                if old is None:
                    old = Chunk(
                        chunk_uid=uid,
                        collection_id=knowledge_id,
                        knowledge_id=knowledge_id,
                        collection_name=collection_name,
                        file_id=file_id,
                        file_version=file_version,
                        chunk_version=1,
                        chunk_index=index,
                        start_index=metadata.get('start_index'),
                        content_hash=content_hash,
                        chunker_config_hash=config_hash,
                        text=item['text'],
                        metadata_=metadata,
                        is_active=not self.staged,
                        created_at=now,
                        updated_at=now,
                    )
                    db.add(old)
                else:
                    old.is_active, old.updated_at = not self.staged, now
                evidence = await self._upsert_evidence(
                    db,
                    ref=ref,
                    file_id=file_id,
                    version=file_version,
                    knowledge_id=knowledge_id,
                    source_name=item.get('source_name') or source_name or file_id,
                    content_hash=content_hash,
                    modality='text',
                    chunk_uid=uid,
                    page_index=metadata.get('page_index'),
                    anchor=metadata.get('anchor'),
                    now=now,
                )
                if space:
                    mapping_status = 'ready' if compatible else 'quarantined'
                    if mapping_status == 'ready' and metadata.get('file_id') != file_id:
                        # Official pgvector pushes down a file_id metadata filter. Metadata-only
                        # backfill is required for a legacy row missing this field, not re-embedding.
                        mapping_status = 'metadata_pending'
                    await self._map(
                        db,
                        evidence,
                        space,
                        collection_name,
                        vector_id,
                        mapping_status,
                    )
            job.status, job.finished_at, job.updated_at = 'succeeded', now, now
            job.result = {
                'text_mapped': len(normalized),
                'embedding_calls': 0,
                'space_status': space.verification_status if space else 'unassigned',
                'metadata_backfill_ids': [v[4] for v in normalized if v[1].get('file_id') != file_id],
            }
            current.status = 'ready'
            await self._set_state(
                db,
                job,
                status=(
                    ('metadata_pending' if job.result['metadata_backfill_ids'] else 'ready')
                    if compatible
                    else 'quarantined'
                ),
                indexed=sum(v[1].get('file_id') == file_id for v in normalized) if compatible else 0,
            )
            await db.commit()
        # Text publication is independent of slower/failing images and lexical service.
        result = {
            'file_id': file_id,
            'file_version': file_version,
            'text_mapped': len(normalized),
            'text_embedding_calls': 0,
            'images': {'status': 'not_requested', 'indexed': 0},
            'metadata_backfill_ids': [v[4] for v in normalized if v[1].get('file_id') != file_id],
        }
        if assets:
            result['images'] = await self.index_assets(
                file_id=file_id,
                file_version=file_version,
                collection_name=collection_name,
                assets=assets,
                knowledge_id=knowledge_id,
                space_id=space_id,
                source_name=source_name,
            )
        if self.lexical_enabled and not self.staged:
            result['lexical'] = await self.index_lexical(
                file_id=file_id, file_version=file_version, knowledge_id=knowledge_id, collection_name=collection_name
            )
        return result

    async def _upsert_evidence(
        self,
        db,
        *,
        ref,
        file_id,
        version,
        knowledge_id,
        source_name,
        content_hash,
        modality,
        now,
        chunk_uid=None,
        asset_id=None,
        page_index=None,
        anchor=None,
    ):
        evidence = (await db.execute(select(Evidence).where(Evidence.evidence_ref == ref))).scalar_one_or_none()
        if evidence:
            if (
                evidence.file_id != file_id
                or evidence.file_version != version
                or evidence.content_hash != content_hash
                or evidence.knowledge_id != knowledge_id
            ):
                raise ValueError('evidence reference cannot be reused for different content or scope')
            evidence.is_active, evidence.updated_at, evidence.deleted_at = not self.staged, now, None
            return evidence
        evidence = Evidence(
            id=stable_id('evidence', ref=ref),
            evidence_ref=ref,
            knowledge_id=knowledge_id,
            file_id=file_id,
            file_version=version,
            retrieval_chunk_uid=chunk_uid,
            asset_id=asset_id,
            modality=modality,
            evidence_kind='text_chunk' if modality == 'text' else 'document_image',
            source_name=source_name,
            page_index=page_index,
            anchor_json=anchor,
            content_hash=content_hash,
            is_active=not self.staged,
            created_at=now,
            updated_at=now,
        )
        db.add(evidence)
        await db.flush()
        return evidence

    async def _map(self, db, evidence, space, collection, vector_id, status, error=None):
        identity = stable_id('mapping', ref=evidence.evidence_ref, space=space.id)
        mapping = await db.get(Embedding, identity)
        if mapping is None:
            mapping = Embedding(
                id=identity,
                evidence_id=evidence.id,
                evidence_ref=evidence.evidence_ref,
                vector_space_id=space.id,
                vector_backend_collection=collection,
                vector_backend_id=vector_id,
                content_hash=evidence.content_hash,
                config_fingerprint=space.config_fingerprint,
                embedding_status=status,
                embedding_error=error,
                created_at=int(time.time()),
                updated_at=int(time.time()),
            )
            db.add(mapping)
        else:
            if mapping.vector_backend_collection != collection or mapping.vector_backend_id != vector_id:
                raise ValueError('existing vector identity cannot be changed')
            mapping.embedding_status, mapping.embedding_error = status, error
            mapping.updated_at = int(time.time())
        return mapping

    async def _set_state(self, db, job, *, status, indexed=0, error=None):
        identity = stable_id(
            'state',
            file_id=job.file_id,
            version=job.file_version,
            knowledge_id=job.knowledge_id,
            kind=job.index_kind,
            target=job.target_config_hash,
        )
        state = await db.get(State, identity)
        if state is None:
            state = State(
                state_id=identity,
                index_kind=job.index_kind,
                file_id=job.file_id,
                file_version=job.file_version,
                knowledge_id=job.knowledge_id,
                collection_name=job.collection_name,
                target_config_hash=job.target_config_hash,
                created_at=int(time.time()),
                updated_at=int(time.time()),
                status=status,
            )
            db.add(state)
        state.status, state.indexed_chunk_count = status, indexed
        state.active_chunk_count = (job.result or {}).get(
            'text_mapped', len((job.payload or {}).get('assets', [])) if job.index_kind == 'image' else indexed
        )
        state.last_job_id, state.error, state.updated_at = job.job_id, error, int(time.time())

    async def enqueue_assets(
        self, *, file_id, file_version, collection_name, assets, knowledge_id=None, space_id=None, source_name=None
    ):
        """Persist image work before the application schedules its background task."""
        descriptors = [dict(a) for a in assets]
        async with core_db(self.db) as db:
            latest = await self._latest(db, file_id, lock=True)
            if not latest or latest.file_version != file_version or latest.status == 'deleted':
                raise ValueError('image indexing requires the accepted current file version')
            job = await self._job(
                db,
                kind='image',
                file_id=file_id,
                version=file_version,
                knowledge_id=knowledge_id,
                collection=collection_name,
                target=canonical_hash({'space': space_id, 'assets': descriptors}),
                payload={'assets': descriptors, 'space_id': space_id, 'source_name': source_name},
            )
            await db.commit()
            return job.job_id

    async def index_assets(
        self, *, file_id, file_version, collection_name, assets, knowledge_id=None, space_id=None, source_name=None
    ):
        """Supplement current-version images; explicit retry only, no retry storm."""
        now = int(time.time())
        async with core_db(self.db) as db:
            latest = await self._latest(db, file_id, lock=True)
            if not latest or latest.file_version != file_version or latest.status == 'deleted':
                raise ValueError('image indexing requires the accepted current file version')
            descriptors = [dict(a) for a in assets]
            job = await self._job(
                db,
                kind='image',
                file_id=file_id,
                version=file_version,
                knowledge_id=knowledge_id,
                collection=collection_name,
                target=canonical_hash({'space': space_id, 'assets': descriptors}),
                payload={'assets': descriptors, 'space_id': space_id, 'source_name': source_name},
            )
            if job.status == 'succeeded':
                return job.result
            if job.status == 'running':
                return {'status': 'indexing', 'indexed': 0, 'job_id': job.job_id}
            if job.retry_count >= job.max_retries:
                return {'status': 'failed', 'indexed': 0, 'error': 'retry limit reached', 'job_id': job.job_id}
            job.status, job.started_at = 'running', now
            job.retry_count += 1
            await db.commit()
            job_id = job.job_id
        indexed, failed = 0, []
        for descriptor in descriptors:
            try:
                await self._index_asset(
                    file_id=file_id,
                    version=file_version,
                    knowledge_id=knowledge_id,
                    collection=collection_name,
                    space_id=space_id,
                    source_name=source_name or file_id,
                    descriptor=descriptor,
                )
                indexed += 1
            except Exception as exc:
                # The error is classified without persisting remote response bodies/credentials.
                failed.append(type(exc).__name__)
                async with core_db(self.db) as db:
                    await db.rollback()
                    # A usable original image is distinct from a failed derived embedding.
                    refs = select(Evidence.evidence_ref).where(
                        Evidence.file_id == file_id,
                        Evidence.file_version == file_version,
                        Evidence.knowledge_id == knowledge_id,
                        Evidence.modality == 'image',
                        Evidence.content_hash == descriptor.get('sha256'),
                    )
                    await db.execute(
                        update(Embedding)
                        .where(
                            Embedding.evidence_ref.in_(refs),
                            Embedding.vector_space_id == space_id,
                            Embedding.embedding_status == 'pending',
                        )
                        .values(
                            embedding_status='failed', embedding_error=type(exc).__name__, updated_at=int(time.time())
                        )
                    )
                    await db.commit()
        result = {
            'status': 'partial' if failed and indexed else 'failed' if failed else 'ready',
            'indexed': indexed,
            'failed': len(failed),
            'job_id': job_id,
        }
        async with core_db(self.db) as db:
            job = await db.get(Job, job_id)
            job.status = 'failed' if failed else 'succeeded'
            job.error = ', '.join(failed) or None
            job.result, job.finished_at, job.updated_at = result, int(time.time()), int(time.time())
            await self._set_state(db, job, status=result['status'], indexed=indexed, error=job.error)
            await db.commit()
        return result

    async def _index_asset(
        self, *, file_id, version, knowledge_id, collection, space_id, source_name, descriptor
    ):  # noqa: C901
        required = ('sha256', 'mime_type')
        if any(not descriptor.get(k) for k in required) or not (
            descriptor.get('storage_uri') or descriptor.get('path')
        ):
            raise ValueError('asset descriptor requires hash, MIME and stored path')
        asset_id = descriptor.get('id') or stable_id(
            'asset',
            file_id=file_id,
            version=version,
            hash=descriptor['sha256'],
            page=descriptor.get('page_index'),
            anchor=descriptor.get('anchor'),
        )
        ref = descriptor.get('evidence_ref') or stable_id(
            'ke', knowledge_id=knowledge_id, file_id=file_id, version=version, asset_id=asset_id
        )
        now = int(time.time())
        async with core_db(self.db) as db:
            latest = await self._latest(db, file_id)
            if not latest or latest.file_version != version or latest.status == 'deleted':
                raise ValueError('source version is no longer current')
            asset = await db.get(Asset, asset_id)
            if asset is None:
                asset = Asset(
                    id=asset_id,
                    asset_ref=descriptor.get('asset_ref') or asset_id,
                    file_id=file_id,
                    file_version=version,
                    asset_kind=descriptor.get('asset_kind', 'document_image'),
                    storage_uri=descriptor.get('storage_uri') or descriptor['path'],
                    sha256=descriptor['sha256'],
                    mime_type=descriptor['mime_type'],
                    width=descriptor.get('width'),
                    height=descriptor.get('height'),
                    page_index=descriptor.get('page_index'),
                    anchor_json=descriptor.get('anchor') or descriptor.get('anchor_json'),
                    caption=descriptor.get('caption'),
                    ocr_text=descriptor.get('ocr_text'),
                    surrounding_text=descriptor.get('surrounding_text'),
                    status='ready',
                    created_at=now,
                    updated_at=now,
                )
                db.add(asset)
                await db.flush()
            elif asset.file_id != file_id or asset.file_version != version or asset.sha256 != descriptor['sha256']:
                raise ValueError('asset identity collision')
            evidence = await self._upsert_evidence(
                db,
                ref=ref,
                file_id=file_id,
                version=version,
                knowledge_id=knowledge_id,
                source_name=source_name,
                content_hash=descriptor['sha256'],
                modality='image',
                now=now,
                asset_id=asset_id,
                page_index=asset.page_index,
                anchor=asset.anchor_json,
            )
            space = await db.get(Space, space_id) if space_id else None
            if space is None:
                await db.commit()
                raise ValueError('image encoding space is unassigned')
            vector_id = descriptor.get('vector_id') or stable_id(
                'image_vector', file_id=file_id, version=version, asset_id=asset_id, space=space.id
            )
            image_collection = descriptor.get('vector_collection') or collection + '-images'
            mapping = (
                await db.execute(
                    select(Embedding).where(Embedding.evidence_ref == ref, Embedding.vector_space_id == space.id)
                )
            ).scalar_one_or_none()
            if mapping and mapping.embedding_status == 'ready':
                return
            if space.verification_status != 'verified' or not space.active:
                await self._map(db, evidence, space, image_collection, vector_id, 'quarantined')
                await db.commit()
                raise ValueError('image encoding space is quarantined')
            if not space.supports_image_evidence:
                await self._map(db, evidence, space, image_collection, vector_id, 'quarantined')
                await db.commit()
                raise ValueError('space does not support image evidence')
            # The same physical image may be attached to several KBs. A new
            # scope creates a reference, not another image embedding.
            shared = (
                await db.execute(
                    select(Embedding)
                    .join(Evidence, Evidence.evidence_ref == Embedding.evidence_ref)
                    .where(
                        Evidence.asset_id == asset_id,
                        Evidence.file_version == version,
                        (
                            (
                                Evidence.is_active.is_(True)
                                | ((Evidence.file_id == file_id) & (Evidence.file_version == version))
                            )
                            if self.staged
                            else Evidence.is_active.is_(True)
                        ),
                        Evidence.deleted_at.is_(None),
                        Embedding.vector_space_id == space.id,
                        Embedding.embedding_status == 'ready',
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if shared:
                await self._map(
                    db, evidence, space, shared.vector_backend_collection, shared.vector_backend_id, 'ready'
                )
                await db.commit()
                return
            await self._map(db, evidence, space, image_collection, vector_id, 'pending')
            await db.commit()
            if descriptor.get('vector_id'):
                # Importer explicitly supplies an existing, provenance-checked vector. Do not encode it again.
                vector = None
            else:
                if not self.embedding or not self.asset_reader or not self.vector_client:
                    raise ValueError('image encoder, asset reader and vector client are required')
                image_bytes = await self.asset_reader(asset)
                if hashlib.sha256(image_bytes).hexdigest() != asset.sha256:
                    raise ValueError('stored asset hash mismatch')
                vector = await self.embedding.encode(
                    space=space, image_bytes=image_bytes, mime_type=asset.mime_type, query_text='', document=True
                )
            if vector is not None:
                await self.vector_client.upsert(
                    image_collection,
                    [
                        dict(
                            id=vector_id,
                            text=asset.caption or '',
                            vector=vector,
                            metadata={
                                'file_id': file_id,
                                'file_version': version,
                                'evidence_ref': ref,
                                'space_id': space.id,
                                'modality': 'image',
                            },
                        )
                    ],
                )
            # Recheck source after slow external encoding. A concurrent update/delete wins.
            db.expire_all()
            latest = await self._latest(db, file_id, lock=True)
            if not latest or latest.file_version != version or latest.status == 'deleted':
                await db.rollback()
                if vector is not None:
                    # A delete/update may finish while the encoder was running. Track any late
                    # derived vector as a cleanup job; never invalidate the newer source version.
                    cleanup_job = await self._job(
                        db,
                        kind='delete',
                        file_id=file_id,
                        version=version,
                        knowledge_id=knowledge_id,
                        collection=image_collection,
                        target=canonical_hash({'orphan': vector_id}),
                        payload={'targets': [(image_collection, vector_id)], 'refs': []},
                    )
                    cleanup_job.status = 'pending'
                    await db.commit()
                raise ValueError('source changed during image encoding')
            evidence = (await db.execute(select(Evidence).where(Evidence.evidence_ref == ref))).scalar_one()
            space = await db.get(Space, space_id)
            await self._map(db, evidence, space, image_collection, vector_id, 'ready')
            evidence.is_active = not self.staged
            await db.commit()

    async def index_lexical(self, *, file_id, file_version, knowledge_id=None, collection_name=None):
        if not self.lexical_enabled:
            return {'status': 'disabled'}
        if not self.lexical_factory:
            return {'status': 'failed', 'error': 'lexical adapter is not configured'}
        async with core_db(self.db) as db:
            latest = await self._latest(db, file_id)
            if not latest or latest.file_version != file_version or latest.status == 'deleted':
                raise ValueError('lexical indexing requires current source version')
            rows = (
                (
                    await db.execute(
                        select(Evidence).where(
                            Evidence.file_id == file_id,
                            Evidence.file_version == file_version,
                            Evidence.knowledge_id == knowledge_id,
                            Evidence.is_active.is_(True),
                        )
                    )
                )
                .scalars()
                .all()
            )
            documents = []
            for row in rows:
                chunk = (
                    (
                        await db.execute(select(Chunk).where(Chunk.chunk_uid == row.retrieval_chunk_uid))
                    ).scalar_one_or_none()
                    if row.retrieval_chunk_uid
                    else None
                )
                asset = await db.get(Asset, row.asset_id) if row.asset_id else None
                text = (
                    chunk.text
                    if chunk
                    else (
                        '\n'.join(filter(None, (asset.caption, asset.ocr_text, asset.surrounding_text)))
                        if asset
                        else ''
                    )
                )
                if text:
                    documents.append(
                        {
                            'evidence_ref': row.evidence_ref,
                            'file_id': file_id,
                            'file_version': file_version,
                            'knowledge_id': knowledge_id,
                            'modality': row.modality,
                            'text': text,
                        }
                    )
            target = canonical_hash(documents)
            job = await self._job(
                db,
                kind='lexical',
                file_id=file_id,
                version=file_version,
                knowledge_id=knowledge_id,
                collection=collection_name,
                target=target,
                payload={},
            )
            job.status = 'running'
            await db.commit()
            try:
                await self.lexical_factory().index(documents)
                job.status, job.error = 'succeeded', None
            except Exception as exc:
                job.status, job.error = 'failed', type(exc).__name__
            job.finished_at = job.updated_at = int(time.time())
            await self._set_state(
                db,
                job,
                status='ready' if job.status == 'succeeded' else 'failed',
                indexed=len(documents) if job.status == 'succeeded' else 0,
                error=job.error,
            )
            await db.commit()
            return {'status': job.status, 'indexed': len(documents) if job.status == 'succeeded' else 0}

    async def _cleanup_targets(self, db, targets, refs):
        failures = []
        for collection, vector_id in dict.fromkeys(tuple(t) for t in targets):
            # A physical vector can serve multiple KB evidence records. Never delete a shared row.
            survivors = (
                await db.execute(
                    select(Embedding.id)
                    .join(Evidence, Evidence.id == Embedding.evidence_id)
                    .where(
                        Embedding.vector_backend_collection == collection,
                        Embedding.vector_backend_id == vector_id,
                        Evidence.is_active.is_(True),
                    )
                    .limit(1)
                )
            ).first()
            if survivors:
                continue
            try:
                if not self.vector_client:
                    raise RuntimeError('vector client is not configured')
                await self.vector_client.delete(collection, ids=[vector_id])
            except Exception as exc:
                failures.append(type(exc).__name__)
        if self.lexical_enabled and self.lexical_factory:
            try:
                await self.lexical_factory().delete(refs)
            except Exception as exc:
                failures.append(type(exc).__name__)
        return failures

    async def deactivate_file(self, file_id, *, knowledge_id=None, cleanup=True):
        """SQL invalidation first; deletion failures never expose stale sources."""
        now = int(time.time())
        async with core_db(self.db) as db:
            latest = await self._latest(db, file_id, lock=True)
            conditions = [Evidence.file_id == file_id]
            if knowledge_id is not None:
                conditions.append(Evidence.knowledge_id == knowledge_id)
            refs = list((await db.execute(select(Evidence.evidence_ref).where(*conditions))).scalars())
            mappings = (
                (await db.execute(select(Embedding).where(Embedding.evidence_ref.in_(refs)))).scalars().all()
                if refs
                else []
            )
            targets = [(m.vector_backend_collection, m.vector_backend_id) for m in mappings]
            await db.execute(
                update(Evidence).where(*conditions).values(is_active=False, deleted_at=now, updated_at=now)
            )
            if knowledge_id is None:
                await db.execute(
                    update(Chunk)
                    .where(Chunk.file_id == file_id)
                    .values(is_active=False, deleted_at=now, updated_at=now, text=None, metadata_=None)
                )
                await db.execute(
                    update(Asset)
                    .where(Asset.file_id == file_id)
                    .values(status='deleted', updated_at=now, caption=None, ocr_text=None, surrounding_text=None)
                )
                # Resume payloads can contain parser captions/OCR; keep only tombstones on deletion.
                await db.execute(
                    update(Job)
                    .where(Job.file_id == file_id, Job.index_kind != 'delete')
                    .values(payload=None, updated_at=now)
                )
                if latest:
                    latest.status, latest.updated_at = 'deleted', now
            job = await self._job(
                db,
                kind='delete',
                file_id=file_id,
                version=latest.file_version if latest else 1,
                knowledge_id=knowledge_id,
                collection=None,
                target=canonical_hash(refs),
                payload={'targets': targets, 'refs': refs},
            )
            job.status = 'running'
            await db.commit()
            failures = await self._cleanup_targets(db, targets, refs) if cleanup else []
            job.status = 'failed' if failures else 'succeeded' if cleanup else 'pending'
            job.error, job.finished_at, job.updated_at = ', '.join(failures) or None, now, now
            await self._set_state(db, job, status='failed' if failures else 'deleted', error=job.error)
            await db.commit()
            return {'status': job.status, 'invalidated': len(refs), 'cleanup_failures': len(failures)}

    async def recover_jobs(self, *, limit=25):
        """At single-owner startup only: return interrupted *new* jobs to pending, no history replay.

        This does not execute network work. The application may dispatch returned IDs with its
        existing background task mechanism. Never run concurrently with active indexing workers.
        """
        async with core_db(self.db) as db:
            rows = (
                (
                    await db.execute(
                        select(Job)
                        .where(
                            Job.executor_version == EXECUTOR_VERSION,
                            Job.status.in_(('pending', 'running')),
                            Job.retry_count < Job.max_retries,
                        )
                        .order_by(Job.created_at)
                        .limit(min(max(limit, 1), 100))
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                row.status, row.updated_at = 'pending', int(time.time())
            await db.commit()
            return [row.job_id for row in rows]

    async def run_job(self, job_id):
        """Dispatch a persisted new job after single-owner recovery or an explicit retry."""
        async with core_db(self.db) as db:
            job = await db.get(Job, job_id)
            if not job or job.executor_version != EXECUTOR_VERSION:
                raise ValueError('historical jobs cannot be replayed by this executor')
            if job.status == 'running':
                raise ValueError('job is owned by an active worker; use startup recovery after it has stopped')
            if job.status == 'succeeded':
                return job.result or {'status': 'succeeded'}
            kind, payload = job.index_kind, dict(job.payload or {})
            common = dict(
                file_id=job.file_id,
                file_version=job.file_version,
                knowledge_id=job.knowledge_id,
                collection_name=job.collection_name,
            )
            if kind in ('image', 'lexical'):
                latest = await self._latest(db, job.file_id)
                if not latest or latest.file_version != job.file_version or latest.status == 'deleted':
                    job.status = 'cancelled'
                    job.finished_at = job.updated_at = int(time.time())
                    await db.commit()
                    return {'status': 'cancelled', 'reason': 'source version is no longer current'}
        if kind == 'image':
            return await self.index_assets(
                **common,
                assets=payload.get('assets', []),
                space_id=payload.get('space_id'),
                source_name=payload.get('source_name'),
            )
        if kind == 'lexical':
            return await self.index_lexical(**common)
        if kind == 'delete':
            # Replay only the recorded physical targets, never re-invalidate a possibly newer file.
            async with core_db(self.db) as db:
                failures = await self._cleanup_targets(db, payload.get('targets', []), payload.get('refs', []))
                job = await db.get(Job, job_id)
                job.status = 'failed' if failures else 'succeeded'
                job.error = ', '.join(failures) or None
                job.finished_at = job.updated_at = int(time.time())
                await self._set_state(db, job, status='failed' if failures else 'deleted', error=job.error)
                await db.commit()
                return {'status': job.status, 'cleanup_failures': len(failures)}
        raise ValueError('text mapping is an atomic transaction; re-submit its source items, not an old job')
