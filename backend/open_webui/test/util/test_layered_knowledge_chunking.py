import os

os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")

import open_webui.utils.layered_knowledge as layered_mod


def test_estimate_text_tokens_uses_tiktoken(monkeypatch):
    called = {}

    class FakeEncoding:
        def encode(self, text):
            return [1, 2, 3]

    def fake_get_encoding(name):
        called["name"] = name
        return FakeEncoding()

    monkeypatch.setattr(layered_mod.tiktoken, "get_encoding", fake_get_encoding)

    count = layered_mod.estimate_text_tokens("hello world")
    assert count == 3
    assert called["name"] == "cl100k_base"


def test_plan_text_chunks_keeps_small_text_single_source():
    text = "short text"
    chunks = layered_mod.plan_text_chunks(text, max_tokens=24000, min_tail_tokens=1000)
    assert len(chunks) == 1
    assert chunks[0]["content"] == "short text"


def test_plan_text_chunks_splits_when_exceeding_limit():
    paragraph_1 = "a " * 10000
    paragraph_2 = "b " * 10000
    paragraph_3 = "c " * 9000
    text = f"{paragraph_1.strip()}\n\n{paragraph_2.strip()}\n\n{paragraph_3.strip()}"

    chunks = layered_mod.plan_text_chunks(text, max_tokens=24000, min_tail_tokens=1000)
    assert len(chunks) == 2
    assert chunks[0]["token_count"] <= 24000
    assert chunks[1]["token_count"] <= 24000
    assert "a " in chunks[0]["content"]
    assert "b " in chunks[0]["content"]
    assert "c " in chunks[1]["content"]


def test_plan_text_chunks_drops_small_trailing_chunk_by_requirement():
    paragraph_1 = "a " * 23800
    paragraph_2 = "b " * 900
    text = f"{paragraph_1.strip()}\n\n{paragraph_2.strip()}"

    chunks = layered_mod.plan_text_chunks(text, max_tokens=24000, min_tail_tokens=1000)
    assert len(chunks) == 1
    assert "a " in chunks[0]["content"]
    assert "b " not in chunks[0]["content"]


def test_plan_text_chunks_prefers_paragraph_boundaries():
    paragraph_1 = "a " * 6
    paragraph_2 = "b " * 6
    paragraph_3 = "c " * 6
    text = f"{paragraph_1.strip()}\n\n{paragraph_2.strip()}\n\n{paragraph_3.strip()}"

    chunks = layered_mod.plan_text_chunks(text, max_tokens=10, min_tail_tokens=1)
    assert len(chunks) == 3
    assert chunks[0]["content"] == paragraph_1.strip()
    assert chunks[1]["content"] == paragraph_2.strip()
    assert chunks[2]["content"] == paragraph_3.strip()
