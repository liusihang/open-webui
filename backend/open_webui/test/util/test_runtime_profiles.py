import pytest
from pathlib import Path

from open_webui.utils.runtime_profiles import (
    is_external_services_slim_enabled,
    resolve_vector_db,
    validate_vector_db_for_runtime_profile,
)


def test_external_services_slim_disabled_by_default():
    assert is_external_services_slim_enabled({}) is False
    assert resolve_vector_db({}) == 'chroma'


def test_external_services_slim_defaults_vector_db_to_pgvector():
    env = {'USE_EXTERNAL_SERVICES_SLIM_DOCKER': 'true'}

    assert is_external_services_slim_enabled(env) is True
    assert resolve_vector_db(env) == 'pgvector'


def test_external_services_slim_rejects_chroma_backend():
    env = {
        'USE_EXTERNAL_SERVICES_SLIM_DOCKER': 'true',
        'VECTOR_DB': 'chroma',
    }

    with pytest.raises(RuntimeError, match='VECTOR_DB=chroma is not available'):
        validate_vector_db_for_runtime_profile(resolve_vector_db(env), env)


def test_external_services_slim_allows_explicit_pgvector_backend():
    env = {
        'USE_EXTERNAL_SERVICES_SLIM_DOCKER': 'true',
        'VECTOR_DB': 'pgvector',
    }

    validate_vector_db_for_runtime_profile(resolve_vector_db(env), env)


def test_external_services_slim_requirements_include_startup_dependencies():
    requirements_path = Path(__file__).resolve().parents[3] / 'requirements-external-slim.txt'
    requirements_text = requirements_path.read_text()

    for package_name in (
        'typer',
        'python-dotenv',
        'PyYAML',
        'black',
        'huggingface-hub',
    ):
        assert package_name in requirements_text


def _requirement_map(path: Path) -> dict[str, str]:
    requirements = {}
    for line in path.read_text().splitlines():
        candidate = line.split('#', 1)[0].strip()
        if not candidate:
            continue
        package_name = candidate.split('==', 1)[0].split('[', 1)[0].lower()
        requirements[package_name] = candidate
    return requirements


def test_external_services_slim_dependencies_track_v011_runtime_versions():
    backend_dir = Path(__file__).resolve().parents[3]
    primary = _requirement_map(backend_dir / 'requirements.txt')
    external_slim = _requirement_map(backend_dir / 'requirements-external-slim.txt')

    required = {'uvicorn', 'python-multipart', 'orjson', 'regex', 'aiodns', 'redis', 'hiredis', 'lxml'}
    assert required <= external_slim.keys()
    for package_name in required:
        assert external_slim[package_name] == primary[package_name]
    assert 'python-jose' not in external_slim
    assert 'rapidocr-onnxruntime' not in external_slim
