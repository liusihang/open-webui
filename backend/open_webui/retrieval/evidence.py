"""One authorized retrieval/read path for automatic RAG and Native knowledge tools."""

import hashlib
import math
from dataclasses import dataclass

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
from open_webui.models.evidence import stable_id
from open_webui.models.retrieval_chunks import RetrievalChunk as Chunk
from open_webui.models.retrieval_chunks import core_db
from open_webui.models.retrieval_indexes import RetrievalIndexState as State
from open_webui.retrieval.hybrid import reciprocal_rank_fusion
from open_webui.retrieval.vector.embedding_adapter import validate_image
from sqlalchemy import and_, exists, or_, select


@dataclass(frozen=True)
class ResolvedScope:
    knowledge_ids: tuple[str, ...] = ()
    file_ids: tuple[str, ...] = ()


async def resolve_scope(user, *, knowledge_ids=(), file_ids=(), db=None):
    """Revalidate server-supplied scope using official ownership/AccessGrants semantics.

    No scope means no search, not an implicit site-wide scan. This function never expands
    a caller's scope. A ResolvedScope is a request value, not an enduring authorization grant.
    """
    from open_webui.models.files import Files
    from open_webui.models.knowledge import Knowledges
    from open_webui.utils.access_control.files import has_access_to_file

    if not user or not getattr(user, 'id', None):
        raise PermissionError('authenticated user is required')
    allowed_kb, allowed_files = [], []
    for identity in dict.fromkeys(knowledge_ids or ()):
        if not isinstance(identity, str) or not identity:
            continue
        knowledge = await Knowledges.get_knowledge_by_id(identity, db=db)
        if knowledge and (
            user.role == 'admin'
            or knowledge.user_id == user.id
            or await Knowledges.check_access_by_user_id(identity, user.id, 'read', db=db)
        ):
            allowed_kb.append(identity)
    for identity in dict.fromkeys(file_ids or ()):
        if not isinstance(identity, str) or not identity:
            continue
        file = await Files.get_file_by_id(identity, db=db)
        if file and (
            user.role == 'admin' or file.user_id == user.id or await has_access_to_file(identity, 'read', user, db=db)
        ):
            allowed_files.append(identity)
    return ResolvedScope(tuple(allowed_kb), tuple(allowed_files))


