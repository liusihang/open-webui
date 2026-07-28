from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import subprocess
import time
import uuid
from typing import Any

from remote_native_phase_order_e2e import MODEL_ID, request_json, shell_output

OUT_DIR = pathlib.Path(
    os.environ.get(
        'OUT_DIR',
        '/home/aiserver/staging/openwebui-pr7-eea11194ed-test',
    )
)


def tool_source(marker: str) -> str:
    return f'''
class Tools:
    def cancellation_probe(self, marker: str) -> dict:
        """Return the cancellation fixture marker.

        :param marker: The exact marker from the user request.
        """
        return {{"status": "unexpected_tool_execution", "marker": marker, "expected": {marker!r}}}
'''.strip()


def runtime_status(run_id: str) -> dict[str, Any]:
    script = r"""
import json
import os
import sys
import urllib.request

run_id = sys.argv[1]
base_url = os.environ["AGENT_RUNTIME_BASE_URL"].rstrip("/")
token = os.environ["AGENT_RUNTIME_SERVICE_TOKEN"]
request = urllib.request.Request(
    f"{base_url}/v1/openwebui/runs/{run_id}/status",
    method="GET",
    headers={"Authorization": f"Bearer {token}"},
)
with urllib.request.urlopen(request, timeout=10) as response:
    print(response.read().decode("utf-8"))
"""
    proc = subprocess.run(
        ['docker', 'exec', '-i', 'open-webui-pr7', 'python', '-', run_id],
        input=script,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(proc.stdout)


def event_types(run_id: str) -> list[str]:
    payload = request_json('GET', f'/api/agent/runs/{run_id}/events/list', timeout=30)
    return [
        str(event.get('event_type'))
        for event in payload.get('events') or []
        if isinstance(event, dict)
    ]


def main() -> int:  # noqa: C901 - deployment acceptance keeps the audit flow linear
    timestamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    marker = f'CANCEL-E2E-{timestamp}-{uuid.uuid4().hex[:8]}'
    tool_id = f"e2e_cancel_{timestamp.replace('-', '_')}".lower()
    out_path = OUT_DIR / f'e2e-agentmode-cancellation-{timestamp}.json'
    summary: dict[str, Any] = {
        'timestamp': timestamp,
        'marker': marker,
        'model_id': MODEL_ID,
        'tool_id': tool_id,
        'audit_path': str(out_path),
    }
    tool_created = False
    run_id: str | None = None
    started = time.monotonic()
    result_code = 1

    try:
        tool = request_json(
            'POST',
            '/api/v1/tools/create',
            {
                'id': tool_id,
                'name': 'E2E Agent Cancellation Fixture',
                'content': tool_source(marker),
                'meta': {'description': 'Temporary Agent Mode cancellation fixture.'},
                'access_grants': [],
            },
            timeout=60,
        )
        tool_created = True
        summary['tool_specs'] = tool.get('specs')

        user_message_id = f'msg-user-{uuid.uuid4().hex}'
        assistant_message_id = f'msg-assistant-{uuid.uuid4().hex}'
        prompt = (
            f'Cancellation fixture marker {marker}. Before calling cancellation_probe, '
            'carefully inspect the marker and write one short sentence explaining the next action. '
            'Then call cancellation_probe with the exact marker. Do not answer directly.'
        )
        chat_started = time.monotonic()
        chat = request_json(
            'POST',
            '/api/chat/completions',
            {
                'model': MODEL_ID,
                'messages': [{'role': 'user', 'content': prompt}],
                'stream': False,
                'params': {'function_calling': 'native', 'temperature': 0},
                'features': {},
                'variables': {},
                'session_id': f'agent-e2e-cancel-session-{uuid.uuid4().hex}',
                'parent_id': None,
                'message_ids': {MODEL_ID: assistant_message_id},
                'user_message': {
                    'id': user_message_id,
                    'parentId': None,
                    'childrenIds': [assistant_message_id],
                    'role': 'user',
                    'content': prompt,
                    'timestamp': int(time.time()),
                    'models': [MODEL_ID],
                },
                'tool_ids': [tool_id],
                'background_tasks': {},
            },
            timeout=180,
        )
        if not chat or not chat.get('status') or not chat.get('agent_run_id'):
            raise AssertionError(f'chat did not start Agent Mode run: {chat}')
        run_id = str(chat['agent_run_id'])
        summary['run_id'] = run_id
        summary['chat_start_seconds'] = round(time.monotonic() - chat_started, 4)

        cancel_started = time.monotonic()
        cancelled = request_json(
            'POST',
            f'/api/agent/runs/{run_id}/cancel',
            timeout=30,
        )
        summary['cancel_response_seconds'] = round(time.monotonic() - cancel_started, 4)
        summary['cancel_response_state'] = cancelled.get('state')
        if cancelled.get('state') != 'cancelled':
            raise AssertionError(f'cancel endpoint returned unexpected state: {cancelled}')

        deadline = time.monotonic() + 15
        types: list[str] = []
        while time.monotonic() < deadline:
            types = event_types(run_id)
            if 'run.cancelled' in types:
                break
            time.sleep(0.2)
        else:
            raise AssertionError(f'run.cancelled event did not arrive: {types}')

        runtime_after_cancel = runtime_status(run_id)
        time.sleep(5)
        detail_after_grace = request_json('GET', f'/api/agent/runs/{run_id}', timeout=30)
        types_after_grace = event_types(run_id)
        runtime_after_grace = runtime_status(run_id)
        forbidden = {
            'tool.requested',
            'tool.completed',
            'final.started',
            'final.delta',
            'run.completed',
            'run.failed',
        }
        observed_forbidden = [event_type for event_type in types_after_grace if event_type in forbidden]

        if detail_after_grace.get('state') != 'cancelled':
            raise AssertionError(f'backend run left cancelled state: {detail_after_grace}')
        if runtime_after_cancel.get('state') != 'cancelled' or not runtime_after_cancel.get(
            'cancel_requested'
        ):
            raise AssertionError(f'runtime did not accept cancellation: {runtime_after_cancel}')
        if runtime_after_grace.get('state') != 'cancelled' or not runtime_after_grace.get(
            'cancel_requested'
        ):
            raise AssertionError(f'runtime cancellation was not stable: {runtime_after_grace}')
        if observed_forbidden:
            raise AssertionError(
                f'work continued after immediate cancellation: {observed_forbidden}'
            )

        summary['event_types_after_grace'] = types_after_grace
        summary['backend_state_after_grace'] = detail_after_grace.get('state')
        summary['runtime_after_cancel'] = runtime_after_cancel
        summary['runtime_after_grace'] = runtime_after_grace
        summary['isolated_webui_anchor_after'] = shell_output(
            [
                'docker',
                'inspect',
                'open-webui-pr7',
                '--format',
                '{{.Config.Image}} {{.Image}} {{.State.Health.Status}} {{.RestartCount}} {{.State.StartedAt}}',
            ]
        )
        summary['runtime_anchor_after'] = shell_output(
            [
                'docker',
                'inspect',
                'openwebui-pr7-agentscope-runtime',
                '--format',
                '{{.Config.Image}} {{.Image}} {{.State.Health.Status}} {{.RestartCount}} {{.State.StartedAt}}',
            ]
        )
        summary['live_anchor_after'] = shell_output(
            [
                'docker',
                'inspect',
                'open-webui',
                '--format',
                '{{.Config.Image}} {{.Image}} {{.State.Health.Status}} {{.RestartCount}} {{.State.StartedAt}}',
            ]
        )
        summary['ok'] = True
        result_code = 0
    except Exception as exc:
        summary['ok'] = False
        summary['error'] = repr(exc)
        if run_id:
            try:
                summary['event_types_on_error'] = event_types(run_id)
            except Exception as event_exc:
                summary['event_read_error'] = repr(event_exc)
    finally:
        if tool_created:
            try:
                request_json('DELETE', f'/api/v1/tools/id/{tool_id}/delete', timeout=30)
                summary['tool_deleted'] = True
            except Exception as exc:
                summary['tool_deleted'] = False
                summary['tool_delete_error'] = repr(exc)
                summary['ok'] = False
                result_code = 1
        summary['elapsed_seconds'] = round(time.monotonic() - started, 3)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True),
            encoding='utf-8',
        )
        print(
            json.dumps(
                {
                    'ok': summary.get('ok'),
                    'run_id': summary.get('run_id'),
                    'audit_path': summary.get('audit_path'),
                    'event_types_after_grace': summary.get('event_types_after_grace'),
                    'runtime_state': (summary.get('runtime_after_grace') or {}).get('state'),
                    'tool_deleted': summary.get('tool_deleted'),
                    'error': summary.get('error'),
                },
                indent=2,
                ensure_ascii=False,
            )
        )

    return result_code


if __name__ == '__main__':
    raise SystemExit(main())
