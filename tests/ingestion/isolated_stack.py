"""Run on aiserver against a copied source tree; no Live mounts/env/ports.

Uses only synthetic accounts, a private internal network, a disposable pgvector
database and a local fake encoder. Retains containers/logs for verification.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
PREFIX = 'owui-idx-fixture-'
IMAGE = 'open-webui:v0123-intelligent-ui-914f8a38320d-20261022-slim'
NETWORK = PREFIX + 'net'
DATA = ROOT / 'isolated-data'
FIXTURE = ROOT / 'isolated-fixture'


def call(args, *, timeout=60, check=True):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError('Isolated command failed: ' + ' '.join(args[:4]) + '\n' + result.stdout[-4000:])
    return result


def container(name, *, image=IMAGE, command=(), env=None, memory='1g', cpus='1', tmp_size=512 * 1024**2, binds=True):
    args = [
        'docker',
        'run',
        '-d',
        '--name',
        PREFIX + name,
        '--network',
        NETWORK,
        '--memory',
        memory,
        '--memory-swap',
        memory,
        '--cpus',
        cpus,
        '--pids-limit',
        '128',
        '--tmpfs',
        f'/tmp:rw,size={tmp_size}',
        '--workdir',
        '/work',
    ]
    if name.startswith('worker-'):
        args += [
            '--read-only',
            '--cap-drop',
            'ALL',
            '--security-opt',
            'no-new-privileges:true',
            '--init',
            '--device-read-bps',
            '/dev/sdb:20mb',
            '--device-write-bps',
            '/dev/sdb:10mb',
            '--health-cmd',
            'python -c "from pathlib import Path; import time; p=Path(\'/tmp/indexing/worker-ready\'); exit(0 if p.exists() and time.time()-p.stat().st_mtime<30 else 1)"',
            '--health-interval',
            '10s',
            '--health-start-period',
            '30s',
        ]
    elif name != 'web':
        args += ['--no-healthcheck']
    if binds:
        args += [
            '--mount',
            f'type=bind,src={ROOT},dst=/work,readonly',
            '--mount',
            f'type=bind,src={DATA},dst=/data',
            '--mount',
            f'type=bind,src={FIXTURE},dst=/fixture',
        ]
    for key, value in (env or {}).items():
        args += ['-e', key + '=' + str(value)]
    args += ['--entrypoint', 'python'] if image == IMAGE else []
    args += [image, *command]
    return call(args).stdout.strip()


def wait_command(args, label, *, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if call(args, timeout=15, check=False).returncode == 0:
            print('ISOLATED_READY', label, flush=True)
            return
        time.sleep(1)
    raise RuntimeError('Isolated readiness timed out: ' + label)


def clean_source_metadata():
    if not str(ROOT).startswith('/home/aiserver/staging/ingestion-safety-20261024-'):
        raise RuntimeError('Refusing metadata cleanup outside the owned copied source')
    removed = 0
    for path in ROOT.rglob('._*'):
        if not path.is_file():
            continue
        with path.open('rb') as stream:
            if stream.read(4) != b'\x00\x05\x16\x07':
                continue
        path.unlink()
        removed += 1
    print('ISOLATED_APPLEDOUBLE_METADATA_REMOVED', removed, flush=True)


def readable_source():
    # The production image owns these files as root. The test bind is owned by
    # the SSH user; make only copied public source resources readable to the
    # cap-drop worker, not fixture credentials or private deployment files.
    ROOT.chmod(0o755)
    for name in ('CHANGELOG.md', 'package.json', 'pyproject.toml'):
        (ROOT / name).chmod(0o644)
    for directory in (ROOT / 'backend', ROOT / 'tests'):
        for path in (directory, *directory.rglob('*')):
            if path.is_dir():
                path.chmod(0o755)
            elif path.is_file():
                path.chmod(0o644)


def run():
    if not str(ROOT).startswith('/home/aiserver/staging/ingestion-safety-20261024-'):
        raise RuntimeError('Refusing to run outside the explicitly owned staging directory')
    clean_source_metadata()
    readable_source()
    if call(['docker', 'network', 'inspect', NETWORK], check=False).returncode == 0:
        raise RuntimeError('Isolated stack already exists; inspect it rather than overwriting')
    DATA.mkdir(exist_ok=True)
    FIXTURE.mkdir(exist_ok=True)
    # Host Docker's default pools are exhausted. This /28 was checked against
    # current Docker IPAM and host routes; do not alter existing networks.
    call(['docker', 'network', 'create', '--internal', '--subnet', '10.253.240.0/28', NETWORK])
    container(
        'pg',
        image='pgvector/pgvector:pg16',
        binds=False,
        memory='768m',
        env={'POSTGRES_PASSWORD': 'fixture-only-password', 'POSTGRES_DB': 'fixture', 'PGDATA': '/tmp/pgdata'},
    )
    wait_command(['docker', 'exec', PREFIX + 'pg', 'pg_isready', '-U', 'postgres', '-d', 'fixture'], 'postgres')
    container(
        'redis',
        image='redis:7-alpine',
        binds=False,
        memory='64m',
        command=('redis-server', '--save', '', '--appendonly', 'no'),
    )
    env = {
        'ENABLE_DB_MIGRATIONS': 'false',
        'USE_SLIM_DOCKER': 'true',
        'PYTHONPATH': '/work/backend',
        'FRONTEND_BUILD_DIR': '/app/build',
        'DATA_DIR': '/data',
        'STATIC_DIR': '/tmp/static',
        'WEBUI_SECRET_KEY': 'isolated-indexing-fixture-not-a-live-secret',
        'DATABASE_URL': f'postgresql://postgres:fixture-only-password@{PREFIX}pg:5432/fixture',
        'PGVECTOR_DB_URL': f'postgresql://postgres:fixture-only-password@{PREFIX}pg:5432/fixture',
        'VECTOR_DB': 'pgvector',
        'PGVECTOR_INITIALIZE_MAX_VECTOR_LENGTH': '2048',
        'PGVECTOR_USE_HALFVEC': 'true',
        'DATABASE_ENABLE_SESSION_SHARING': 'true',
        'DATABASE_POOL_SIZE': '0',
        'DATABASE_POOL_MAX_OVERFLOW': '0',
        'PGVECTOR_POOL_SIZE': '0',
        'PGVECTOR_POOL_MAX_OVERFLOW': '0',
        'REDIS_URL': f'redis://{PREFIX}redis:6379/0',
        'WEBSOCKET_MANAGER': 'redis',
        'OFFLINE_MODE': 'true',
        'HF_HUB_OFFLINE': '1',
        'RAG_EMBEDDING_ENGINE': 'openai',
        'INDEXING_TEMP_DIR': '/tmp/indexing',
        'INDEXING_QUEUE_ENABLED': 'true',
        'OMP_NUM_THREADS': '1',
        'OPENBLAS_NUM_THREADS': '1',
        'MKL_NUM_THREADS': '1',
        'HTTP_PROXY': '',
        'HTTPS_PROXY': '',
        'ALL_PROXY': '',
    }
    container('mock', command=('/work/tests/ingestion/fixture_runtime.py', 'mock'), env=env, memory='256m')
    seed_env = {**env, 'ENABLE_DB_MIGRATIONS': 'true'}
    container('seed', command=('/work/tests/ingestion/fixture_runtime.py', 'seed'), env=seed_env, memory='1g')
    code = call(['docker', 'wait', PREFIX + 'seed'], timeout=120).stdout.strip()
    log = call(['docker', 'logs', PREFIX + 'seed']).stdout
    (FIXTURE / 'seed.log').write_text(log)
    if code != '0':
        raise RuntimeError('Fixture seeding failed:\n' + log[-5000:])
    print('ISOLATED_SEEDED', flush=True)
    acceptance(env)


def acceptance(env):
    for slot in (0, 1):
        container(
            f'worker-{slot}',
            command=('-m', 'open_webui.retrieval.ingestion_worker'),
            env={**env, 'INDEXING_WORKER_SLOT': str(slot)},
            memory='2g',
        )
    container(
        'web',
        command=('-m', 'uvicorn', 'open_webui.main:app', '--host', '0.0.0.0', '--port', '8080', '--workers', '4'),
        env={**env, 'UVICORN_WORKERS': '4'},
        memory='4g',
        cpus='4',
    )
    wait_command(
        ['docker', 'exec', PREFIX + 'web', 'curl', '-fsS', 'http://localhost:8080/health'],
        'four-worker web',
        timeout=150,
    )
    container('probe', command=('/work/tests/ingestion/fixture_runtime.py', 'probe'), env=env, memory='256m')
    code = call(['docker', 'wait', PREFIX + 'probe'], timeout=300).stdout.strip()
    log = call(['docker', 'logs', PREFIX + 'probe']).stdout
    (FIXTURE / 'probe.log').write_text(log)
    print(log[-6000:], flush=True)
    for name in ('worker-0', 'worker-1', 'web'):
        (FIXTURE / (name + '.log')).write_text(call(['docker', 'logs', PREFIX + name]).stdout)
    if code != '0':
        raise RuntimeError('Isolated API acceptance failed; inspect the owned fixture logs')
    states = []
    for name in ('web', 'worker-0', 'worker-1'):
        row = json.loads(call(['docker', 'inspect', PREFIX + name]).stdout)[0]
        states.append(
            {
                'name': name,
                'memory': row['HostConfig']['Memory'],
                'memory_swap': row['HostConfig']['MemorySwap'],
                'state': row['State']['Status'],
            }
        )
    (FIXTURE / 'resource-receipt.json').write_text(json.dumps(states, indent=2))
    print('ISOLATED_API_ACCEPTANCE_PASSED', flush=True)


def refresh_and_faults(*, skip_faults=False):
    """Replace only this task's synthetic Web/worker/probe containers."""
    readable_source()
    row = json.loads(call(['docker', 'inspect', PREFIX + 'seed']).stdout)[0]
    env = dict(item.split('=', 1) for item in row['Config']['Env'])
    expected = f'postgresql://postgres:fixture-only-password@{PREFIX}pg:5432/fixture'
    mounts = {item['Destination']: item['Source'] for item in row['Mounts']}
    if env.get('DATABASE_URL') != expected or mounts.get('/work') != str(ROOT):
        raise RuntimeError('Fixture identity mismatch')
    active = call(
        [
            'docker',
            'exec',
            PREFIX + 'pg',
            'psql',
            '-U',
            'postgres',
            '-d',
            'fixture',
            '-Atc',
            "SELECT count(*) FROM ingestion_job WHERE status IN ('queued','running')",
        ]
    ).stdout.strip()
    if active != '0':
        raise RuntimeError('Refusing refresh while a synthetic task is active')
    # The in-progress test DB predates this development-only queue column;
    # production upgrades use the final f64b7a2810ce migration in one step.
    call(
        [
            'docker',
            'exec',
            PREFIX + 'pg',
            'psql',
            '-U',
            'postgres',
            '-d',
            'fixture',
            '-c',
            'ALTER TABLE ingestion_job ADD COLUMN IF NOT EXISTS cleaned_at BIGINT; '
            'CREATE INDEX IF NOT EXISTS ix_ingestion_cleanup ON ingestion_job (cleaned_at,status,finished_at)',
        ]
    )
    for name in (
        'worker-0',
        'worker-1',
        'web',
        'probe',
        'mock',
        'fault-oom',
        'fault-timeout',
        'fault-temp',
        'fault-db',
    ):
        if call(['docker', 'inspect', PREFIX + name], check=False).returncode:
            continue
        (FIXTURE / (name + '-refresh-' + str(time.time_ns()) + '.log')).write_text(
            call(['docker', 'logs', PREFIX + name]).stdout
        )
        call(['docker', 'stop', '-t', '5', PREFIX + name])
        call(['docker', 'rm', PREFIX + name])
    env.update(
        ENABLE_DB_MIGRATIONS='false',
        INDEXING_JOB_TIMEOUT='45',
        PGOPTIONS='-c statement_timeout=30000 -c lock_timeout=3000 -c idle_in_transaction_session_timeout=30000',
    )
    container('mock', command=('/work/tests/ingestion/fixture_runtime.py', 'mock'), env=env, memory='256m')
    acceptance(env)
    if skip_faults:
        return
    events_before = {
        str(slot): call(['docker', 'exec', PREFIX + f'worker-{slot}', 'cat', '/sys/fs/cgroup/memory.events']).stdout
        for slot in (0, 1)
    }
    for case in ('oom', 'timeout', 'temp', 'db'):
        if case == 'temp':
            call(['docker', 'pause', PREFIX + 'worker-1'])
            filler = (
                "from pathlib import Path; p=Path('/tmp/indexing/fixture-filler'); f=p.open('wb'); "
                "block=b'x'*1048576; [f.write(block) for _ in range(510)]; f.close()"
            )
            call(['docker', 'exec', PREFIX + 'worker-0', 'python', '-c', filler])
        container(
            'fault-' + case, command=('/work/tests/ingestion/fixture_runtime.py', 'fault', case), env=env, memory='256m'
        )
        if case == 'db':
            deadline = time.monotonic() + 90
            while not (FIXTURE / 'db-fault-ready').exists():
                if time.monotonic() > deadline:
                    raise RuntimeError('DB-fault probe never reached its running checkpoint')
                time.sleep(1)
            call(['docker', 'pause', PREFIX + 'pg'])
            try:
                time.sleep(50)
            finally:
                call(['docker', 'unpause', PREFIX + 'pg'])
        code = call(['docker', 'wait', PREFIX + 'fault-' + case], timeout=240).stdout.strip()
        log = call(['docker', 'logs', PREFIX + 'fault-' + case]).stdout
        (FIXTURE / ('fault-' + case + '.log')).write_text(log)
        if case == 'temp':
            call(
                [
                    'docker',
                    'exec',
                    PREFIX + 'worker-0',
                    'python',
                    '-c',
                    "from pathlib import Path; Path('/tmp/indexing/fixture-filler').unlink()",
                ]
            )
            call(['docker', 'unpause', PREFIX + 'worker-1'])
        print(log[-2500:], flush=True)
        if code != '0':
            raise RuntimeError('Fault acceptance failed: ' + case)
    events_after = {
        str(slot): call(['docker', 'exec', PREFIX + f'worker-{slot}', 'cat', '/sys/fs/cgroup/memory.events']).stdout
        for slot in (0, 1)
    }
    (FIXTURE / 'oom-events.json').write_text(json.dumps({'before': events_before, 'after': events_after}, indent=2))
    print('ISOLATED_FAULT_ACCEPTANCE_PASSED', flush=True)


