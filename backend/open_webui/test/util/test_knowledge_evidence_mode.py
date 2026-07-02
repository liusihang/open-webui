from types import SimpleNamespace

import pytest

from open_webui.models.knowledge import (
    DEFAULT_KNOWLEDGE_EVIDENCE_MODE,
    KNOWLEDGE_EVIDENCE_MODES,
    get_knowledge_evidence_mode,
    normalize_knowledge_evidence_mode,
    set_knowledge_meta_evidence_mode,
)


def test_knowledge_evidence_mode_helpers_default_to_legacy_text():
    assert DEFAULT_KNOWLEDGE_EVIDENCE_MODE == "legacy_text"
    assert KNOWLEDGE_EVIDENCE_MODES == ("legacy_text", "evidence_dual_write", "evidence_primary")
    assert normalize_knowledge_evidence_mode("EVIDENCE_PRIMARY") == "evidence_primary"
    assert get_knowledge_evidence_mode(SimpleNamespace(meta=None)) == "legacy_text"
    assert get_knowledge_evidence_mode({"meta": {"evidence_mode": "evidence_dual_write"}}) == "evidence_dual_write"
    assert get_knowledge_evidence_mode(SimpleNamespace(meta={"evidence_mode": "bad-value"})) == "legacy_text"
    assert set_knowledge_meta_evidence_mode({"source": "kb"}, "evidence_primary") == {
        "source": "kb",
        "evidence_mode": "evidence_primary",
    }


def test_knowledge_evidence_mode_normalizer_rejects_unknown_modes():
    with pytest.raises(ValueError):
        normalize_knowledge_evidence_mode("image_only")
