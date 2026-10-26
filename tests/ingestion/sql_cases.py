"""Actual queue/publication SQL on isolated SQLite; no application startup/network.

Only app import/bootstrap is replaced. Queue, models, IndexingService and
publication functions are loaded from production files, not reimplemented.
"""

import ast
import asyncio
from contextlib import asynccontextmanager
import importlib
from pathlib import Path
import sys
import tempfile
import time
from types import ModuleType
import unittest
from typing import Any, ClassVar
import logging
import os
import io
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock, Mock

from sqlalchemy import (
    JSON,
    Column,
    String,
    Text,
    Integer,
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    UniqueConstraint,
    select,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

ROOT = Path(__file__).resolve().parents[2] / 'backend/open_webui'
for package, path in [
    ('open_webui', ROOT),
    ('open_webui.models', ROOT / 'models'),
    ('open_webui.internal', ROOT / 'internal'),
    ('open_webui.retrieval', ROOT / 'retrieval'),
]:
    module = ModuleType(package)
    module.__path__ = [str(path)]
    sys.modules[package] = module

Base = declarative_base()
db_module = ModuleType('open_webui.internal.db')
db_module.Base, db_module.JSONField = Base, JSON
session_factory = None


@asynccontextmanager
async def sessions(db=None):
    if db is not None:
        yield db
    else:
        async with session_factory() as session:
            yield session


db_module.get_async_db_context = db_module.get_async_db = sessions
sys.modules[db_module.__name__] = db_module


def model_classes(name, classes):
    source = ROOT / 'models' / (name + '.py')
    tree = ast.parse(source.read_text())
    body = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name in classes]
    module = ModuleType('open_webui.models.' + name)
    module.__dict__.update(globals())
    exec(compile(ast.Module(body=body, type_ignores=[]), str(source), 'exec'), module.__dict__)
    sys.modules['open_webui.models.' + name] = module
    return module


File = model_classes('files', {'File'}).File
model_classes('knowledge', {'Knowledge', 'KnowledgeDirectory', 'KnowledgeFile'})
queue = importlib.import_module('open_webui.retrieval.ingestion_queue')
ingestion = importlib.import_module('open_webui.retrieval.ingestion')
from open_webui.models.ingestion_jobs import IngestionJob as Job
from open_webui.models.evidence import (
    KnowledgeEvidence as Evidence,
    stable_id,
    KnowledgeEvidenceEmbedding as Mapping,
    KnowledgeVectorSpace as Space,
)
from open_webui.models.retrieval_chunks import RetrievalChunk as Chunk
from open_webui.models.retrieval_indexes import RetrievalIndexState as State
from open_webui.retrieval.indexing import IndexingService

# Config's production seed implementation, with only JSON encoding/bootstrap
# replaced; use a real mapped Config table and independent DB sessions.
source = ROOT / 'models/config.py'
tree = ast.parse(source.read_text())
nodes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Config']
namespace = {
    **globals(),
    'get_async_db': sessions,
    '_json_value': lambda value: value,
    'AsyncSession': Any,
    'log': logging.getLogger(__name__),
}
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)
Config = namespace['Config']


class QueueSQLTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        global session_factory
        self.temp = tempfile.TemporaryDirectory()
        url = os.getenv('INGESTION_TEST_DB_URL')
        if url and url not in (
            'postgresql+asyncpg://fixture:fixture@owui-office-fixture-pg/fixture',
            'postgresql+psycopg://fixture:fixture@owui-office-fixture-pg/fixture',
        ):
            raise ValueError('Only the isolated office fixture PostgreSQL endpoint is allowed')
        self.engine = create_async_engine(url or 'sqlite+aiosqlite:///' + self.temp.name + '/fixture.db')
        session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            if url:
                await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as db:
            for identity in ('a', 'b', 'c'):
                db.add(
                    File(
                        id=identity,
                        user_id='owner',
                        filename='paper.txt',
                        data={'content': 'old body'},
                        meta={},
                        created_at=1,
                    )
                )
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.temp.cleanup()

    async def enqueue(self, file='a', user='owner', source=None, knowledge=None):
        return await queue.enqueue(
            file_id=file,
            user_id=user,
            knowledge_id=knowledge,
            source_hash=source or file,
            config_hash='config',
            payload={},
        )

    async def test_same_scope_duplicate_is_not_executed_twice(self):
        identity = await self.enqueue(source='same')
        with self.assertRaises(queue.DuplicateIngestion) as exc:
            await self.enqueue('b', source='same')
        self.assertEqual(exc.exception.job_id, identity)
        async with sessions() as db:
            self.assertEqual((await db.get(File, 'a')).data['content'], 'old body')

    async def test_queue_budget_stops_new_work_without_erasing_content(self):
        with patch.dict(os.environ, {'INDEXING_MAX_QUEUED_PER_USER': '1'}):
            await self.enqueue()
            with self.assertRaisesRegex(ValueError, 'queue budget'):
                await self.enqueue('b')
        async with sessions() as db:
            self.assertEqual((await db.get(File, 'b')).data['content'], 'old body')

    async def test_150_received_uploads_survive_full_execution_queue_and_restart(self):
        with patch.dict(os.environ, {'INDEXING_MAX_QUEUED_PER_USER': '1', 'INDEXING_MAX_QUEUED': '1'}):
            first = await self.enqueue()  # The ordinary processing queue is full.
            identities = []
            for number in range(150):
                file_id = f'upload-{number}'
                async with sessions() as db:
                    db.add(File(id=file_id, user_id='batch', filename='paper.txt', data={}, meta={}))
                    await db.commit()
                identities.append(
                    await queue.enqueue(
                        file_id=file_id,
                        user_id='batch',
                        knowledge_id='kb',
                        source_hash=file_id,
                        config_hash='config',
                        payload={},
                        received=True,
                    )
                )
        # A new DB session (not a browser/in-memory queue) sees all receipts.
        async with sessions() as db:
            jobs = list(await db.scalars(select(Job).where(Job.user_id == 'batch')))
            self.assertEqual(len(jobs), 150)
            self.assertEqual({job.status for job in jobs}, {'waiting'})
            file = await db.get(File, 'upload-149')
            self.assertEqual(file.data['ingestion_job_id'], identities[-1])
        self.assertEqual((await queue.claim(0))[0], first)
        self.assertEqual((await queue.claim(1))[0], identities[0])
        self.assertIsNone(await queue.claim(1))

    async def test_received_backlog_does_not_expire_while_waiting(self):
        identity = await queue.enqueue(
            file_id='a',
            user_id='owner',
            knowledge_id='kb',
            source_hash='a',
            config_hash='config',
            payload={},
            received=True,
        )
        async with sessions() as db:
            job = await db.get(Job, identity)
            job.created_at = (int(time.time()) - 24 * 3600) * 1_000_000
            await db.commit()
        self.assertEqual(await queue.expire_jobs(job_id=identity), 0)
        self.assertEqual((await queue.claim(0))[0], identity)

    async def test_upload_receipts_restore_waiting_and_failed_without_document_body(self):
        source = ROOT / 'models/files.py'
        table = next(
            n for n in ast.parse(source.read_text()).body if isinstance(n, ast.ClassDef) and n.name == 'FilesTable'
        )
        method = next(
            n for n in table.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'get_pending_files_for_knowledge'
        )
        scope = dict(
            File=File,
            FileModelResponse=SimpleNamespace,
            AsyncSession=Any,
            get_async_db_context=sessions,
            select=select,
            log=logging.getLogger(__name__),
        )
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), scope)
        async with sessions() as db:
            for identity, status in [('a', 'pending'), ('b', 'failed'), ('c', 'completed')]:
                file = await db.get(File, identity)
                file.meta = {'data': {'knowledge_id': 'kb'}}
                file.data = {
                    'status': status,
                    'processing_status': 'waiting' if status == 'pending' else status,
                    'error': 'specific format error' if status == 'failed' else None,
                    'content': 'PRIVATE BODY' * 1000,
                }
            await db.commit()
        receipts = await scope['get_pending_files_for_knowledge'](None, 'kb')
        self.assertEqual({r.id for r in receipts}, {'a', 'b'})
        self.assertEqual(next(r.data['processing_status'] for r in receipts if r.id == 'a'), 'waiting')
        self.assertEqual(next(r.data['error'] for r in receipts if r.id == 'b'), 'specific format error')
        self.assertTrue(all('content' not in r.data for r in receipts))

    async def test_status_projection_does_not_select_document_body(self):
        from sqlalchemy import event

        statements = []
        source = ROOT / 'models/files.py'
        tree = ast.parse(source.read_text())
        table = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'FilesTable')
        method = next(
            n for n in table.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'get_file_processing_state'
        )
        scope = {'File': File, 'select': select, 'get_async_db_context': sessions}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), scope)
        async with sessions() as db:
            file = await db.get(File, 'a')
            file.data = {'content': 'x' * (10 * 1024**2), 'status': 'completed', 'retrieval_version': 1}
            await db.commit()

        def capture(*args):
            statements.append(args[2])

        event.listen(self.engine.sync_engine, 'before_cursor_execute', capture)
        result = await scope['get_file_processing_state'](None, 'a')
        self.assertEqual(result['status'], 'completed')
        self.assertNotIn('content', result)
        if self.engine.dialect.name == 'sqlite':
            self.assertTrue(all('JSON_EXTRACT' in sql for sql in statements), statements)
        else:
            self.assertTrue(all('->>' in sql for sql in statements), statements)
        for sql in statements:
            self.assertNotRegex(sql, r'(?:SELECT|,)\s+file\.data(?:\s|,)')

    async def test_windowing_preserves_boundary_overlap_without_whole_file_split(self):
        module = ModuleType('langchain_core.documents')
        module.Document = SimpleNamespace

        class Splitter:
            _chunk_overlap = 75

            def split_documents(self, docs):
                document = docs[0]
                self_max.append(len(document.page_content))
                return [
                    SimpleNamespace(page_content=document.page_content[i : i + 500], metadata={'start_index': i})
                    for i in range(0, len(document.page_content), 425)
                ]

        self_max = []
        text = '0123456789' * 10000
        with patch.dict(sys.modules, {'langchain_core.documents': module}):
            chunks = list(
                ingestion._chunks(
                    [SimpleNamespace(page_content=text, metadata={})],
                    Splitter(),
                    limits=ingestion.AdmissionLimits(),
                    body_stream=io.BytesIO(),
                )
            )
        self.assertLessEqual(max(self_max), 65536)
        for previous, current in zip(chunks, chunks[1:]):
            end = previous['metadata']['start_index'] + len(previous['text'])
            self.assertGreaterEqual(end - current['metadata']['start_index'], 75)

    async def test_four_web_workers_can_seed_new_defaults_concurrently(self):
        Config.configure(defaults={})
        await asyncio.gather(*(Config.seed_defaults({'ui.fixture': {'enabled': True}}) for _ in range(4)))
        async with sessions() as db:
            self.assertEqual((await db.get(Config, 'ui.fixture')).value, {'enabled': True})
            row = await db.get(Config, 'ui.fixture')
            row.value = 'admin-edited'
            await db.commit()
        await asyncio.gather(*(Config.seed_defaults({'ui.fixture': 'default'}) for _ in range(4)))
        async with sessions() as db:
            self.assertEqual((await db.get(Config, 'ui.fixture')).value, 'admin-edited')

    async def test_dedup_is_permission_scoped(self):
        await self.enqueue(source='same')
        await self.enqueue('b', user='another', source='same')
        await self.enqueue('c', source='same', knowledge='different-kb')

    async def test_two_slots_and_one_job_per_user(self):
        first = await self.enqueue()
        await self.enqueue('b')
        third = await self.enqueue('c', user='other')
        self.assertEqual((await queue.claim(0))[0], first)
        self.assertEqual((await queue.claim(1))[0], third)
        self.assertIsNone(await queue.claim(0))
        with self.assertRaises(ValueError):
            await queue.claim(2)

    async def test_expired_running_job_is_failed_not_retried(self):
        identity = await self.enqueue()
        _, token = await queue.claim(0)
        async with sessions() as db:
            job = await db.get(Job, identity)
            job.lease_until = 0
            await db.commit()
        await queue.expire_jobs()
        self.assertFalse(await queue.heartbeat(identity, token))
        self.assertIsNone(await queue.claim(1))
        async with sessions() as db:
            job = await db.get(Job, identity)
            self.assertEqual(job.status, 'failed')
            self.assertIsNone(job.active_key)
            self.assertEqual((await db.get(File, 'a')).data['content'], 'old body')

    async def test_old_lease_cannot_fail_or_publish_new_work(self):
        identity = await self.enqueue()
        _, token = await queue.claim(0)
        self.assertFalse(await queue.fail_job(identity, 'stale child', token='wrong'))
        with self.assertRaisesRegex(ValueError, 'lease'):
            await ingestion.publish_candidate(
                identity,
                'wrong',
                file_id='a',
                version=2,
                collection='candidate',
                content='new body',
                assets=[],
                chunk_count=1,
                space_id=None,
                scopes=[None],
            )
        async with sessions() as db:
            self.assertEqual((await db.get(File, 'a')).data['content'], 'old body')
            self.assertEqual((await db.get(Job, identity)).status, 'running')
        self.assertTrue(await queue.heartbeat(identity, token))

    async def seed_old_index(self):
        indexer = IndexingService()
        await indexer.index_file(
            file_id='a',
            file_version=1,
            collection_name='old-vectors',
            chunks=[{'id': 'old-id', 'text': 'old body', 'metadata': {'file_id': 'a'}}],
        )
        async with sessions() as db:
            file = await db.get(File, 'a')
            file.data = {**file.data, 'retrieval_version': 1}
            await db.commit()

    async def test_failed_candidate_does_not_hide_old_index(self):
        await self.seed_old_index()
        candidate = IndexingService(staged=True, staging_key='candidate-job')
        await candidate.accept_file_version('a', 2)
        await candidate.index_file(
            file_id='a',
            file_version=2,
            collection_name='candidate',
            chunks=[{'id': 'new-id', 'text': 'new body', 'metadata': {'file_id': 'a'}}],
        )
        async with sessions() as db:
            active = list(await db.scalars(select(Chunk).where(Chunk.is_active.is_(True))))
            self.assertEqual([row.text for row in active], ['old body'])
            self.assertEqual((await db.get(State, stable_id('file', file_id='a'))).file_version, 1)

    async def test_atomic_publication_switches_text_and_index_together(self):
        await self.seed_old_index()
        identity = await self.enqueue()
        _, token = await queue.claim(0)
        candidate = IndexingService(staged=True, staging_key=identity)
        space = await candidate.register_space(
            embedding_model='fixture',
            embedding_dim=2048,
            model_revision='fixture-revision',
            verified=True,
            encoding_config={
                'distance_metric': 'cosine',
                'query_prefix': None,
                'document_prefix': None,
                'normalization': 'fixture',
            },
        )
        for i in range(3):
            await candidate.index_file(
                file_id='a',
                file_version=2,
                collection_name='candidate',
                chunks=[{'id': 'new-' + str(i), 'text': 'section ' + str(i), 'metadata': {'file_id': 'a'}}],
                chunk_offset=i,
                space_id=space.id,
            )
        await ingestion.publish_candidate(
            identity,
            token,
            file_id='a',
            version=2,
            collection='candidate',
            content='new body',
            assets=[],
            chunk_count=3,
            space_id=space.id,
            scopes=[None],
        )
        async with sessions() as db:
            chunks = list(await db.scalars(select(Chunk).where(Chunk.is_active.is_(True))))
            self.assertEqual(sorted(row.chunk_index for row in chunks), [0, 1, 2])
            self.assertEqual((await db.get(File, 'a')).data['content'], 'new body')
            self.assertEqual((await db.get(Job, identity)).status, 'succeeded')
        self.assertFalse(await queue.fail_job(identity, 'late child failure', token=token))

    async def test_association_preserves_legacy_encoder_and_vector_identity(self):
        original = IndexingService()
        space = await original.register_space(embedding_model='legacy-encoder', space_id='old-space')
        async with sessions() as db:
            stored = await db.get(Space, space.id)
            stored.verification_status = 'legacy_read_only'
            await db.commit()
        await original.index_file(
            file_id='a',
            file_version=1,
            collection_name='original-collection',
            space_id=space.id,
            chunks=[{'id': 'existing-vector', 'text': 'old body', 'metadata': {'file_id': 'a'}}],
        )
        identity = await self.enqueue(knowledge='kb')
        _, token = await queue.claim(0)
        async with sessions() as db:
            job = await db.get(Job, identity)
            file = await db.get(File, 'a')
        staged = IndexingService(staged=True, staging_key=identity)
        self.assertTrue(await ingestion._reuse_for_knowledge(job, token, file, staged, 'different-current-encoder'))
        async with sessions() as db:
            target = await db.scalar(
                select(Mapping)
                .join(Evidence, Evidence.evidence_ref == Mapping.evidence_ref)
                .where(Evidence.knowledge_id == 'kb')
            )
            self.assertEqual(target.vector_space_id, 'old-space')
            self.assertEqual(target.vector_backend_id, 'existing-vector')
            self.assertEqual(target.vector_backend_collection, 'original-collection')
            self.assertEqual(target.embedding_status, 'quarantined')
            self.assertEqual((await db.get(Job, identity)).status, 'succeeded')

    async def stage_text(self):
        identity = await self.enqueue()
        _, token = await queue.claim(0)
        candidate = IndexingService(staged=True, staging_key=identity)
        space = await candidate.register_space(
            embedding_model='fixture',
            embedding_dim=2048,
            model_revision='fixture',
            verified=True,
            encoding_config={
                'distance_metric': 'cosine',
                'query_prefix': None,
                'document_prefix': None,
                'normalization': 'fixture',
            },
        )
        await candidate.accept_file_version('a', 2)
        await candidate.index_file(
            file_id='a',
            file_version=2,
            collection_name='candidate',
            chunks=[{'id': 'new', 'text': 'usable body', 'metadata': {'file_id': 'a'}}],
            space_id=space.id,
        )
        return identity, token, space.id

    async def test_unreadable_image_does_not_reject_usable_text(self):
        identity, token, space = await self.stage_text()
        await ingestion.publish_candidate(
            identity,
            token,
            file_id='a',
            version=2,
            collection='candidate',
            content='usable body',
            assets=[],
            skipped=[{'reason': 'unsupported_image_format', 'format': 'WMF', 'message': '不支持 WMF 图片格式'}],
            chunk_count=1,
            space_id=space,
            scopes=[None],
        )
        async with sessions() as db:
            file = await db.get(File, 'a')
            self.assertEqual(file.data['status'], 'completed')
            self.assertEqual(file.data['content'], 'usable body')
            self.assertEqual(file.data['retrieval_status']['text'], 'ready')
            self.assertEqual(file.data['retrieval_status']['images'], 'failed')
            self.assertIn('WMF', file.data['retrieval_status']['warnings'][0]['message'])
            self.assertEqual((await db.get(Job, identity)).status, 'succeeded')

    async def test_images_are_durably_queued_after_text_publication(self):
        identity, token, space = await self.stage_text()
        await ingestion.publish_candidate(
            identity,
            token,
            file_id='a',
            version=2,
            collection='candidate',
            content='usable body',
            assets=[{'sha256': 'image-hash', 'mime_type': 'image/png', 'storage_uri': '/private/image.png'}],
            chunk_count=1,
            space_id=space,
            scopes=[None],
        )
        async with sessions() as db:
            file = await db.get(File, 'a')
            self.assertEqual(file.data['status'], 'completed')
            self.assertEqual(file.data['retrieval_status']['images'], 'pending')
            child = await db.get(Job, file.data['image_ingestion_job_id'])
            self.assertEqual(child.status, 'waiting')
            self.assertTrue(child.payload['retry_images'])
            self.assertEqual(child.payload['source_version'], 2)
            self.assertEqual(file.data['ingestion_job_id'], identity)
            self.assertEqual((await db.get(Job, identity)).status, 'succeeded')

    async def test_auxiliary_image_failure_preserves_completed_file(self):
        async with sessions() as db:
            file = await db.get(File, 'a')
            file.data = {
                'status': 'completed',
                'content': 'usable body',
                'retrieval_version': 1,
                'ingestion_job_id': 'published-text',
                'retrieval_status': {'version': 1, 'text': 'ready', 'images': 'pending'},
            }
            await db.commit()
        identity = await queue.enqueue(
            file_id='a',
            user_id='owner',
            knowledge_id=None,
            source_hash='source',
            config_hash='config',
            payload={'retry_images': True, 'source_version': 1},
        )
        _, token = await queue.claim(0)
        await queue.fail_job(identity, '图片编码超时，正文仍可使用', token=token)
        async with sessions() as db:
            file = await db.get(File, 'a')
            self.assertEqual(file.data['status'], 'completed')
            self.assertEqual(file.data['ingestion_job_id'], 'published-text')
            self.assertEqual(file.data['retrieval_status']['text'], 'ready')
            self.assertEqual(file.data['retrieval_status']['images'], 'failed')
            self.assertEqual((await db.get(Job, identity)).status, 'failed')

    async def test_auxiliary_image_expiry_preserves_text_and_version(self):
        async with sessions() as db:
            file = await db.get(File, 'a')
            file.data = {
                'status': 'completed',
                'content': 'published body',
                'retrieval_version': 7,
                'retrieval_status': {'version': 7, 'text': 'ready', 'images': 'pending'},
            }
            await db.commit()
        identity = await queue.enqueue(
            file_id='a',
            user_id='owner',
            knowledge_id=None,
            source_hash='source',
            config_hash='config',
            payload={'retry_images': True, 'source_version': 7},
        )
        await queue.claim(0)
        async with sessions() as db:
            job = await db.get(Job, identity)
            job.lease_until = int(time.time()) - 1
            await db.commit()
        self.assertEqual(await queue.expire_jobs(job_id=identity), 1)
        async with sessions() as db:
            data = (await db.get(File, 'a')).data
            self.assertEqual(data['status'], 'completed')
            self.assertEqual(data['content'], 'published body')
            self.assertEqual(data['retrieval_status']['version'], 7)
            self.assertEqual(data['retrieval_status']['text'], 'ready')
            self.assertEqual(data['retrieval_status']['images'], 'failed')

    async def test_old_image_job_failure_cannot_overwrite_new_image_job(self):
        async with sessions() as db:
            file = await db.get(File, 'a')
            file.data = {
                'status': 'completed',
                'image_ingestion_job_id': 'new',
                'retrieval_status': {'text': 'ready', 'images': 'ready'},
            }
            db.add(
                Job(
                    job_id='old',
                    file_id='a',
                    user_id='owner',
                    source_hash='s',
                    config_hash='c',
                    status='running',
                    payload={'retry_images': True},
                    lease_token='token',
                    lease_until=int(time.time()) + 45,
                    created_at=time.time_ns() // 1000,
                )
            )
            await db.commit()
        await queue.fail_job('old', 'old failure', token='token')
        async with sessions() as db:
            data = (await db.get(File, 'a')).data
            self.assertEqual(data['retrieval_status']['images'], 'ready')
            self.assertEqual(data['status'], 'completed')

    async def test_incomplete_image_service_result_is_not_reported_ready(self):
        data = {
            'status': 'completed',
            'content': 'published body',
            'retrieval_version': 1,
            'document_image_assets': [
                {'sha256': 'asset', 'storage_uri': '/unused/image.png', 'mime_type': 'image/png'}
            ],
            'retrieval_status': {'text': 'ready', 'images': 'pending'},
        }
        async with sessions() as db:
            file = await db.get(File, 'a')
            file.data = data
            await db.commit()
        identity = await queue.enqueue(
            file_id='a',
            user_id='owner',
            knowledge_id=None,
            source_hash='s',
            config_hash='c',
            payload={'retry_images': True, 'source_version': 1},
        )
        _, token = await queue.claim(0)
        documents = ModuleType('open_webui.retrieval.document_image_assets')
        documents.ImageAssetStore, documents.MAX_IMAGE_BYTES = Mock(), 20 * 1024**2
        knowledge = sys.modules['open_webui.models.knowledge']
        with (
            patch.object(
                knowledge,
                'Knowledges',
                SimpleNamespace(get_knowledges_by_file_id=AsyncMock(return_value=[])),
                create=True,
            ),
            patch.dict(sys.modules, {documents.__name__: documents}),
        ):
            await ingestion._retry_images(
                SimpleNamespace(job_id=identity, payload={'source_version': 1}),
                token,
                SimpleNamespace(id='a', filename='image.png', data=data, meta={}),
                SimpleNamespace(index_assets=AsyncMock(return_value={'status': 'indexing', 'indexed': 0})),
                'space',
            )
        async with sessions() as db:
            data = (await db.get(File, 'a')).data
            self.assertEqual(data['status'], 'completed')
            self.assertEqual(data['retrieval_status']['text'], 'ready')
            self.assertEqual(data['retrieval_status']['images'], 'failed')
            self.assertTrue(data['retrieval_status']['image_retryable'])


if __name__ == '__main__':
    unittest.main()
