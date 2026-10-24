"""Synthetic-only runtime for isolated ingestion acceptance; never use Live config."""

import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time

FIXTURE = Path('/fixture')


async def seed():
    from sqlalchemy import select
    from open_webui.config import Config, seed_registered_defaults
    from open_webui.models.auths import Auths, Auth
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.knowledge import Knowledge, KnowledgeForm, Knowledges
    from open_webui.retrieval.indexing import IndexingService
    from open_webui.utils.auth import create_token

    await seed_registered_defaults()
    base = 'http://owui-idx-fixture-mock:8090/v1'
    await Config.upsert(
        {
            'models.base_models_cache': False,
            'ui.enable_signup': False,
            'rag.embedding_engine': 'openai',
            'rag.embedding_model': 'fixture-2048',
            'rag.openai.api_base_url': base,
            'rag.openai.api_key': 'fixture-only',
            'rag.enable_async_embedding': True,
            'rag.embedding_concurrent_requests': 1,
            'rag.embedding_batch_size': 32,
            'rag.content_extraction_engine': '',
            'rag.enable_hybrid_search': False,
            'rag.bypass_embedding_and_retrieval': False,
            'rag.chunk_size': 500,
            'rag.chunk_overlap': 75,
            'rag.text_splitter': 'character',
            'rag.enable_markdown_header_text_splitter': True,
            'rag.chunk_min_size_target': 100,
            'rag.extract_document_image_assets': True,
            'rag.pdf_image_asset_source': 'local',
            'rag.file.allowed_extensions': [],
            'rag.file.max_size': None,
            'openai.api_base_urls': [],
            'openai.api_keys': [],
            'terminal_server.connections': [],
            'tool_server.connections': [],
            'ui.banners': [],
        }
    )
    space = await IndexingService().register_space(
        embedding_model='fixture-2048',
        embedding_dim=2048,
        model_revision='synthetic-acceptance-v1',
        verified=True,
        supports_images=True,
        encoding_config={
            'distance_metric': 'cosine',
            'query_prefix': None,
            'document_prefix': None,
            'normalization': 'model-native',
            'image_preprocessing': 'synthetic-v1',
            'text_api_base_url': base,
        },
    )
    await Config.upsert({'rag.evidence_vector_space_id': space.id})
    credentials = []
    for index in range(2):
        user = await Auths.insert_new_auth(
            email=f'indexing-{index}@fixture.invalid',
            password='not-a-login-password',
            name='Isolated fixture',
            role='admin',
        )
        async with get_async_db_context() as db:
            auth = await db.get(Auth, user.id)
            token = create_token({'id': user.id, 'session_stamp': auth.session_stamp, 'auth_method': 'password'})
        kb = await Knowledges.insert_new_knowledge(
            user.id, KnowledgeForm(name=f'Fixture KB {index}', description='Synthetic only')
        )
        credentials.append({'id': user.id, 'token': token, 'knowledge_id': kb.id})
    (FIXTURE / 'credentials.json').write_text(json.dumps(credentials))
    (FIXTURE / 'credentials.json').chmod(0o600)
    print('FIXTURE_SEEDED', flush=True)


