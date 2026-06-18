import os

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret')
os.environ.setdefault('ENABLE_DB_MIGRATIONS', 'false')

import pytest
from open_webui.agent.artifacts import (
    AgentRunArtifactRegistrar,
    agent_run_output_dir,
    agent_run_tmp_dir,
    artifact_metadata_for_path,
)


class FakeArtifactStore:
    def __init__(self):
        self.rows = []
        self._by_key = {}
        self._by_path = {}

    async def register_artifact(self, **kwargs):
        key = (kwargs['run_id'], kwargs.get('idempotency_key'))
        path_key = (kwargs['run_id'], kwargs['path'], kwargs['kind'])
        existing = self._by_key.get(key) if key[1] else None
        existing = existing or self._by_path.get(path_key)
        if existing is not None:
            return existing

        row = {
            'id': f'artifact-{len(self.rows) + 1}',
            **kwargs,
            'created_at': len(self.rows) + 1,
        }
        self.rows.append(row)
        if key[1]:
            self._by_key[key] = row
        self._by_path[path_key] = row
        return row


def test_default_run_output_and_tmp_paths_are_run_scoped():
    assert agent_run_output_dir('run-1') == '/workspace/agent-runs/run-1/outputs'
    assert agent_run_output_dir('run-1', '/workspace/research') == '/workspace/research'
    assert agent_run_tmp_dir('run-1') == '/workspace/agent-runs/run-1/tmp'


def test_artifact_cleanup_metadata_is_true_only_for_run_local_tmp_paths():
    assert artifact_metadata_for_path(
        '/workspace/agent-runs/run-1/outputs/report.csv',
        run_id='run-1',
    ) == {
        'cleanup_eligible': False,
        'retention': 'user_visible_output',
    }
    assert artifact_metadata_for_path(
        '/workspace/agent-runs/run-1/tmp/scratch.json',
        run_id='run-1',
    ) == {
        'cleanup_eligible': True,
        'retention': 'temporary_debug',
    }
    assert artifact_metadata_for_path(
        '/workspace/agent-runs/other-run/tmp/scratch.json',
        run_id='run-1',
    ) == {
        'cleanup_eligible': False,
        'retention': 'external_or_user_selected',
    }


@pytest.mark.asyncio
async def test_terminal_output_artifacts_register_idempotently_under_default_outputs():
    store = FakeArtifactStore()
    registrar = AgentRunArtifactRegistrar(store)

    first = await registrar.register_terminal_output_artifacts(
        run_id='run-1',
        user_id='user-1',
        participant_id='leader',
        terminal_server_id='main',
        output_paths=['report.csv', '/workspace/agent-runs/run-1/tmp/scratch.json'],
    )
    duplicate = await registrar.register_terminal_output_artifacts(
        run_id='run-1',
        user_id='user-1',
        participant_id='leader',
        terminal_server_id='main',
        output_paths=['report.csv', '/workspace/agent-runs/run-1/tmp/scratch.json'],
    )

    assert [artifact['path'] for artifact in first] == [
        '/workspace/agent-runs/run-1/outputs/report.csv',
        '/workspace/agent-runs/run-1/tmp/scratch.json',
    ]
    assert duplicate == first
    assert len(store.rows) == 2
    assert store.rows[0]['idempotency_key'] == 'artifact:leader:file:main:run-1:outputs:report.csv'
    assert store.rows[0]['metadata'] == {
        'cleanup_eligible': False,
        'retention': 'user_visible_output',
        'participant_id': 'leader',
    }
    assert store.rows[1]['metadata'] == {
        'cleanup_eligible': True,
        'retention': 'temporary_debug',
        'participant_id': 'leader',
    }


@pytest.mark.asyncio
async def test_terminal_output_artifacts_honor_user_requested_output_directory():
    store = FakeArtifactStore()
    registrar = AgentRunArtifactRegistrar(store)

    artifacts = await registrar.register_terminal_output_artifacts(
        run_id='run-1',
        user_id='user-1',
        participant_id='leader',
        terminal_server_id='main',
        output_paths=['report.csv'],
        output_dir='/workspace/project-results',
    )

    assert artifacts[0]['path'] == '/workspace/project-results/report.csv'
    assert artifacts[0]['metadata']['cleanup_eligible'] is False
    assert artifacts[0]['metadata']['retention'] == 'external_or_user_selected'
