import asyncio
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[4]
MODULE_PATH = ROOT / "backend" / "open_webui" / "utils" / "tool_routing.py"


def _load_module():
    backend_path = str(ROOT / "backend")
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)

    spec = importlib.util.spec_from_file_location("tool_routing_test", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cfg(mod, **overrides):
    base = mod.ToolRoutingConfig(
        enable=True,
        mode="hybrid",
        semantic_top_n=10,
        final_top_k=3,
        min_confidence=0.05,
        lexical_weight=0.7,
        rule_weight=0.3,
        max_injected_tools=3,
        max_schema_chars=10_000,
        bm25_weight=0.45,
        intent_query_enable=True,
        intent_max_clauses=2,
        intent_max_chars=256,
        chat_floor_enable=True,
        chat_floor_min_keep=1,
        keyword_fallback_enable=True,
        mode_patterns={"search": ["search"], "analyze": ["analyze"], "chat": []},
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def _tools():
    return {
        "search_web": {
            "spec": {
                "name": "search_web",
                "description": "Search web pages and retrieve results",
                "parameters": {"properties": {"query": {"type": "string"}}},
            }
        },
        "summarize_text": {
            "spec": {
                "name": "summarize_text",
                "description": "Summarize long text content",
                "parameters": {"properties": {"text": {"type": "string"}}},
            }
        },
    }


def test_route_tools_deterministic_order():
    mod = _load_module()
    tools = _tools()
    decision_a = mod.route_tools("please search web for python", tools, _cfg(mod))
    decision_b = mod.route_tools("please search web for python", tools, _cfg(mod))

    assert decision_a.selected_keys == decision_b.selected_keys
    assert decision_a.scores == decision_b.scores


def test_chat_mode_conservative_selection():
    mod = _load_module()
    tools = _tools()
    decision = mod.route_tools("hello how are you", tools, _cfg(mod, min_confidence=0.0))

    assert len(decision.selected_keys) <= 1


def test_chat_mode_floor_selection_when_threshold_filters_all():
    mod = _load_module()
    tools = _tools()
    decision = mod.route_tools(
        "just chatting",
        tools,
        _cfg(
            mod,
            mode="chat",
            min_confidence=0.95,
            final_top_k=2,
            chat_floor_enable=True,
            chat_floor_min_keep=1,
        ),
    )

    assert decision.fallback_reason == "chat_mode_floor"
    assert len(decision.selected_keys) == 1


def test_chat_mode_without_floor_keeps_empty_selection():
    mod = _load_module()
    tools = _tools()
    decision = mod.route_tools(
        "just chatting",
        tools,
        _cfg(
            mod,
            mode="chat",
            min_confidence=0.95,
            chat_floor_enable=False,
        ),
    )

    assert decision.selected_keys == []


def test_extract_tool_intent_query_prefers_task_clause():
    mod = _load_module()
    prompt = (
        "背景说明：我们今天讨论行程安排。"
        "请帮我搜索上海明天的天气并总结关键点。"
        "补充：我可能下午出门。"
    )
    routing_query, debug = mod.extract_tool_intent_query(prompt, _tools(), _cfg(mod))

    assert "搜索" in routing_query or "总结" in routing_query
    assert debug["reason"] == "ok"
    assert len(debug["selected_clauses"]) >= 1


def test_extract_tool_intent_query_falls_back_when_score_too_low():
    mod = _load_module()
    prompt = "背景信息如下，仅供参考，没有具体任务。"
    routing_query, debug = mod.extract_tool_intent_query(
        prompt, _tools(), _cfg(mod, intent_max_chars=80)
    )

    assert routing_query == "背景信息如下，仅供参考，没有具体任务。"
    assert debug["reason"] == "score_below_threshold"


def test_route_tools_bm25_weight_boundary_changes_order():
    mod = _load_module()
    tools = {
        "a_long": {
            "spec": {
                "name": "a_long",
                "description": "alpha beta extra extra extra extra",
                "parameters": {"properties": {}},
            }
        },
        "b_short": {
            "spec": {
                "name": "b_short",
                "description": "alpha beta",
                "parameters": {"properties": {}},
            }
        },
    }

    query = "alpha beta"
    cfg_no_bm25 = _cfg(
        mod,
        mode="chat",
        min_confidence=0.0,
        final_top_k=1,
        bm25_weight=0.0,
    )
    cfg_full_bm25 = _cfg(
        mod,
        mode="chat",
        min_confidence=0.0,
        final_top_k=1,
        bm25_weight=1.0,
    )

    no_bm25 = mod.route_tools(query, tools, cfg_no_bm25)
    full_bm25 = mod.route_tools(query, tools, cfg_full_bm25)

    assert no_bm25.selected_keys == ["a_long"]
    assert full_bm25.selected_keys == ["b_short"]
    assert full_bm25.score_breakdown["b_short"]["bm25"] > 0


def test_materialize_native_tools_respects_schema_budget():
    mod = _load_module()
    tools = _tools()
    result = mod.materialize_native_tools(tools, max_schema_chars=50)
    assert len(result) <= 1


def test_select_expand_candidate_by_name():
    mod = _load_module()
    dropped = _tools()
    found = mod.select_expand_candidate("search_web", dropped)
    assert found is not None
    assert found[0] == "search_web"


def test_manifest_fingerprint_deterministic():
    mod = _load_module()
    manifests_a = mod.build_tool_manifests(_tools(), scope_id="u1")
    manifests_b = mod.build_tool_manifests(_tools(), scope_id="u1")

    assert manifests_a["search_web"].fingerprint == manifests_b["search_web"].fingerprint
    assert manifests_a["summarize_text"].fingerprint == manifests_b["summarize_text"].fingerprint


def test_manifest_version_changes_on_schema_update():
    mod = _load_module()
    tools = _tools()
    manifests_a = mod.build_tool_manifests(tools, scope_id="u1")
    version_a = mod.compute_manifest_version(manifests_a)

    tools["search_web"]["spec"]["description"] = "Search web pages and fetch citations"
    manifests_b = mod.build_tool_manifests(tools, scope_id="u1")
    version_b = mod.compute_manifest_version(manifests_b)

    assert version_a != version_b


def test_route_tools_prefers_semantic_candidate_set():
    mod = _load_module()
    tools = _tools()

    decision = mod.route_tools(
        "summarize this",
        tools,
        _cfg(mod, min_confidence=0.0),
        semantic_candidates=["summarize_text"],
        semantic_scores={"summarize_text": 1.0},
    )

    assert "summarize_text" in decision.selected_keys


def test_build_tool_routing_config_normalizes_mode():
    mod = _load_module()

    class FakeConfig:
        TOOL_ROUTING_ENABLE = True
        TOOL_ROUTING_MODE = "semantic"
        TOOL_ROUTING_SEMANTIC_TOP_N = 12
        TOOL_ROUTING_FINAL_TOP_K = 6
        TOOL_ROUTING_MIN_CONFIDENCE = 0.12
        TOOL_ROUTING_LEXICAL_WEIGHT = 0.7
        TOOL_ROUTING_RULE_WEIGHT = 0.3
        TOOL_ROUTING_MAX_INJECTED_TOOLS = 6
        TOOL_ROUTING_MAX_SCHEMA_CHARS = 200000
        TOOL_ROUTING_BM25_WEIGHT = 0.45
        TOOL_ROUTING_INTENT_QUERY_ENABLE = True
        TOOL_ROUTING_INTENT_MAX_CLAUSES = 2
        TOOL_ROUTING_INTENT_MAX_CHARS = 256
        TOOL_ROUTING_CHAT_FLOOR_ENABLE = True
        TOOL_ROUTING_CHAT_FLOOR_MIN_KEEP = 1
        TOOL_ROUTING_KEYWORD_FALLBACK_ENABLE = True
        TOOL_ROUTING_KEYWORD_FALLBACK_MODE_PATTERNS = {
            "search": ["search"],
            "analyze": ["analyze"],
            "chat": [],
        }

    cfg = mod.build_tool_routing_config(FakeConfig())

    assert cfg.mode == "hybrid"
    assert cfg.bm25_weight == 0.45
    assert cfg.intent_query_enable is True
    assert cfg.chat_floor_enable is True


def test_route_tools_respects_configured_mode():
    mod = _load_module()
    tools = _tools()
    decision = mod.route_tools(
        "please search web for python",
        tools,
        _cfg(mod, mode="chat", min_confidence=0.0),
    )

    assert decision.mode == "chat"


def test_manifest_id_scope_stable():
    mod = _load_module()
    assert mod.get_manifest_id("u1", "search_web") == "tool::u1::search_web"


def test_select_expand_candidate_matches_function_name_case_insensitive():
    mod = _load_module()
    dropped = {
        "server_search": {
            "spec": {
                "name": "Search_Web",
                "description": "Search over the web",
                "parameters": {"properties": {}},
            }
        }
    }

    found = mod.select_expand_candidate("search_web", dropped)
    assert found is not None
    assert found[0] == "server_search"


def test_bump_tool_routing_manifest_marker_persists_to_redis():
    mod = _load_module()

    class FakeRedis:
        def __init__(self):
            self.calls = []

        async def set(self, key, value):
            self.calls.append((key, value))

    class FakeState:
        def __init__(self):
            self.redis = FakeRedis()
            self.TOOL_ROUTING_MANIFEST_VERSION = 0

    class FakeApp:
        def __init__(self):
            self.state = FakeState()

    class FakeRequest:
        def __init__(self):
            self.app = FakeApp()

    request = FakeRequest()
    marker = asyncio.run(mod.bump_tool_routing_manifest_marker(request))

    assert marker == request.app.state.TOOL_ROUTING_MANIFEST_VERSION
    assert request.app.state.redis.calls == [("tool_routing:manifest_version", marker)]


def test_bump_tool_routing_manifest_marker_handles_redis_failure():
    mod = _load_module()

    class BrokenRedis:
        async def set(self, key, value):
            raise RuntimeError("redis unavailable")

    class FakeState:
        def __init__(self):
            self.redis = BrokenRedis()
            self.TOOL_ROUTING_MANIFEST_VERSION = 0

    class FakeApp:
        def __init__(self):
            self.state = FakeState()

    class FakeRequest:
        def __init__(self):
            self.app = FakeApp()

    request = FakeRequest()
    marker = asyncio.run(mod.bump_tool_routing_manifest_marker(request))

    assert marker == request.app.state.TOOL_ROUTING_MANIFEST_VERSION
    assert marker > 0


def test_bump_tool_routing_manifest_marker_is_monotonic():
    mod = _load_module()

    class FakeRedis:
        async def set(self, key, value):
            return None

    class FakeState:
        def __init__(self):
            self.redis = FakeRedis()
            self.TOOL_ROUTING_MANIFEST_VERSION = 0

    class FakeApp:
        def __init__(self):
            self.state = FakeState()

    class FakeRequest:
        def __init__(self):
            self.app = FakeApp()

    request = FakeRequest()
    first = asyncio.run(mod.bump_tool_routing_manifest_marker(request))
    second = asyncio.run(mod.bump_tool_routing_manifest_marker(request))

    assert second > first


def test_semantic_retrieve_candidates_uses_metadata_key_mapping():
    mod = _load_module()

    class SearchResult:
        def __init__(self):
            self.metadatas = [[{"key": "summarize_text"}, {"key": "search_web"}]]

    class FakeVectorClient:
        def search(self, collection_name, vectors, limit):
            assert collection_name == mod.TOOL_ROUTING_COLLECTION
            assert len(vectors) == 1
            assert limit == 2
            return SearchResult()

    async def fake_embedding(text, prefix=None):
        assert isinstance(text, str)
        return [0.1, 0.2, 0.3]

    manifests = mod.build_tool_manifests(_tools(), scope_id="u1")
    keys, scores = asyncio.run(
        mod.semantic_retrieve_candidates(
            "summarize this page",
            manifests,
            fake_embedding,
            query_prefix="",
            top_n=2,
            vector_client=FakeVectorClient(),
        )
    )

    assert keys == ["summarize_text", "search_web"]
    assert scores["summarize_text"] == 1.0
    assert scores["search_web"] == 0.5


def test_semantic_retrieve_candidates_filters_scope_by_metadata_or_id():
    mod = _load_module()

    class SearchResult:
        def __init__(self):
            self.metadatas = [[{"key": "search_web"}, {"key": "search_web"}]]
            self.ids = [["tool::other::search_web", "tool::u1::search_web"]]

    class FakeVectorClient:
        def search(self, collection_name, vectors, limit):
            return SearchResult()

    async def fake_embedding(text, prefix=None):
        return [0.1, 0.2, 0.3]

    manifests = mod.build_tool_manifests(_tools(), scope_id="u1")
    keys, scores = asyncio.run(
        mod.semantic_retrieve_candidates(
            "search this page",
            manifests,
            fake_embedding,
            query_prefix="",
            top_n=2,
            vector_client=FakeVectorClient(),
            scope_id="u1",
        )
    )

    assert keys == ["search_web"]
    assert scores["search_web"] == 0.5


def test_materialize_native_tools_preserves_ranked_order():
    mod = _load_module()
    tools = {
        "z_top": {
            "spec": {
                "name": "z_top",
                "description": "high-priority",
                "parameters": {"properties": {}},
            }
        },
        "a_low": {
            "spec": {
                "name": "a_low",
                "description": "low-priority",
                "parameters": {"properties": {}},
            }
        },
    }

    payload_size = len(
        json.dumps({"type": "function", "function": tools["z_top"]["spec"]})
    )
    result = mod.materialize_native_tools(tools, max_schema_chars=payload_size)

    assert [item["function"]["name"] for item in result] == ["z_top"]


def test_sync_manifest_index_upserts_dict_items_for_vector_backends():
    mod = _load_module()
    manifests = mod.build_tool_manifests(_tools(), scope_id="u1")

    class FakeVectorClient:
        def __init__(self):
            self.upsert_args = None

        def upsert(self, collection_name, items):
            self.upsert_args = {"collection_name": collection_name, "items": items}

        def delete(self, collection_name, ids):
            return None

    async def fake_embedding(texts, prefix=None):
        assert isinstance(texts, list)
        return [[0.1, 0.2, 0.3] for _ in texts]

    client = FakeVectorClient()
    result = asyncio.run(
        mod.sync_manifest_index(
            manifests=manifests,
            embedding_function=fake_embedding,
            query_prefix="",
            vector_client=client,
            upsert_keys={"search_web"},
        )
    )

    assert result["status"] == "ok"
    assert result["upserted"] == 1
    assert client.upsert_args is not None
    assert client.upsert_args["collection_name"] == mod.TOOL_ROUTING_COLLECTION
    assert isinstance(client.upsert_args["items"][0], dict)
    assert "id" in client.upsert_args["items"][0]
    assert "vector" in client.upsert_args["items"][0]
