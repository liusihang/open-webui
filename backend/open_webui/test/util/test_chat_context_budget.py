import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Optional


ROOT = Path(__file__).resolve().parents[4]
BACKEND_ROOT = ROOT / "backend"
MODULE_PATH = BACKEND_ROOT / "open_webui" / "utils" / "chat_context_budget.py"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _load_module():
    spec = importlib.util.spec_from_file_location("chat_context_budget_test", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_request(config_overrides: Optional[dict] = None):
    config = SimpleNamespace(
        TIKTOKEN_ENCODING_NAME="cl100k_base",
        CHAT_CONTEXT_BUDGET_ENABLED=True,
        CHAT_CONTEXT_NON_SYSTEM_MAX_TOKENS=32000,
        CHAT_CONTEXT_WINDOW_ROUNDS=5,
        CHAT_CONTEXT_COMPACTION_TRIGGER_TOKENS=50000,
        CHAT_CONTEXT_TOOL_OUTPUT_MAX_TOKENS=1800,
        CHAT_CONTEXT_TOOL_OUTPUT_MAX_CHARS=6000,
        CHAT_CONTEXT_ALLOW_TEMP_OVERFLOW=True,
        TASK_MODEL="",
        TASK_MODEL_EXTERNAL="",
    )

    for key, value in (config_overrides or {}).items():
        setattr(config, key, value)

    app_state = SimpleNamespace(
        config=config,
        MODELS={
            "test-model": {
                "id": "test-model",
                "owned_by": "openai",
                "info": {"params": {}},
            }
        },
    )

    return SimpleNamespace(
        app=SimpleNamespace(state=app_state),
        state=SimpleNamespace(direct=False, metadata={"chat_id": "test-chat"}),
    )


def _run(coro):
    return asyncio.run(coro)


def test_recent_round_window_keeps_last_5_user_turns() -> None:
    mod = _load_module()

    messages = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
        {"role": "assistant", "content": "a3"},
        {"role": "user", "content": "u4"},
        {"role": "assistant", "content": "a4"},
        {"role": "user", "content": "u5"},
        {"role": "assistant", "content": "a5"},
        {"role": "user", "content": "u6"},
        {"role": "assistant", "content": "a6"},
    ]

    window = mod.select_recent_round_window(messages, rounds=5)

    assert window[0]["role"] == "user"
    assert window[0]["content"] == "u2"
    assert window[-1]["content"] == "a6"
    assert sum(1 for msg in window if msg.get("role") == "user") == 5


def test_compaction_triggered_over_50k_inserts_summary() -> None:
    mod = _load_module()
    request = _make_request()

    big = "x" * 12000
    messages = []
    for i in range(1, 8):
        messages.append({"role": "user", "content": f"u{i}:{big}"})
        messages.append({"role": "assistant", "content": f"a{i}:{big}"})

    form_data = {
        "model": "test-model",
        "messages": messages,
    }

    async def fake_summarizer(**kwargs):
        return "SYNTH SUMMARY"

    updated_form_data, diagnostics = _run(
        mod.apply_context_budget_policy(
            request=request,
            form_data=form_data,
            user=SimpleNamespace(role="user"),
            models=request.app.state.MODELS,
            summarizer=fake_summarizer,
        )
    )

    non_system = [msg for msg in updated_form_data["messages"] if msg.get("role") != "system"]

    assert diagnostics["compaction_applied"] is True
    assert non_system[0]["role"] == "assistant"
    assert "SYNTH SUMMARY" in non_system[0]["content"]
    assert sum(1 for msg in non_system if msg.get("role") == "user") == 5


def test_tool_output_medium_compression_json() -> None:
    mod = _load_module()

    content = json.dumps(
        {
            "status": "ok",
            "results": [
                {"id": i, "text": "A" * 1800, "extra": {"debug": "B" * 500}}
                for i in range(20)
            ],
            "metadata": {"trace": "C" * 4000},
        },
        ensure_ascii=False,
    )

    compressed = mod.compress_tool_message_content(
        content,
        level="medium",
        max_tokens=1800,
        max_chars=6000,
        encoding_name="cl100k_base",
    )

    assert len(compressed) <= 6000
    assert "_truncated_items" in compressed or "...[truncated]..." in compressed


def test_tool_output_medium_compression_text() -> None:
    mod = _load_module()

    text = "\n".join(
        [
            f"line {i}: normal output {'x' * 120}"
            if i % 15
            else f"line {i}: ERROR critical failure {'y' * 200}"
            for i in range(600)
        ]
    )

    compressed = mod.compress_tool_message_content(
        text,
        level="medium",
        max_tokens=1800,
        max_chars=6000,
        encoding_name="cl100k_base",
    )

    assert len(compressed) <= 6000
    assert "...[truncated]..." in compressed


def test_budget_target_32k_with_five_round_priority() -> None:
    mod = _load_module()
    request = _make_request()

    huge = "U" * 25000
    messages = []
    for i in range(1, 6):
        messages.append({"role": "user", "content": f"user-{i}:{huge}"})
        messages.append({"role": "assistant", "content": f"assistant-{i}"})

    form_data = {
        "model": "test-model",
        "messages": messages,
    }

    updated_form_data, diagnostics = _run(
        mod.apply_context_budget_policy(
            request=request,
            form_data=form_data,
            user=SimpleNamespace(role="user"),
        )
    )

    non_system = [msg for msg in updated_form_data["messages"] if msg.get("role") != "system"]
    users = [msg for msg in non_system if msg.get("role") == "user"]

    assert diagnostics["overflow"] is True
    assert len(users) == 5
    assert users[-1]["content"].startswith("user-5:")


def test_recursive_tool_call_payload_is_budgeted() -> None:
    mod = _load_module()
    request = _make_request()

    tool_payload = json.dumps(
        {
            "status": "ok",
            "output": "Z" * 24000,
            "items": [{"idx": i, "value": "K" * 1000} for i in range(15)],
        },
        ensure_ascii=False,
    )

    form_data = {
        "model": "test-model",
        "messages": [
            {"role": "user", "content": "Analyze this tool output"},
            {"role": "assistant", "content": "Understood, processing now"},
            {"role": "tool", "tool_call_id": "call_1", "content": tool_payload},
            {"role": "assistant", "content": "Continuing analysis"},
        ],
    }

    updated_form_data, diagnostics = _run(
        mod.apply_context_budget_policy(
            request=request,
            form_data=form_data,
            user=SimpleNamespace(role="user"),
        )
    )

    tool_messages = [msg for msg in updated_form_data["messages"] if msg.get("role") == "tool"]

    assert diagnostics["applied"] is True
    assert len(tool_messages) == 1
    assert len(tool_messages[0].get("content", "")) <= 6000


def test_budget_policy_can_be_disabled() -> None:
    mod = _load_module()
    request = _make_request({"CHAT_CONTEXT_BUDGET_ENABLED": False})

    original_messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]

    updated_form_data, diagnostics = _run(
        mod.apply_context_budget_policy(
            request=request,
            form_data={"model": "test-model", "messages": list(original_messages)},
            user=SimpleNamespace(role="user"),
        )
    )

    assert diagnostics["applied"] is False
    assert diagnostics["reason"] == "disabled"
    assert updated_form_data["messages"] == original_messages
