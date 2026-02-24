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
        TOOL_ROUTING_KEYWORD_FALLBACK_ENABLE = True
        TOOL_ROUTING_KEYWORD_FALLBACK_MODE_PATTERNS = {
            "search": ["search"],
            "analyze": ["analyze"],
            "chat": [],
        }

    cfg = mod.build_tool_routing_config(FakeConfig())

    assert cfg.mode == "hybrid"


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
