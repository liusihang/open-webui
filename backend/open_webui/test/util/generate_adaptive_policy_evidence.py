import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
MODULE_PATH = ROOT / "open-webui-latest" / "backend" / "open_webui" / "utils" / "adaptive_file_context.py"
EVIDENCE_DIR = ROOT / ".sisyphus" / "evidence"


def _load_module():
    spec = importlib.util.spec_from_file_location("adaptive_file_context_local", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load adaptive_file_context module spec")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    mod = _load_module()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    config = {
        "default_mode": "retrieval",
        "max_tokens_per_file": 8000,
        "max_tokens_per_request": 32000,
        "small_file_tokens": 1200,
    }

    query = "Summarize the main points of this report"
    files = [
        {"id": "small", "type": "file", "size": 1500},
        {"id": "large", "type": "file", "size": 9000},
        {"id": "manual", "type": "file", "context": "full", "size": 30000},
        {"id": "capped", "type": "file", "token_count": 9001},
    ]

    decisions_first = mod.decide_file_contexts(query=query, items=files, config=config)
    decisions_second = mod.decide_file_contexts(query=query, items=files, config=config)

    deterministic = {
        "identical": [
            (d.mode, d.reason, d.estimated_tokens) for d in decisions_first
        ]
        == [(d.mode, d.reason, d.estimated_tokens) for d in decisions_second],
        "first": [d.__dict__ for d in decisions_first],
        "second": [d.__dict__ for d in decisions_second],
    }

    (EVIDENCE_DIR / "task-2-determinism.json").write_text(
        json.dumps(deterministic, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    sparse_decisions = mod.decide_file_contexts(
        query="please help",
        items=[{}, {"metadata": {"pages": "2"}}, {"meta": {"size": "1200"}}, {"context": "retrieval"}],
        config=config,
    )
    (EVIDENCE_DIR / "task-2-sparse.json").write_text(
        json.dumps({"handled": True, "result": [d.__dict__ for d in sparse_decisions]}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    over_budget = mod.decide_file_contexts(
        query="overview",
        items=[{"id": "f1", "type": "file", "token_count": 5000}, {"id": "f2", "type": "file", "token_count": 4500}],
        config={"default_mode": "full", "max_tokens_per_file": 10000, "max_tokens_per_request": 2000, "small_file_tokens": 1200},
    )
    (EVIDENCE_DIR / "task-3-over-budget.json").write_text(
        json.dumps([d.__dict__ for d in over_budget], indent=2, ensure_ascii=False), encoding="utf-8"
    )

    under_budget = mod.decide_file_contexts(
        query="overview",
        items=[{"id": "f3", "type": "file", "size": 1200}],
        config={"default_mode": "full", "max_tokens_per_file": 10000, "max_tokens_per_request": 20000, "small_file_tokens": 1200},
    )
    (EVIDENCE_DIR / "task-3-under-budget.json").write_text(
        json.dumps([d.__dict__ for d in under_budget], indent=2, ensure_ascii=False), encoding="utf-8"
    )

    manual = mod.decide_file_contexts(
        query="find section",
        items=[{"id": "manual", "type": "file", "context": "full", "token_count": 900}],
        config=config,
    )
    (EVIDENCE_DIR / "task-4-manual.txt").write_text(
        "\n".join([f"{d.mode}:{d.reason}:{d.estimated_tokens}" for d in manual]), encoding="utf-8"
    )

    ambiguity = mod.decide_file_contexts(
        query="hello there",
        items=[{"id": "a1", "type": "file", "size": 6000}],
        config={"default_mode": "retrieval", "max_tokens_per_file": 8000, "max_tokens_per_request": 32000, "small_file_tokens": 1200},
    )
    (EVIDENCE_DIR / "task-4-ambiguity.txt").write_text(
        "\n".join([f"{d.mode}:{d.reason}:{d.estimated_tokens}" for d in ambiguity]), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
