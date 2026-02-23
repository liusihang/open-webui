import importlib.util
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
ADAPTIVE_PATH = (
    ROOT / "backend" / "open_webui" / "utils" / "adaptive_file_context.py"
)
MIGRATION_PATH = (
    ROOT / "backend" / "open_webui" / "utils" / "adaptive_file_context_migration.py"
)


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_migration_module_with_stub_functions():
    open_webui_module = types.ModuleType("open_webui")
    models_module = types.ModuleType("open_webui.models")
    functions_module = types.ModuleType("open_webui.models.functions")

    class FunctionsStub:
        fixtures = []

        @classmethod
        def get_functions(cls, include_valves=True):
            return cls.fixtures

    setattr(functions_module, "Functions", FunctionsStub)
    sys.modules.setdefault("open_webui", open_webui_module)
    sys.modules.setdefault("open_webui.models", models_module)
    sys.modules["open_webui.models.functions"] = functions_module

    return _load_module(MIGRATION_PATH, "adaptive_migration_test"), FunctionsStub


def _default_config() -> dict[str, object]:
    return {
        "default_mode": "retrieval",
        "max_tokens_per_file": 8000,
        "max_tokens_per_request": 32000,
        "small_file_tokens": 1200,
    }


def test_adaptive_policy_is_deterministic() -> None:
    mod = _load_module(ADAPTIVE_PATH, "adaptive_policy_test")
    files = [
        {"id": "f1", "type": "file", "size": 1200},
        {"id": "f2", "type": "file", "token_count": 9000},
    ]
    first = mod.decide_file_contexts("Summarize this", files, _default_config())
    second = mod.decide_file_contexts("Summarize this", files, _default_config())

    assert [(d.mode, d.reason, d.estimated_tokens) for d in first] == [
        (d.mode, d.reason, d.estimated_tokens) for d in second
    ]


def test_manual_full_override_preserved_under_safe_budget() -> None:
    mod = _load_module(ADAPTIVE_PATH, "adaptive_policy_test_manual")
    files = [{"id": "f1", "type": "file", "context": "full", "token_count": 1000}]
    config = _default_config()
    config["max_tokens_per_request"] = 5000
    decisions = mod.decide_file_contexts("find section", files, config)

    assert decisions[0].mode == "full"
    assert decisions[0].reason == "manual_override"


def test_budget_arbitration_downgrades_deterministically() -> None:
    mod = _load_module(ADAPTIVE_PATH, "adaptive_policy_test_budget")
    files = [
        {"id": "a", "type": "file", "token_count": 2000},
        {"id": "b", "type": "file", "token_count": 1800},
    ]
    config = _default_config()
    config["default_mode"] = "full"
    config["max_tokens_per_request"] = 1500

    decisions = mod.decide_file_contexts("hello", files, config)
    assert all(d.mode == "retrieval" for d in decisions)
    assert all(d.reason == "budget_cap" for d in decisions)


def test_malformed_item_falls_back_conservative() -> None:
    mod = _load_module(ADAPTIVE_PATH, "adaptive_policy_test_malformed")
    items, decisions = mod.apply_adaptive_context_to_items("help", ["bad-item"], _default_config())

    assert items[0]["context"] == "retrieval"
    assert decisions[0].reason == "malformed_item"


def test_migration_first_run_and_rerun() -> None:
    migration_module, functions_stub = _load_migration_module_with_stub_functions()

    functions_stub.fixtures = [
        types.SimpleNamespace(
            id="adaptive_file_context",
            name="Adaptive File Context",
            valves={
                "enabled": True,
                "default_mode": "full",
                "max_tokens_per_file": 111,
                "max_tokens_per_request": 222,
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
    second = migration_module.run_adaptive_file_context_migration(cfg)

    assert first == "migrated"
    assert second == "noop:already-migrated"
    assert cfg.ADAPTIVE_FILE_CONTEXT_ENABLED is True
    assert cfg.ADAPTIVE_FILE_CONTEXT_DEFAULT_MODE == "full"