def mock():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        calls = 0
        image_calls = 0
        maximum_batch = 0

        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        'status': 'fixture-only',
                        'calls': Handler.calls,
                        'image_calls': Handler.image_calls,
                        'maximum_batch': Handler.maximum_batch,
                    }
                ).encode()
            )

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            Handler.calls += 1
            Handler.image_calls += int(bool(payload.get('messages')))
            inputs = payload.get('input') or ['image fixture']
            if isinstance(inputs, str):
                inputs = [inputs]
            Handler.maximum_batch = max(Handler.maximum_batch, len(inputs))
            if any('FIXTURE_OOM_RESPONSE' in text for text in inputs):
                # Deliberately malformed encoder response, streamed with fixed
                # mock memory. Only the isolated job cgroup receives it.
                head, tail = b'{"data":[{"embedding":[', b'0.1]}]}'
                block, repeats = b'0.1,' * 262144, 1400
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(head) + len(block) * repeats + len(tail)))
                self.end_headers()
                try:
                    self.wfile.write(head)
                    for _ in range(repeats):
                        self.wfile.write(block)
                    self.wfile.write(tail)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            if any('FIXTURE_SLOW' in text for text in inputs):
                time.sleep(90)
            if any('FIXTURE_BAD_DIM' in text for text in inputs):
                dimensions = 4
            else:
                dimensions = 2048
            rows = []
            for index, text in enumerate(inputs):
                value = hashlib.sha256(text.encode()).digest()[0] / 255
                rows.append(
                    {'object': 'embedding', 'index': index, 'embedding': [value + 0.01] + [0.01] * (dimensions - 1)}
                )
            body = json.dumps({'object': 'list', 'model': 'fixture-2048', 'data': rows}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass

    print('FIXTURE_MOCK_READY', flush=True)
    ThreadingHTTPServer(('0.0.0.0', 8090), Handler).serve_forever()


def probe():
    import requests

    credentials = json.loads((FIXTURE / 'credentials.json').read_text())
    origin = 'http://owui-idx-fixture-web:8080'
    receipts = []

    def upload(content, *, name='paper.txt', owner=0, knowledge=False):
        import mimetypes

        user = credentials[owner]
        metadata = {'upload_context': 'knowledge', 'knowledge_id': user['knowledge_id']} if knowledge else {}
        response = requests.post(
            origin + '/api/v1/files/',
            headers={'Authorization': 'Bearer ' + user['token']},
            files={'file': (name, content, mimetypes.guess_type(name)[0] or 'application/octet-stream')},
            data={'metadata': json.dumps(metadata)},
            timeout=30,
        )
        if not response.ok:
            raise AssertionError(f'fixture upload failed: {response.status_code} {response.text[:300]}')
        return response.json()

    def wait(item, *, owner=0, expected='succeeded', timeout=180):
        identity = item['ingestion_job_id']
        deadline = time.monotonic() + timeout
        headers = {'Authorization': 'Bearer ' + credentials[owner]['token']}
        while time.monotonic() < deadline:
            response = requests.get(origin + '/api/v1/retrieval/process/jobs/' + identity, headers=headers, timeout=10)
            response.raise_for_status()
            result = response.json()
            if result['status'] in ('succeeded', 'failed', 'rejected', 'cancelled'):
                assert result['status'] == expected, result
                receipts.append({'case': name_case[0], 'status': result['status'], 'result': result.get('result')})
                print('FIXTURE_CASE', name_case[0], result['status'], flush=True)
                return result
            time.sleep(1)
        raise AssertionError('fixture task exceeded acceptance time limit')

    name_case = ['normal knowledge text']
    body = (
        'The experiment compares physical measurements and independent observations. '
        'Document evidence is retained with stable source identity.\n'
    ) * 80
    item = upload(body.encode(), knowledge=True)
    good = wait(item)
    headers = {'Authorization': 'Bearer ' + credentials[0]['token']}
    response = requests.post(
        origin + '/api/v1/retrieval/query/doc',
        headers=headers,
        json={'collection_name': credentials[0]['knowledge_id'], 'query': 'physical measurements', 'k': 3},
        timeout=30,
    )
    response.raise_for_status()
    assert response.json().get('documents', [[]])[0], response.text[:300]
    print('FIXTURE_QUERY_OK', flush=True)
    response = requests.post(
        origin + '/api/v1/retrieval/query/collection',
        headers=headers,
        json={
            'collection_names': [credentials[0]['knowledge_id'], 'file-' + item['id']],
            'query': 'physical measurements',
            'k': 3,
        },
        timeout=30,
    )
    response.raise_for_status()
    assert response.json().get('documents', [[]])[0], response.text[:300]
    print('FIXTURE_MULTI_COLLECTION_QUERY_OK', flush=True)

    name_case[0] = 'image text and image publication together'
    from PIL import Image

    picture = io.BytesIO()
    Image.new('RGB', (32, 32), 'red').save(picture, format='PNG')
    before = requests.get('http://owui-idx-fixture-mock:8090/stats', timeout=5).json()['image_calls']
    image = upload(picture.getvalue(), name='figure.png', owner=1, knowledge=True)
    wait(image, owner=1)
    after = requests.get('http://owui-idx-fixture-mock:8090/stats', timeout=5).json()['image_calls']
    assert after - before == 1, 'same candidate image re-embedded per knowledge scope'
    name_case[0] = 'image retry stays in isolated queue'
    response = requests.post(
        origin + '/api/v1/files/' + image['id'] + '/retrieval/retry-images',
        headers={'Authorization': 'Bearer ' + credentials[1]['token']},
        timeout=20,
    )
    response.raise_for_status()
    retry = wait(response.json(), owner=1)
    assert retry['result']['text_reembedded'] is False
    assert retry['result']['file_version'] == 1

    state = requests.get(
        origin + '/api/v1/files/' + image['id'] + '/process/status',
        headers={'Authorization': 'Bearer ' + credentials[1]['token']},
        timeout=10,
    )
    state.raise_for_status()
    assert state.json()['status'] == 'completed'
    print('FIXTURE_STATUS_PROJECTION_OK', flush=True)

    name_case[0] = 'rejected numeric matrix'
    # Declared size + rectangular values; the small compressed transmission is
    # not used here: the actual stored text exceeds the 8 MiB sample threshold.
    matrix = b'Matrix Size (270,270,300)\nnbVal=21870000\n' + (b'0.01 ' * 270 + b'\n') * 7000
    rejected = upload(matrix, owner=1)
    wait(rejected, owner=1, expected='rejected')

    name_case[0] = 'failed candidate preserves original'
    response = requests.post(
        origin + '/api/v1/files/' + item['id'] + '/data/content/update',
        headers=headers,
        json={'content': 'FIXTURE_BAD_DIM New body that must not replace the old usable document.'},
        timeout=90,
    )
    assert not response.ok, response.text[:200]
    current = requests.get(origin + '/api/v1/files/' + item['id'], headers=headers, timeout=20)
    current.raise_for_status()
    assert 'physical measurements' in current.json()['data']['content']
    receipts.append({'case': name_case[0], 'status': 'old body retained'})
    print('FIXTURE_OLD_VERSION_RETAINED', flush=True)

    name_case[0] = 'successful edit publishes every knowledge scope once'
    calls = requests.get('http://owui-idx-fixture-mock:8090/stats', timeout=5).json()['calls']
    response = requests.post(
        origin + '/api/v1/files/' + item['id'] + '/data/content/update',
        headers=headers,
        json={'content': 'Updated physical measurements: the published replacement is independently verified.'},
        timeout=90,
    )
    response.raise_for_status()
    after = requests.get('http://owui-idx-fixture-mock:8090/stats', timeout=5).json()['calls']
    assert after - calls == 1, 'content edit scheduled duplicate embedding passes'
    response = requests.post(
        origin + '/api/v1/retrieval/query/doc',
        headers=headers,
        json={'collection_name': credentials[0]['knowledge_id'], 'query': 'physical measurements', 'k': 3},
        timeout=30,
    )
    response.raise_for_status()
    assert any('published replacement' in text for text in response.json()['documents'][0])
    # The retrieval query itself encodes its query text once.
    after_query = requests.get('http://owui-idx-fixture-mock:8090/stats', timeout=5).json()['calls']
    response = requests.post(
        origin + '/api/v1/knowledge/' + credentials[0]['knowledge_id'] + '/file/update',
        headers=headers,
        json={'file_id': item['id']},
        timeout=90,
    )
    response.raise_for_status()
    assert requests.get('http://owui-idx-fixture-mock:8090/stats', timeout=5).json()['calls'] == after_query
    print('FIXTURE_EDIT_AND_ASSOCIATION_OK', flush=True)

    name_case[0] = 'two independent jobs, one cancelled'
    slow = upload(b'FIXTURE_SLOW Wait for cancellation without publishing.', owner=0)
    fast = upload(b'Independent second worker remains usable during another task.', owner=1)
    wait(fast, owner=1)
    response = requests.post(
        origin + '/api/v1/retrieval/process/jobs/' + slow['ingestion_job_id'] + '/cancel', headers=headers, timeout=10
    )
    response.raise_for_status()
    wait(slow, expected='cancelled')
    (FIXTURE / 'probe-results.json').write_text(json.dumps(receipts, indent=2))
    print('FIXTURE_PROBES_PASSED', flush=True)


def fault(case):
    """Real production job paths with bounded synthetic fault inputs."""
    import requests

    credentials = json.loads((FIXTURE / 'credentials.json').read_text())
    origin = 'http://owui-idx-fixture-web:8080'
    headers = [{'Authorization': 'Bearer ' + user['token']} for user in credentials]

    def upload(stream, name, owner=0):
        response = requests.post(
            origin + '/api/v1/files/',
            headers=headers[owner],
            files={'file': (name, stream, 'application/octet-stream')},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def status(item, owner=0):
        response = requests.get(
            origin + '/api/v1/retrieval/process/jobs/' + item['ingestion_job_id'], headers=headers[owner], timeout=10
        )
        response.raise_for_status()
        return response.json()

    def wait(item, owner=0, expected='failed'):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            try:
                result = status(item, owner)
                if result['status'] in ('succeeded', 'failed', 'cancelled', 'rejected'):
                    assert result['status'] == expected, result
                    return result
            except requests.RequestException:
                if case != 'db':
                    raise
            time.sleep(1)
        raise AssertionError('fault task remained pending/running beyond deadline')

    started = time.monotonic()
    if case == 'oom':
        item = upload(io.BytesIO(b'FIXTURE_OOM_RESPONSE Synthetic malformed encoder output.'), 'oom.txt')
        peer = upload(
            io.BytesIO(b'Independent healthy worker during synthetic parser resource exhaustion.'),
            'healthy-peer.txt',
            owner=1,
        )
        wait(peer, owner=1, expected='succeeded')
        result = wait(item)
    else:
        body = (
            (b'Physical measurements are compared with independent observations.\n' * 70000)
            if case == 'temp'
            else b'FIXTURE_SLOW synthetic delayed encoder'
        )
        item = upload(io.BytesIO(body), case + '.txt')
        if case == 'db':
            deadline = time.monotonic() + 60
            while status(item)['status'] != 'running':
                if time.monotonic() > deadline:
                    raise AssertionError('DB fault task did not start')
                time.sleep(1)
            (FIXTURE / 'db-fault-ready').write_text(item['ingestion_job_id'])
        result = wait(item)
    assert requests.get(origin + '/health', timeout=5).ok
    query = requests.post(
        origin + '/api/v1/retrieval/query/doc',
        headers=headers[0],
        json={'collection_name': credentials[0]['knowledge_id'], 'query': 'physical measurements', 'k': 3},
        timeout=30,
    )
    query.raise_for_status()
    assert query.json().get('documents', [[]])[0], 'old published knowledge became unreadable'
    receipt = {
        'case': case,
        'elapsed_seconds': round(time.monotonic() - started, 2),
        'job': result,
        'old_knowledge_readable': True,
        'web_health': True,
    }
    (FIXTURE / ('fault-' + case + '.json')).write_text(json.dumps(receipt, indent=2))
    print('FIXTURE_FAULT_PASSED', case, result['status'], result.get('error'), flush=True)


def memory_probe():
    import requests

    credentials = json.loads((FIXTURE / 'credentials.json').read_text())
    origin = 'http://owui-idx-fixture-web:8080'
    headers = {'Authorization': 'Bearer ' + credentials[0]['token']}
    receipts = []
    for paragraphs in (150, 2400):
        # Unique 450-character sections avoid citation/hash dedup obscuring the
        # actual number of generated vector rows.
        body = '\n\n'.join(
            (
                f'Section {index}: Evidence and independently observed physical measurements. '
                + 'The document compares observations without inferring unsupported causes. ' * 6
            )
            for index in range(paragraphs)
        ).encode()
        response = requests.post(
            origin + '/api/v1/files/',
            headers=headers,
            files={'file': ('memory-fixture.txt', body, 'text/plain')},
            timeout=30,
        )
        response.raise_for_status()
        item = response.json()
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            status = requests.get(
                origin + '/api/v1/retrieval/process/jobs/' + item['ingestion_job_id'], headers=headers, timeout=10
            )
            status.raise_for_status()
            job = status.json()
            if job['status'] == 'succeeded':
                receipts.append(job['result'])
                print(
                    'FIXTURE_MEMORY',
                    paragraphs,
                    job['result']['text_chunks'],
                    job['result']['worker_peak_rss_bytes'],
                    flush=True,
                )
                break
            assert job['status'] not in ('failed', 'rejected', 'cancelled'), job
            time.sleep(1)
        else:
            raise AssertionError('memory probe did not finish')
    assert receipts[1]['text_chunks'] >= receipts[0]['text_chunks'] * 10
    assert receipts[1]['worker_peak_rss_bytes'] - receipts[0]['worker_peak_rss_bytes'] < 96 * 1024**2, receipts
    (FIXTURE / 'memory-probe.json').write_text(json.dumps(receipts, indent=2))
    print('FIXTURE_BOUNDED_MEMORY_PASSED', flush=True)


if __name__ == '__main__':
    if sys.argv[1] == 'seed':
        asyncio.run(seed())
    elif sys.argv[1] == 'mock':
        mock()
    elif sys.argv[1] == 'probe':
        probe()
    elif sys.argv[1] == 'fault':
        fault(sys.argv[2])
    elif sys.argv[1] == 'memory':
        memory_probe()
