import json
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]


def _project_dependencies() -> set[str]:
    with (REPO_ROOT / 'pyproject.toml').open('rb') as handle:
        return set(tomllib.load(handle)['project']['dependencies'])


def _requirements() -> set[str]:
    return {
        line.split('#', 1)[0].strip()
        for line in (REPO_ROOT / 'backend' / 'requirements.txt').read_text().splitlines()
        if line.split('#', 1)[0].strip()
    }


def test_v011_python_security_and_runtime_dependencies_are_locked() -> None:
    expected = {
        'uvicorn[standard]==0.51.0',
        'python-multipart==0.0.32',
        'orjson==3.11.9',
        'joserfc==1.7.4',
        'aiodns==4.0.4',
        'redis==8.0.1',
        'hiredis==3.4.0',
        'regex==2026.5.9',
        'lxml==6.1.1',
        'rapidocr==3.9.2',
    }
    project = _project_dependencies()
    requirements = _requirements()

    assert expected <= project
    assert expected <= requirements
    assert not any(dependency.startswith('python-jose') for dependency in project | requirements)

    with (REPO_ROOT / 'uv.lock').open('rb') as handle:
        locked = {
            package['name']: package['version'] for package in tomllib.load(handle)['package'] if 'version' in package
        }
    for dependency in expected:
        package_name, version = dependency.split('==', 1)
        package_name = package_name.split('[', 1)[0]
        assert locked[package_name] == version


def test_v011_pyodide_package_and_npm_lock_agree() -> None:
    package = json.loads((REPO_ROOT / 'package.json').read_text())
    package_lock = json.loads((REPO_ROOT / 'package-lock.json').read_text())

    assert package['dependencies']['pyodide'] == '^314.0.3'
    assert package_lock['packages']['']['dependencies']['pyodide'] == '^314.0.3'
    assert package_lock['packages']['node_modules/pyodide']['version'] == '314.0.3'


def test_removed_v011_test_dependency_is_absent() -> None:
    with (REPO_ROOT / 'pyproject.toml').open('rb') as handle:
        optional = tomllib.load(handle)['project']['optional-dependencies']['all']

    assert not any(dependency.startswith('gcp-storage-emulator') for dependency in optional)
