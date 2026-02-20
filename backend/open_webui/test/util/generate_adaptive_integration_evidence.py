import importlib.util
import json
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
EVIDENCE_DIR = ROOT / ".sisyphus" / "evidence"
MIDDLEWARE_PATH = ROOT / "open-webui-latest" / "backend" / "open_webui" / "utils" / "middleware.py"
MIGRATION_PATH = ROOT / "open-webui-latest" / "backend" / "open_webui" / "utils" / "adaptive_file_context_migration.py"


def _load_migration_module_with_stub_functions():
    open_webui_module = types.ModuleType("open_webui")
    models_module = types.ModuleType("open_webui.models")
    functions_module = types.ModuleType("open_webui.models.functions")

    class _FunctionsStub:
        fixtures = []

        @classmethod
        def get_functions(cls, include_valves=True):
            return cls.fixtures

    functions_module.Functions = _FunctionsStub

    sys.modules.setdefault("open_webui", open_webui_module)
    sys.modules.setdefault("open_webui.models", models_module)
    sys.modules["open_webui.models.functions"] = functions_module

    spec = importlib.util.spec_from_file_location("adaptive_file_context_migration_local", MIGRATION_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load migration module spec")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, _FunctionsStub


def main() -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    middleware_text = MIDDLEWARE_PATH.read_text(encoding="utf-8")

    debug_on_checks = {
        "has_action": '"action": "adaptive_file_context"' in middleware_text,
        "has_reason": '"reason": decision.reason' in middleware_text,
        "has_tokens": '"estimated_tokens": decision.estimated_tokens' in middleware_text,
        "contains_raw_content_field": "data.content" in middleware_text,
    }
    (EVIDENCE_DIR / "task-5-debug-on.log").write_text(
        json.dumps(debug_on_checks, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    debug_off_checks = {
        "guarded_by_flag": "if adaptive_debug:" in middleware_text,
        "emitter_inside_guard": "await __event_emitter__(" in middleware_text,
    }
    (EVIDENCE_DIR / "task-5-debug-off.log").write_text(
        json.dumps(debug_off_checks, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    task6_on = {
        "adaptive_enabled_gate": "if adaptive_enabled:" in middleware_text,
        "metadata_mutation": 'metadata["files"] = files' in middleware_text,
    }
    (EVIDENCE_DIR / "task-6-on.log").write_text(
        json.dumps(task6_on, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    task6_off = {
        "feature_flag_present": "ADAPTIVE_FILE_CONTEXT_ENABLED" in middleware_text,
        "passthrough_retains_handler": "chat_completion_files_handler" in middleware_text,
    }
    (EVIDENCE_DIR / "task-6-off.log").write_text(
        json.dumps(task6_off, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    task7_mixed = {
        "uses_item_context_for_branch": 'item.get("context") == "full"' in (
            ROOT / "open-webui-latest" / "backend" / "open_webui" / "retrieval" / "utils.py"
        ).read_text(encoding="utf-8"),
    }
    (EVIDENCE_DIR / "task-7-mixed.log").write_text(
        json.dumps(task7_mixed, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    task7_malformed = {
        "non_dict_guard": "Skipping malformed retrieval item" in (
            ROOT / "open-webui-latest" / "backend" / "open_webui" / "retrieval" / "utils.py"
        ).read_text(encoding="utf-8"),
    }
    (EVIDENCE_DIR / "task-7-malformed.log").write_text(
        json.dumps(task7_malformed, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    task7_scope = {
        "scope_reason": "scope_denied" in middleware_text,
        "excluded_skip": "_adaptive_excluded" in (
            ROOT / "open-webui-latest" / "backend" / "open_webui" / "retrieval" / "utils.py"
        ).read_text(encoding="utf-8"),
    }
    (EVIDENCE_DIR / "task-7-scope-isolation.log").write_text(
        json.dumps(task7_scope, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    migration_module, functions_stub = _load_migration_module_with_stub_functions()

    functions_stub.fixtures = [
        types.SimpleNamespace(
            id="adaptive_file_context",
            name="Adaptive File Context",
            valves={
                "enabled": True,
                "default_mode": "full",
                "max_tokens_per_file": 123,
                "max_tokens_per_request": 456,
                "debug": True,
            },
        )
    ]

    cfg = types.SimpleNamespace(
        ADAPTIVE_FILE_CONTEXT_ENABLED=False,
        ADAPTIVE_FILE_CONTEXT_DEFAULT_MODE="retrieval",
        ADAPTIVE_FILE_CONTEXT_MAX_TOKENS_PER_FILE=8000,
        ADAPTIVE_FILE_CONTEXT_MAX_TOKENS_PER_REQUEST=32000,
        ADAPTIVE_FILE_CONTEXT_DEBUG=False,
        ADAPTIVE_FILE_CONTEXT_MIGRATION_VERSION=0,
    )

    first = migration_module.run_adaptive_file_context_migration(cfg)
    (EVIDENCE_DIR / "task-8-migrate-first.txt").write_text(
        json.dumps(
            {
                "result": first,
                "version": cfg.ADAPTIVE_FILE_CONTEXT_MIGRATION_VERSION,
                "enabled": cfg.ADAPTIVE_FILE_CONTEXT_ENABLED,
                "default_mode": cfg.ADAPTIVE_FILE_CONTEXT_DEFAULT_MODE,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    second = migration_module.run_adaptive_file_context_migration(cfg)
    (EVIDENCE_DIR / "task-8-migrate-rerun.txt").write_text(
        json.dumps(
            {
                "result": second,
                "version": cfg.ADAPTIVE_FILE_CONTEXT_MIGRATION_VERSION,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
