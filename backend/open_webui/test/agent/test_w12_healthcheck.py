import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[4]


def load_healthcheck():
    path = ROOT / 'scripts' / 'agent_mode' / 'healthcheck.py'
    spec = importlib.util.spec_from_file_location('agent_mode_healthcheck', path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_env_readiness_requires_runtime_url_and_token_when_enabled():
    healthcheck = load_healthcheck()

    result = healthcheck.evaluate_env_readiness({'ENABLE_AGENT_MODE': 'true'})

    assert result.status == 'failed'
    assert result.details['missing'] == ['AGENT_RUNTIME_BASE_URL', 'AGENT_RUNTIME_SERVICE_TOKEN']


def test_env_readiness_accepts_disabled_mode_without_runtime_env():
    healthcheck = load_healthcheck()

    result = healthcheck.evaluate_env_readiness({'ENABLE_AGENT_MODE': 'false'})

    assert result.status == 'ok'
    assert result.details['enable_agent_mode'] is False


def test_runtime_health_requires_status_ok_payload():
    healthcheck = load_healthcheck()

    ok = healthcheck.evaluate_runtime_health(200, {'status': 'ok'})
    failed = healthcheck.evaluate_runtime_health(200, {'status': 'starting'})

    assert ok.status == 'ok'
    assert failed.status == 'failed'


def test_runtime_readiness_matches_requested_run_id():
    healthcheck = load_healthcheck()

    ok = healthcheck.evaluate_runtime_status(200, {'run_id': 'run-1'}, run_id='run-1')
    failed = healthcheck.evaluate_runtime_status(200, {'run_id': 'other'}, run_id='run-1')

    assert ok.status == 'ok'
    assert failed.status == 'failed'


def test_run_checks_can_verify_env_only_without_network_probe():
    healthcheck = load_healthcheck()
    args = SimpleNamespace(
        check_env=True,
        runtime_base_url='',
        service_token='',
        readiness_run_id='',
        skip_runtime=True,
        timeout=1,
    )

    summary = healthcheck.run_checks(
        args,
        {
            'ENABLE_AGENT_MODE': 'true',
            'AGENT_RUNTIME_BASE_URL': 'http://agent-runtime.test',
            'AGENT_RUNTIME_SERVICE_TOKEN': 'secret',
            'AGENT_TEAM_MAX_SUBAGENTS': '5',
        },
    )

    assert summary['status'] == 'ok'
    assert summary['probes'][0]['name'] == 'env'
