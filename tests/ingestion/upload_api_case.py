"""Real multipart upload route + receipt queue SQL, with isolated storage/auth.

Uses production upload/submit/enqueue code. No worker is started: every response
must finish while processing is unavailable and the interactive queue is full.
"""

import ast
import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock, patch

import sql_cases as fixture
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    FastAPI,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi import (
    File as MultipartFile,
)
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel
from sqlalchemy import func, select


async def main():
    test = fixture.QueueSQLTests()
    await test.asyncSetUp()
    try:
        with tempfile.TemporaryDirectory() as upload_dir:

            async def config_get(key, default=None):
                return [] if key == 'audio.stt.supported_content_types' else default

            config = ModuleType('open_webui.config')
            config.Config = SimpleNamespace(get=config_get, get_many=AsyncMock(return_value={'rag.chunk_size': 100}))
            config.UPLOAD_DIR = upload_dir
            retrieval = ModuleType('open_webui.routers.retrieval')
            retrieval.RETRIEVAL_CONFIG_KEYS = {'size': 'rag.chunk_size'}
            retrieval._validate_collection_access = AsyncMock()
            access = ModuleType('open_webui.utils.access_control.files')
            access.has_access_to_file = AsyncMock(return_value=False)

            class FileForm(BaseModel):
                id: str
                filename: str
                path: str
                data: dict
                meta: dict

            class Repository:
                async def insert_new_file(self, user_id, form, db=None):
                    record = form.model_dump()
                    async with fixture.sessions(db) as session:
                        row = fixture.File(
                            **record, user_id=user_id, created_at=int(time.time()), updated_at=int(time.time())
                        )
                        session.add(row)
                        await session.commit()
                    result = {**record, 'user_id': user_id, 'created_at': int(time.time())}
                    return SimpleNamespace(**result, model_dump=lambda: result)

            repository = Repository()
            model_tree = ast.parse((fixture.ROOT / 'models/files.py').read_text())
            table = next(n for n in model_tree.body if isinstance(n, ast.ClassDef) and n.name == 'FilesTable')
            method = next(
                n for n in table.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'get_file_processing_state'
            )
            projection = dict(File=fixture.File, select=select, get_async_db_context=fixture.sessions)
            exec(compile(ast.Module(body=[method], type_ignores=[]), '<production-status>', 'exec'), projection)
            repository.get_file_processing_state = lambda identity: projection['get_file_processing_state'](
                None, identity
            )

            def store(stream, name, tags, return_contents):
                path = Path(upload_dir) / name
                with path.open('wb') as output:
                    shutil.copyfileobj(stream, output)
                return None, str(path)

            user = SimpleNamespace(id='batch-user', email='synthetic@example.invalid', name='Fixture', role='user')

            async def verified_user():
                return user

            knowledges = SimpleNamespace(get_knowledge_by_id=AsyncMock(return_value=SimpleNamespace(user_id=user.id)))
            scope = dict(
                APIRouter=APIRouter,
                BackgroundTasks=BackgroundTasks,
                Depends=Depends,
                File=MultipartFile,
                Form=Form,
                HTTPException=HTTPException,
                Query=Query,
                Request=Request,
                UploadFile=UploadFile,
                Optional=Optional,
                AsyncSession=object,
                status=status,
                os=os,
                uuid=uuid,
                errno=__import__('errno'),
                asyncio=asyncio,
                JSONCodec=json,
                FileForm=FileForm,
                FileModelResponse=dict,
                Files=repository,
                Knowledges=knowledges,
                AccessGrants=SimpleNamespace(has_access=AsyncMock(return_value=False)),
                Config=config.Config,
                Storage=SimpleNamespace(upload_file=store),
                strict_match_mime_type=lambda *args: False,
                validate_chat_pdf_upload=lambda *args: None,
                should_defer_pdf_processing=lambda *args, **kwargs: False,
                get_verified_user=verified_user,
                get_async_db_context=fixture.sessions,
                get_async_session=lambda: None,
                log=logging.getLogger('upload-fixture'),
                ProcessFileForm=lambda **kwargs: SimpleNamespace(content=None, **kwargs),
                publish_event=AsyncMock(),
                EVENTS=SimpleNamespace(FILE_UPLOADED='uploaded'),
                ERROR_MESSAGES=SimpleNamespace(DEFAULT=lambda message: message),
                router=APIRouter(),
            )
            tree = ast.parse((fixture.ROOT / 'routers/files.py').read_text())
            nodes = [
                n
                for n in tree.body
                if isinstance(n, ast.AsyncFunctionDef)
                and n.name in ('upload_file', 'upload_file_handler', 'validate_knowledge_upload_target')
            ]
            exec(compile(ast.Module(body=nodes, type_ignores=[]), '<production-upload>', 'exec'), scope)
            app = FastAPI()
            app.state.MODELS = {}
            app.include_router(scope['router'], prefix='/files')
            modules = {module.__name__: module for module in (config, retrieval, access)}
            with (
                patch.dict(sys.modules, modules),
                patch.dict(
                    os.environ,
                    {
                        'INDEXING_MAX_QUEUED': '1',
                        'INDEXING_MAX_QUEUED_PER_USER': '1',
                        'INDEXING_UPLOAD_STORAGE_MIN_FREE_BYTES': str(1024**2),
                    },
                ),
            ):
                await test.enqueue()  # Interactive execution queue already full.
                async with AsyncClient(transport=ASGITransport(app=app), base_url='http://fixture') as client:
                    receipts = []
                    for index in range(150):
                        response = await client.post(
                            '/files/',
                            files={'file': (f'paper-{index}.txt', f'body {index}'.encode(), 'text/plain')},
                            data={'metadata': json.dumps({'knowledge_id': 'kb'})},
                        )
                        assert response.status_code == 200, response.text
                        receipt = response.json()
                        assert receipt['id'] and receipt['ingestion_job_id'], receipt
                        assert receipt['data']['processing_status'] == 'waiting', receipt
                        receipts.append(receipt)
                    # Rejected targets must not leave an additional stored original.
                    knowledges.get_knowledge_by_id.return_value = None
                    response = await client.post(
                        '/files/',
                        files={'file': ('stale.txt', b'stale target', 'text/plain')},
                        data={'metadata': json.dumps({'knowledge_id': 'deleted'})},
                    )
                    assert response.status_code == 404, response.text
                    assert '知识库' in response.json()['detail']
                async with fixture.sessions() as db:
                    saved = await db.scalar(
                        select(func.count()).select_from(fixture.File).where(fixture.File.user_id == user.id)
                    )
                    jobs = await db.scalar(
                        select(func.count())
                        .select_from(fixture.Job)
                        .where(fixture.Job.user_id == user.id, fixture.Job.status == 'waiting')
                    )
                assert saved == jobs == len(list(Path(upload_dir).iterdir())) == 150
                assert len({r['id'] for r in receipts}) == 150
                print(
                    'BULK_MULTIPART_API_PASS received=150 durable_waiting=150 workers_started=0 stale_target_stored=0'
                )
    finally:
        await test.asyncTearDown()


if __name__ == '__main__':
    asyncio.run(main())
