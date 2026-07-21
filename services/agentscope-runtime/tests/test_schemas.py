import pytest
from agentscope_runtime.schemas import TextDeltaRequest
from pydantic import ValidationError


def _text_delta_request(**overrides):
    values = {
        "idempotency_key": "text:run-1:block-1:0",
        "run_id": "run-1",
        "block_id": "block-1",
        "block_kind": "assistant_note",
        "delta_index": 0,
        "delta": "public summary",
    }
    values.update(overrides)
    return TextDeltaRequest(**values)


def test_text_delta_schema_rejects_nested_debug_payload():
    with pytest.raises(ValidationError, match="debug"):
        _text_delta_request(
            payload={"debug": {"thinking": "SECRET"}},
        )


def test_text_delta_schema_allows_unrelated_public_payload_fields():
    request = _text_delta_request(
        payload={
            "debug_info": {"visible": True},
            "details": {"status": "ok"},
        }
    )

    assert request.payload["debug_info"]["visible"] is True
