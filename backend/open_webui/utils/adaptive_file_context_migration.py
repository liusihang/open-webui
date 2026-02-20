import logging
from collections.abc import Mapping

from open_webui.models.functions import Functions


log = logging.getLogger(__name__)

_MIGRATION_VERSION = 1


def _as_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: object, default: int) -> int:
    try:
        parsed = int(str(value).strip())
        return parsed if parsed >= 0 else default
    except (TypeError, ValueError):
        return default


def _extract_legacy_valves() -> dict[str, object] | None:
    for function in Functions.get_functions(include_valves=True):
        name = str(function.name or "").lower()
        function_id = str(function.id or "").lower()
        if "adaptive" not in name and "adaptive" not in function_id:
            continue

        valves = function.valves or {}
        if isinstance(valves, Mapping):
            normalized = {str(key): value for key, value in valves.items()}
            if any(
                key in normalized
                for key in (
                    "enabled",
                    "default_mode",
                    "max_tokens_per_file",
                    "max_tokens_per_request",
                    "debug",
                )
            ):
                return normalized

    return None


def run_adaptive_file_context_migration(app_config) -> str:
    current_version = _as_int(
        getattr(app_config, "ADAPTIVE_FILE_CONTEXT_MIGRATION_VERSION", 0),
        0,
    )
    if current_version >= _MIGRATION_VERSION:
        return "noop:already-migrated"

    legacy = _extract_legacy_valves()
    if not legacy:
        app_config.ADAPTIVE_FILE_CONTEXT_MIGRATION_VERSION = _MIGRATION_VERSION
        return "noop:no-legacy"

    try:
        if "enabled" in legacy:
            app_config.ADAPTIVE_FILE_CONTEXT_ENABLED = _as_bool(
                legacy.get("enabled"),
                bool(getattr(app_config, "ADAPTIVE_FILE_CONTEXT_ENABLED", False)),
            )

        if "default_mode" in legacy:
            mode = str(legacy.get("default_mode") or "retrieval").strip().lower()
            app_config.ADAPTIVE_FILE_CONTEXT_DEFAULT_MODE = (
                mode if mode in {"full", "retrieval"} else "retrieval"
            )

        if "max_tokens_per_file" in legacy:
            app_config.ADAPTIVE_FILE_CONTEXT_MAX_TOKENS_PER_FILE = _as_int(
                legacy.get("max_tokens_per_file"),
                int(getattr(app_config, "ADAPTIVE_FILE_CONTEXT_MAX_TOKENS_PER_FILE", 8000)),
            )

        if "max_tokens_per_request" in legacy:
            app_config.ADAPTIVE_FILE_CONTEXT_MAX_TOKENS_PER_REQUEST = _as_int(
                legacy.get("max_tokens_per_request"),
                int(
                    getattr(
                        app_config,
                        "ADAPTIVE_FILE_CONTEXT_MAX_TOKENS_PER_REQUEST",
                        32000,
                    )
                ),
            )

        if "debug" in legacy:
            app_config.ADAPTIVE_FILE_CONTEXT_DEBUG = _as_bool(
                legacy.get("debug"),
                bool(getattr(app_config, "ADAPTIVE_FILE_CONTEXT_DEBUG", False)),
            )

        app_config.ADAPTIVE_FILE_CONTEXT_MIGRATION_VERSION = _MIGRATION_VERSION
        return "migrated"
    except Exception:
        log.exception("adaptive file context migration failed")
        return "failed"
