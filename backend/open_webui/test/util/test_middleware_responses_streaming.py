import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
MIDDLEWARE_PATH = ROOT / "backend" / "open_webui" / "utils" / "middleware.py"


def _load_handler():
    source = MIDDLEWARE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    wanted = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in {
            "deep_merge",
            "merge_output_item_preserve_content",
            "merge_final_output_preserve_content",
            "handle_responses_streaming_event",
        }:
            wanted[node.name] = node

    required = {
        "deep_merge",
        "merge_output_item_preserve_content",
        "merge_final_output_preserve_content",
        "handle_responses_streaming_event",
    }
    if not required.issubset(wanted):
        raise RuntimeError("Required middleware functions not found")

    module_ast = ast.Module(
        body=[
            wanted["deep_merge"],
            wanted["merge_output_item_preserve_content"],
            wanted["merge_final_output_preserve_content"],
            wanted["handle_responses_streaming_event"],
        ],
        type_ignores=[],
    )
    ast.fix_missing_locations(module_ast)

    namespace = {
        "Any": object,
        "_normalize_reasoning_item": lambda item: item,
        "_clone_output_item": lambda item: dict(item) if isinstance(item, dict) else item,
        "normalize_reasoning_output_items": lambda output: output,
    }
    exec(compile(module_ast, str(MIDDLEWARE_PATH), "exec"), namespace)
    return namespace["handle_responses_streaming_event"]


def test_response_completed_keeps_streamed_output_when_final_output_empty() -> None:
    handler = _load_handler()
    current_output = [
        {
            "type": "message",
            "status": "in_progress",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "hello from stream"}],
        }
    ]
    event = {
        "type": "response.completed",
        "response": {"output": [], "usage": {"total_tokens": 7}},
    }

    new_output, metadata = handler(event, current_output)

    assert new_output == current_output
    assert metadata == {"usage": {"total_tokens": 7}, "done": True}


def test_response_completed_uses_non_empty_final_output() -> None:
    handler = _load_handler()
    current_output = [
        {
            "type": "message",
            "status": "in_progress",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "partial"}],
        }
    ]
    final_output = [
        {
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "final"}],
        }
    ]
    event = {
        "type": "response.completed",
        "response": {"output": final_output, "usage": {"total_tokens": 12}},
    }

    new_output, metadata = handler(event, current_output)

    assert new_output == final_output
    assert metadata == {"usage": {"total_tokens": 12}, "done": True}
