from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import os
import statistics
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta


BASE_URL = os.environ.get('BASE_URL', 'http://127.0.0.1:18085').rstrip('/')
ADMIN_USER_ID = os.environ.get('ADMIN_USER_ID', 'b6826286-1251-4576-b3a0-e109ff085a61')
MODEL_ID = os.environ.get('MODEL_ID', 'bifrostapi.Cliproxy/gpt-5.5')


def docker_token() -> str:
    script = (
        'from datetime import timedelta\n'
        'from open_webui.utils.auth import create_token\n'
        f'print(create_token({{"id": "{ADMIN_USER_ID}"}}, expires_delta=timedelta(hours=2)))\n'
    )
    result = subprocess.run(
        ['docker', 'exec', '-i', 'open-webui-pr7', 'python', '-'],
        input=script,
        text=True,
        capture_output=True,
        check=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()][-1]


TOKEN = docker_token()
HEADERS = {'Authorization': f'Bearer {TOKEN}'}


def request_once(path: str) -> dict:
    started = time.perf_counter()
    request = urllib.request.Request(BASE_URL + path, headers=HEADERS, method='GET')
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
            return {
                'status': response.status,
                'bytes': len(body),
                'elapsed_ms': round((time.perf_counter() - started) * 1000, 2),
            }
    except Exception as exc:
        return {
            'status': getattr(exc, 'code', None),
            'bytes': 0,
            'elapsed_ms': round((time.perf_counter() - started) * 1000, 2),
            'error': type(exc).__name__,
        }


def sse_once() -> dict:
    started = time.perf_counter()
    body = json.dumps(
        {
            'model': MODEL_ID,
            'messages': [{'role': 'user', 'content': 'Reply with exactly OK.'}],
            'stream': True,
            'temperature': 0,
            'max_tokens': 8,
        }
    ).encode()
    request = urllib.request.Request(
        BASE_URL + '/api/chat/completions',
        data=body,
        headers={**HEADERS, 'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data_lines = 0
            total_bytes = 0
            done = False
            for raw_line in response:
                total_bytes += len(raw_line)
                if raw_line.startswith(b'data:'):
                    data_lines += 1
                    if raw_line.strip() == b'data: [DONE]':
                        done = True
            return {
                'status': response.status,
                'bytes': total_bytes,
                'sse_data_lines': data_lines,
                'done': done,
                'elapsed_ms': round((time.perf_counter() - started) * 1000, 2),
            }
    except Exception as exc:
        return {
            'status': getattr(exc, 'code', None),
            'bytes': 0,
            'sse_data_lines': 0,
            'done': False,
            'elapsed_ms': round((time.perf_counter() - started) * 1000, 2),
            'error': type(exc).__name__,
        }


def summarize(results: list[dict]) -> dict:
    elapsed = sorted(item['elapsed_ms'] for item in results)
    successes = [item for item in results if item.get('status') == 200 and 'error' not in item]
    p95_index = min(len(elapsed) - 1, max(0, int(len(elapsed) * 0.95) - 1))
    return {
        'requests': len(results),
        'successes': len(successes),
        'errors': len(results) - len(successes),
        'statuses': sorted({item.get('status') for item in results}),
        'p50_ms': round(statistics.median(elapsed), 2),
        'p95_ms': round(elapsed[p95_index], 2),
        'max_ms': round(max(elapsed), 2),
        'sse_done': sum(1 for item in results if item.get('done') is True),
        'sse_data_lines': [item.get('sse_data_lines', 0) for item in results],
    }


def run_batch(name: str, worker, concurrency: int) -> dict:
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        results = list(executor.map(lambda _: worker(), range(concurrency)))
    return {
        'name': name,
        'concurrency': concurrency,
        'started_at': started,
        'summary': summarize(results),
    }


def main() -> None:
    batches = [
        run_batch('models', lambda: request_once('/api/models'), 8),
        run_batch('knowledge_list', lambda: request_once('/api/v1/knowledge/?page=1'), 4),
        run_batch(
            'knowledge_search',
            lambda: request_once('/api/v1/knowledge/search?query=acceptance&source=local&page=1'),
            4,
        ),
        run_batch('files_list', lambda: request_once('/api/v1/files/?content=false'), 4),
        run_batch('files_count', lambda: request_once('/api/v1/files/count'), 4),
        run_batch('chat_sse', sse_once, 2),
    ]
    print(json.dumps({'base_url': BASE_URL, 'model_id': MODEL_ID, 'batches': batches}, indent=2))


if __name__ == '__main__':
    main()
