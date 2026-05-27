import os
from collections.abc import Mapping

EXTERNAL_SERVICES_SLIM_ENV = 'USE_EXTERNAL_SERVICES_SLIM_DOCKER'


def is_external_services_slim_enabled(env: Mapping[str, str] | None = None) -> bool:
    source = env if env is not None else os.environ
    return source.get(EXTERNAL_SERVICES_SLIM_ENV, 'false').lower() == 'true'


def resolve_vector_db(env: Mapping[str, str] | None = None) -> str:
    source = env if env is not None else os.environ
    default = 'pgvector' if is_external_services_slim_enabled(source) else 'chroma'
    return source.get('VECTOR_DB', default)


def validate_vector_db_for_runtime_profile(
    vector_db: str,
    env: Mapping[str, str] | None = None,
) -> None:
    if is_external_services_slim_enabled(env) and vector_db == 'chroma':
        raise RuntimeError(
            'VECTOR_DB=chroma is not available in the external-services slim image. '
            'Set VECTOR_DB=pgvector (or another installed backend) when '
            'USE_EXTERNAL_SERVICES_SLIM_DOCKER=true.'
        )