class EvidenceService:
    def __init__(
        self,
        *,
        db=None,
        vector_client=None,
        embedding=None,
        asset_reader=None,
        lexical_enabled=False,
        lexical_factory=None,
        reranker=None,
    ):
        self.db = db
        self.vector_client = vector_client
        self.embedding = embedding
        self.asset_reader = asset_reader
        self.lexical_enabled = lexical_enabled
        self.lexical_factory = lexical_factory
        self.reranker = reranker

    async def _scope(self, user, scope):
        if not isinstance(scope, ResolvedScope):
            raise TypeError('resolved_scope must be a ResolvedScope')
        return await resolve_scope(user, knowledge_ids=scope.knowledge_ids, file_ids=scope.file_ids, db=self.db)

    def _visible(self, scope):
        # Membership is rechecked in SQL, not inferred from an old vector's metadata.
        from open_webui.models.files import File
        from open_webui.models.knowledge import KnowledgeFile

        by_kb = and_(
            Evidence.knowledge_id.in_(scope.knowledge_ids),
            exists(
                select(KnowledgeFile.id).where(
                    KnowledgeFile.knowledge_id == Evidence.knowledge_id, KnowledgeFile.file_id == Evidence.file_id
                )
            ),
        )
        by_file = Evidence.file_id.in_(scope.file_ids)
        current = exists(
            select(State.state_id).where(
                State.file_id == Evidence.file_id,
                State.state_id.like('file_%'),
                State.file_version == Evidence.file_version,
                State.status != 'deleted',
            )
        )
        return and_(
            Evidence.is_active.is_(True),
            Evidence.deleted_at.is_(None),
            or_(by_kb, by_file),
            current,
            exists(select(File.id).where(File.id == Evidence.file_id)),
        )

    async def _rows(self, scope, *, refs=None, modalities=('text', 'image')):
        async with core_db(self.db) as db:
            statement = select(Evidence, Chunk, Asset).outerjoin(Chunk, Chunk.chunk_uid == Evidence.retrieval_chunk_uid)
            statement = statement.outerjoin(Asset, Asset.id == Evidence.asset_id).where(
                self._visible(scope), Evidence.modality.in_(modalities)
            )
            if refs is not None:
                statement = statement.where(Evidence.evidence_ref.in_(refs))
            rows = (await db.execute(statement)).all()
            valid = {}
            for row, chunk, asset in rows:
                if row.modality == 'text' and (
                    not chunk
                    or not chunk.is_active
                    or chunk.deleted_at
                    or chunk.file_version != row.file_version
                    or chunk.content_hash != row.content_hash
                ):
                    continue
                if row.modality == 'image' and (
                    not asset
                    or asset.status != 'ready'
                    or asset.file_version != row.file_version
                    or asset.sha256 != row.content_hash
                ):
                    continue
                valid[row.evidence_ref] = (row, chunk, asset)
            return valid

    def _item(self, parts, scope):
        row, chunk, asset = parts
        text = chunk.text if chunk else '\n'.join(filter(None, (asset.caption, asset.ocr_text, asset.surrounding_text)))
        source = {
            'file_id': row.file_id,
            'knowledge_id': row.knowledge_id if row.knowledge_id in scope.knowledge_ids else None,
            'name': row.source_name,
            'file_version': row.file_version,
            'page_index': row.page_index,
            'anchor': row.anchor_json,
        }
        item = {
            'evidence_ref': row.evidence_ref,
            'modality': row.modality,
            'content': text or '',
            'preview': (text or '')[:1000],
            'source': source,
            'status': 'ready',
            'citation_key': stable_id(
                'source',
                file_id=row.file_id,
                version=row.file_version,
                modality=row.modality,
                location=(chunk.chunk_index, chunk.start_index, row.content_hash) if chunk else row.asset_id,
            ),
        }
        if asset:
            item['asset_id'] = asset.id
            item['mime_type'] = asset.mime_type
            item['image_available'] = True
            item['model_note'] = 'Image found. Text-only models have not seen the original image.'
        # No storage_uri, permanent public link, credentials or arbitrary fetching location.
        return item

    @staticmethod
    def _deduplicate(items, seen_refs=None):
        seen = set(seen_refs or ())
        result = []
        for item in items:
            ref, key = item['evidence_ref'], item.get('citation_key', item['evidence_ref'])
            if ref in seen or key in seen:
                continue
            seen.update((ref, key))
            result.append(item)
        if seen_refs is not None:
            seen_refs.update(seen)
        return result

    async def read(self, evidence_refs, *, user, resolved_scope, seen_refs=None):
        scope = await self._scope(user, resolved_scope)
        if not isinstance(evidence_refs, (list, tuple)):
            raise ValueError('evidence_refs must be a list of stored references')
        refs = list(dict.fromkeys(evidence_refs))
        if len(refs) > 100 or any(not isinstance(r, str) for r in refs):
            raise ValueError('read accepts at most 100 evidence references')
        rows = await self._rows(scope, refs=refs)
        items = []
        for ref in refs:
            if ref not in rows:
                # Indistinguishable absent/unauthorized/old version; never redirect to new content.
                items.append({'evidence_ref': ref, 'status': 'unavailable'})
            else:
                items.append(self._item(rows[ref], scope))
        # Explicit reads must work even after search/read returned the reference.
        # Deduplicate this response only, while retaining history for later searches.
        read_refs = set()
        items = self._deduplicate(items, read_refs)
        if seen_refs is not None:
            seen_refs.update(read_refs)
        return {'items': items, 'warnings': []}

    async def resolve_query_image(self, reference, *, user, resolved_scope):  # noqa: C901
        """Accept a stored asset/evidence ID or uploaded file ID; authorize before reading bytes."""
        if (
            not isinstance(reference, str)
            or not reference
            or len(reference) > 512
            or any(token in reference for token in ('://', 'data:', '\\', '/'))
        ):
            raise ValueError('query image must be a stored file, asset or evidence reference')
        scope = await self._scope(user, resolved_scope)
        async with core_db(self.db) as db:
            asset = (
                await db.execute(select(Asset).where(or_(Asset.id == reference, Asset.asset_ref == reference)))
            ).scalar_one_or_none()
            if not asset:
                row = (
                    await db.execute(select(Evidence).where(Evidence.evidence_ref == reference))
                ).scalar_one_or_none()
                if row and row.asset_id:
                    asset = await db.get(Asset, row.asset_id)
            if asset:
                # Query images may be separate uploads, not necessarily inside the searched KB.
                file_scope = await resolve_scope(user, file_ids=(asset.file_id,), db=self.db)
                combined = ResolvedScope(scope.knowledge_ids, tuple(set(scope.file_ids + file_scope.file_ids)))
                rows = await self._rows(combined, modalities=('image',))
                if not any(parts[0].asset_id == asset.id for parts in rows.values()):
                    raise PermissionError('image is unavailable')
                if not self.asset_reader:
                    raise ValueError('stored asset reader is not configured')
                image_bytes = await self.asset_reader(asset)
                validate_image(image_bytes, asset.mime_type)
                if hashlib.sha256(image_bytes).hexdigest() != asset.sha256:
                    raise ValueError('stored image hash mismatch')
                final_scope = await self._scope(user, combined)
                current = await self._rows(final_scope, refs=list(rows), modalities=('image',))
                if not any(parts[0].asset_id == asset.id for parts in current.values()):
                    raise PermissionError('image is no longer available')
                return image_bytes, asset.mime_type
        from open_webui.models.files import Files
        from open_webui.storage.provider import Storage

        authorized = await resolve_scope(user, file_ids=(reference,), db=self.db)
        if reference not in authorized.file_ids:
            raise PermissionError('image is unavailable')
        file = await Files.get_file_by_id(reference, db=self.db)
        mime = (file.meta or {}).get('content_type', '')
        if not mime.startswith('image/'):
            raise ValueError('query file is not an image')
        import asyncio
        from pathlib import Path

        from open_webui.retrieval.vector.embedding_adapter import MAX_IMAGE_BYTES

        def load():
            path = Path(Storage.get_file(file.path))
            if path.stat().st_size > MAX_IMAGE_BYTES:
                raise ValueError('query image exceeds size limit')
            with path.open('rb') as handle:
                return handle.read(MAX_IMAGE_BYTES + 1)

        data = await asyncio.to_thread(load)
        if reference not in (await resolve_scope(user, file_ids=(reference,), db=self.db)).file_ids:
            raise PermissionError('image is no longer available')
        return validate_image(data, mime), mime

    async def image_content(self, evidence_ref, *, user, resolved_scope):
        """Authorized route seam for original/thumbnail/model image bytes."""
        result = await self.read([evidence_ref], user=user, resolved_scope=resolved_scope)
        if (
            not result['items']
            or result['items'][0].get('status') != 'ready'
            or result['items'][0].get('modality') != 'image'
        ):
            raise PermissionError('image is unavailable')
        return await self.resolve_query_image(evidence_ref, user=user, resolved_scope=resolved_scope)

    async def _groups(self, scope, *, image, modalities):
        # Candidate selection loads identities/mappings only, never the KB's full text bodies.
        valid_text = and_(
            Evidence.modality == 'text',
            exists(
                select(Chunk.row_id).where(
                    Chunk.chunk_uid == Evidence.retrieval_chunk_uid,
                    Chunk.file_version == Evidence.file_version,
                    Chunk.content_hash == Evidence.content_hash,
                    Chunk.is_active.is_(True),
                    Chunk.deleted_at.is_(None),
                )
            ),
        )
        valid_image = and_(
            Evidence.modality == 'image',
            exists(
                select(Asset.id).where(
                    Asset.id == Evidence.asset_id,
                    Asset.file_version == Evidence.file_version,
                    Asset.sha256 == Evidence.content_hash,
                    Asset.status == 'ready',
                )
            ),
        )
        async with core_db(self.db) as db:
            visible = self._visible(scope)
            refs = list(
                (
                    await db.scalars(
                        select(Evidence.evidence_ref).where(
                            visible, Evidence.modality.in_(modalities), or_(valid_text, valid_image)
                        )
                    )
                ).all()
            )
            if not refs:
                return {}, []
            pairs = (
                await db.execute(
                    select(Embedding, Space, Evidence.file_id)
                    .join(Space, Space.id == Embedding.vector_space_id)
                    .join(Evidence, Evidence.id == Embedding.evidence_id)
                    .where(
                        visible,
                        Evidence.modality.in_(modalities),
                        or_(valid_text, valid_image),
                        Embedding.embedding_status == 'ready',
                        Embedding.content_hash == Evidence.content_hash,
                        Space.active.is_(True),
                        Space.verification_status.in_(('verified', 'legacy_read_only')),
                        Space.embedding_dim == 2048,
                        or_(
                            and_(Evidence.modality == 'text', Space.supports_text_evidence.is_(True)),
                            and_(Evidence.modality == 'image', Space.supports_image_evidence.is_(True)),
                        ),
                    )
                )
            ).all()
        groups = {}
        for mapping, space, file_id in pairs:
            if mapping.config_fingerprint != space.config_fingerprint:
                continue
            try:
                self.embedding.validate_space(space, image=image)
            except ValueError:
                continue
            key = (space.id, mapping.vector_backend_collection)
            group = groups.setdefault(key, {'space': space, 'vectors': {}, 'files': set()})
            group['vectors'].setdefault(mapping.vector_backend_id, []).append(mapping.evidence_ref)
            group['files'].add(file_id)
        return groups, refs

    async def _vector_ranking(self, collection, group, vector, count, seen_refs):
        ranking, warnings, succeeded, scores = [], [], False, {}
        # Bounded over-fetch; never disable ANN indexes or perform a default exact scan.
        for limit in (min(count * 4, 200), min(count * 16, 800)):
            try:
                result = await self.vector_client.search(
                    collection, vectors=[vector], filter={'file_id': {'$in': sorted(group['files'])}}, limit=limit
                )
                if result is None:
                    raise RuntimeError('vector backend did not return a result')
                succeeded = True
            except Exception:
                warnings.append('Vector branch unavailable.')
                break
            ids = result.ids[0] if result is not None and result.ids else []
            similarities = result.distances[0] if result is not None and result.distances else []
            if len(similarities) != len(ids) or any(not math.isfinite(score) for score in similarities):
                raise ValueError('pgvector candidates require finite comparable cosine scores')
            # Official pgvector exposes (2 - cosine_distance) / 2: higher is better.
            # Compare these only within the SAME verified encoding space.
            for identity, similarity in zip(ids, similarities):
                for ref in group['vectors'].get(identity, ()):
                    if seen_refs is None or ref not in seen_refs:
                        scores[ref] = max(scores.get(ref, -math.inf), similarity)
            refs = (ref for identity in ids for ref in group['vectors'].get(identity, ()))
            ranking = list(dict.fromkeys(ref for ref in refs if seen_refs is None or ref not in seen_refs))
            if len(ranking) >= count or len(ids) < limit:
                break
        if succeeded and len(ranking) < count and len(ids) >= min(count * 16, 800):
            warnings.append('Bounded candidate scan exhausted after source/space filtering.')
        return ranking, warnings, succeeded, scores

    async def _rankings(self, groups, refs, query_text, image_bytes, mime, user, count, seen_refs, vector_scores=None):
        warnings, rankings, encoded, space_scores = [], [], {}, {}
        successful = 0
        for (_, collection), group in groups.items():
            space = group['space']
            if space.id not in encoded:
                try:
                    encoded[space.id] = await self.embedding.encode(
                        space=space, query_text=query_text, image_bytes=image_bytes, mime_type=mime, user=user
                    )
                except ValueError:
                    raise  # Never hide a dimension/space/numerical invariant violation.
                except Exception:
                    warnings.append('Vector encoding unavailable.')
                    continue
            ranking, errors, succeeded, scores = await self._vector_ranking(
                collection, group, encoded[space.id], count, seen_refs
            )
            warnings.extend(errors)
            successful += int(succeeded)
            merged = space_scores.setdefault(space.id, {})
            for ref, score in scores.items():
                merged[ref] = max(merged.get(ref, -math.inf), score)
        for scores in space_scores.values():
            rankings.append(sorted(scores, key=scores.get, reverse=True))
        if vector_scores is not None:
            vector_scores.update(space_scores)
        if self.lexical_enabled and query_text.strip():
            try:
                if not self.lexical_factory:
                    raise ValueError('lexical adapter is not configured')
                lexical = await self.lexical_factory().search(query_text, evidence_refs=refs, count=min(count * 4, 200))
                permitted = set(refs)
                rankings.append(
                    [ref for ref in lexical if ref in permitted and (seen_refs is None or ref not in seen_refs)]
                )
                successful += 1
            except Exception:
                warnings.append('Keyword branch unavailable; vector results only.')
        if not successful:
            raise RuntimeError('all retrieval branches failed')
        return reciprocal_rank_fusion(*rankings, count=min(count * 4, 200)), warnings

    async def _rerank(self, query_text, items):
        text_items = [item for item in items if item['modality'] == 'text']
        if not self.reranker or not query_text.strip() or not text_items:
            return items
        # Only already-authorized text reaches the optional official reranker adapter.
        reranked = await self.reranker(query_text, text_items)
        allowed = {item['evidence_ref']: item for item in text_items}
        refs = [r['evidence_ref'] if isinstance(r, dict) else r for r in reranked]
        reordered = [allowed.pop(ref) for ref in dict.fromkeys(refs) if ref in allowed]
        reordered.extend(allowed.values())
        iterator = iter(reordered)
        return [next(iterator) if item['modality'] == 'text' else item for item in items]

    @staticmethod
    def _validate_query(query_text, query_image_refs, modalities, count):
        if not isinstance(query_text, str) or len(query_text) > 16000:
            raise ValueError('query_text must be a string of at most 16000 characters')
        if len(query_image_refs) > 1 or not set(modalities) <= {'text', 'image'} or not modalities:
            raise ValueError('one query image and text/image modalities are supported')
        if not query_text.strip() and not query_image_refs:
            raise ValueError('text or an authorized query image is required')
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 50:
            raise ValueError('count must be between 1 and 50')

    async def search(
        self,
        query_text='',
        query_image_refs=(),
        modalities=('text', 'image'),
        count=5,
        *,
        user,
        resolved_scope,
        seen_refs=None,
        include_vector_scores=False,
    ):
        self._validate_query(query_text, query_image_refs, modalities, count)
        scope = await self._scope(user, resolved_scope)
        if not scope.knowledge_ids and not scope.file_ids:
            return {'items': [], 'warnings': []}
        image_bytes, mime = None, None
        if query_image_refs:
            image_bytes, mime = await self.resolve_query_image(query_image_refs[0], user=user, resolved_scope=scope)
        if not self.embedding or not self.vector_client:
            raise ValueError('evidence encoder and vector client are not configured')
        groups, refs = await self._groups(scope, image=image_bytes is not None, modalities=modalities)
        if not refs:
            return {'items': [], 'warnings': []}
        if not groups and not (self.lexical_enabled and query_text.strip()):
            return {'items': [], 'warnings': ['No verified compatible vector space is available.']}
        vector_scores = {} if include_vector_scores else None
        ranked, warnings = await self._rankings(
            groups, refs, query_text, image_bytes, mime, user, count, seen_refs, vector_scores=vector_scores
        )
        warnings.extend(
            'Legacy raw-v1 compatibility results; historical encoder revision is unverified.'
            for group in groups.values()
            if group['space'].verification_status == 'legacy_read_only'
        )
        # Revocation/update during a slow encoding/DB request wins before reranker/LLM exposure.
        final_scope = await self._scope(user, scope)
        final_rows = await self._rows(final_scope, refs=[ref for ref, _ in ranked], modalities=modalities)
        items = []
        for ref, score in ranked:
            if ref in final_rows:
                item = self._item(final_rows[ref], final_scope)
                item.update(score=score, score_type='rrf')
                if vector_scores is not None:
                    item['vector_scores'] = {
                        space: scores[ref] for space, scores in vector_scores.items() if ref in scores
                    }
                items.append(item)
        items = await self._rerank(query_text, items)
        # A reranker can be slow as well; recheck on the final hand-off to the model.
        final_scope = await self._scope(user, final_scope)
        available = await self._rows(final_scope, refs=[item['evidence_ref'] for item in items])
        items = self._deduplicate([item for item in items if item['evidence_ref'] in available])[:count]
        if seen_refs is not None:
            items = self._deduplicate(items, seen_refs)
        return {'items': items, 'warnings': list(dict.fromkeys(warnings))}