def memory_acceptance():
    row = json.loads(call(['docker', 'inspect', PREFIX + 'seed']).stdout)[0]
    env = dict(item.split('=', 1) for item in row['Config']['Env'])
    if env.get('DATABASE_URL') != f'postgresql://postgres:fixture-only-password@{PREFIX}pg:5432/fixture':
        raise RuntimeError('Not the synthetic fixture database')
    env.update(
        ENABLE_DB_MIGRATIONS='false',
        INDEXING_JOB_TIMEOUT='1200',
        PGOPTIONS='-c statement_timeout=30000 -c lock_timeout=3000 -c idle_in_transaction_session_timeout=30000',
    )
    for slot in (0, 1):
        name = f'worker-{slot}'
        call(['docker', 'stop', '-t', '5', PREFIX + name])
        call(['docker', 'rm', PREFIX + name])
        container(
            name,
            command=('-m', 'open_webui.retrieval.ingestion_worker'),
            env={**env, 'INDEXING_WORKER_SLOT': str(slot)},
            memory='2g',
        )
    container('memory-probe', command=('/work/tests/ingestion/fixture_runtime.py', 'memory'), env=env, memory='256m')
    code = call(['docker', 'wait', PREFIX + 'memory-probe'], timeout=700).stdout.strip()
    log = call(['docker', 'logs', PREFIX + 'memory-probe']).stdout
    (FIXTURE / 'memory-probe.log').write_text(log)
    print(log[-3500:], flush=True)
    if code != '0':
        raise RuntimeError('Bounded memory acceptance failed')


if __name__ == '__main__':
    if sys.argv[1:] == ['resume-after-seed']:
        row = json.loads(call(['docker', 'inspect', PREFIX + 'seed']).stdout)[0]
        if row['State']['ExitCode'] != 0 or row['State']['Running']:
            raise RuntimeError('Seed is not successfully complete')
        fixture_env = dict(item.split('=', 1) for item in row['Config']['Env'])
        fixture_env['ENABLE_DB_MIGRATIONS'] = 'false'
        acceptance(fixture_env)
    elif sys.argv[1:] == ['clean-source-metadata']:
        clean_source_metadata()
    elif sys.argv[1:] == ['refresh-and-faults']:
        refresh_and_faults()
    elif sys.argv[1:] == ['refresh-and-probe']:
        refresh_and_faults(skip_faults=True)
    elif sys.argv[1:] == ['memory-acceptance']:
        memory_acceptance()
    else:
        run()
