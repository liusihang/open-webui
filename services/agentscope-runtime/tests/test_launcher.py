from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SERVICE_DIR = Path(__file__).parents[1]


@pytest.mark.parametrize(
    ("arguments", "worker_env"),
    [
        (["--workers", "2"], {}),
        ([], {"WEB_CONCURRENCY": "2"}),
        ([], {"UVICORN_WORKERS": "2"}),
    ],
)
def test_launcher_rejects_multiple_workers_once_without_spawning(
    arguments: list[str],
    worker_env: dict[str, str],
) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SERVICE_DIR)
    env.pop("WEB_CONCURRENCY", None)
    env.pop("UVICORN_WORKERS", None)
    env.update(worker_env)

    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-m", "agentscope_runtime.launcher", *arguments],
        cwd=SERVICE_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )
    elapsed = time.monotonic() - started

    assert result.returncode == 2
    assert "AgentScope runtime requires exactly one worker" in result.stderr
    assert "Child process died" not in result.stderr
    assert elapsed < 5


def test_launcher_normalizes_single_worker_and_constructs_uvicorn_exec() -> None:
    script = """
import json

from agentscope_runtime.launcher import main

captured = {}

def capture_exec(executable, argv, env):
    captured['executable'] = executable
    captured['argv'] = argv
    captured['web_concurrency'] = env.get('WEB_CONCURRENCY')
    captured['uvicorn_workers'] = env.get('UVICORN_WORKERS')

result = main(
    ['--workers=1', '--host', '0.0.0.0', '--port', '9010'],
    environ={'WEB_CONCURRENCY': '1', 'UVICORN_WORKERS': '1'},
    exec_fn=capture_exec,
)
print(json.dumps({'result': result, **captured}))
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SERVICE_DIR)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=SERVICE_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    captured = json.loads(result.stdout)
    assert captured["result"] == 0
    assert captured["executable"] == sys.executable
    assert captured["argv"] == [
        sys.executable,
        "-m",
        "uvicorn",
        "agentscope_runtime.app:create_app_from_env",
        "--factory",
        "--workers",
        "1",
        "--host",
        "0.0.0.0",
        "--port",
        "9010",
    ]
    assert captured["web_concurrency"] == "1"
    assert captured["uvicorn_workers"] == "1"
