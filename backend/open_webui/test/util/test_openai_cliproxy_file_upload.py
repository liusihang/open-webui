import base64
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[4]
OPENAI_ROUTER_PATH = ROOT / "backend" / "open_webui" / "routers" / "openai.py"
_MISSING = object()


def _load_openai_module_with_stubs():
    originals: dict[str, object] = {}

    def register(name: str, module: types.ModuleType) -> None:
        if name not in originals:
            originals[name] = sys.modules.get(name, _MISSING)
        sys.modules[name] = module

    def package(name: str) -> types.ModuleType:
        mod = types.ModuleType(name)
        mod.__path__ = []  # type: ignore[attr-defined]
        return mod

    try:
        # External dependencies
        aiohttp_mod = types.ModuleType("aiohttp")

        class ClientTimeout:
            def __init__(self, total=None):
                self.total = total

        class ClientSession:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def request(self, *args, **kwargs):
                raise RuntimeError("Stub ClientSession.request should be monkeypatched")

            async def get(self, *args, **kwargs):
                raise RuntimeError("Stub ClientSession.get should be monkeypatched")

        aiohttp_mod.ClientTimeout = ClientTimeout
        aiohttp_mod.ClientSession = ClientSession
        aiohttp_mod.ClientError = Exception
        register("aiohttp", aiohttp_mod)

        aiocache_mod = types.ModuleType("aiocache")

        def cached(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

        aiocache_mod.cached = cached
        register("aiocache", aiocache_mod)
        register("requests", types.ModuleType("requests"))

        azure_mod = package("azure")
        azure_identity_mod = types.ModuleType("azure.identity")

        class DefaultAzureCredential:
            pass

        def get_bearer_token_provider(*args, **kwargs):
            return lambda: "token"

        azure_identity_mod.DefaultAzureCredential = DefaultAzureCredential
        azure_identity_mod.get_bearer_token_provider = get_bearer_token_provider
        register("azure", azure_mod)
        register("azure.identity", azure_identity_mod)

        fastapi_mod = types.ModuleType("fastapi")

        class HTTPException(Exception):
            def __init__(self, status_code=None, detail=None):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        def Depends(value):
            return value

        class Request:
            pass

        class APIRouter:
            def _decorator(self, *args, **kwargs):
                def wrapped(func):
                    return func

                return wrapped

            get = post = put = patch = delete = options = head = api_route = _decorator

        fastapi_mod.Depends = Depends
        fastapi_mod.HTTPException = HTTPException
        fastapi_mod.Request = Request
        fastapi_mod.APIRouter = APIRouter
        register("fastapi", fastapi_mod)

        fastapi_responses_mod = types.ModuleType("fastapi.responses")

        class ResponseBase:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

        fastapi_responses_mod.FileResponse = ResponseBase
        fastapi_responses_mod.StreamingResponse = ResponseBase
        fastapi_responses_mod.JSONResponse = ResponseBase
        fastapi_responses_mod.PlainTextResponse = ResponseBase
        register("fastapi.responses", fastapi_responses_mod)

        pydantic_mod = types.ModuleType("pydantic")

        class BaseModel:
            def __init__(self, **kwargs):
                for key, value in kwargs.items():
                    setattr(self, key, value)

            def model_dump(self, *args, **kwargs):
                return dict(self.__dict__)

        def ConfigDict(**kwargs):
            return kwargs

        pydantic_mod.BaseModel = BaseModel
        pydantic_mod.ConfigDict = ConfigDict
        register("pydantic", pydantic_mod)

        sqlalchemy_mod = package("sqlalchemy")
        sqlalchemy_orm_mod = types.ModuleType("sqlalchemy.orm")

        class Session:
            pass

        sqlalchemy_orm_mod.Session = Session
        register("sqlalchemy", sqlalchemy_mod)
        register("sqlalchemy.orm", sqlalchemy_orm_mod)

        # Internal modules
        register("open_webui", package("open_webui"))
        register("open_webui.internal", package("open_webui.internal"))
        register("open_webui.internal.db", types.ModuleType("open_webui.internal.db"))
        sys.modules["open_webui.internal.db"].get_session = lambda: None

        register("open_webui.models", package("open_webui.models"))
        models_models_mod = types.ModuleType("open_webui.models.models")

        class Models:
            @staticmethod
            def get_model_by_id(model_id):
                return None

        models_models_mod.Models = Models
        register("open_webui.models.models", models_models_mod)

        access_grants_mod = types.ModuleType("open_webui.models.access_grants")

        class AccessGrants:
            @staticmethod
            def has_access(*args, **kwargs):
                return False

        access_grants_mod.AccessGrants = AccessGrants
        register("open_webui.models.access_grants", access_grants_mod)

        files_mod = types.ModuleType("open_webui.models.files")

        class Files:
            @staticmethod
            def get_file_by_id(file_id):
                return None

        files_mod.Files = Files
        register("open_webui.models.files", files_mod)

        groups_mod = types.ModuleType("open_webui.models.groups")

        class Groups:
            @staticmethod
            def get_groups_by_member_id(_user_id):
                return []

        groups_mod.Groups = Groups
        register("open_webui.models.groups", groups_mod)

        users_mod = types.ModuleType("open_webui.models.users")

        class UserModel:
            pass

        users_mod.UserModel = UserModel
        register("open_webui.models.users", users_mod)

        config_mod = types.ModuleType("open_webui.config")
        config_mod.CACHE_DIR = "/tmp"
        register("open_webui.config", config_mod)

        env_mod = types.ModuleType("open_webui.env")
        env_mod.MODELS_CACHE_TTL = 0
        env_mod.AIOHTTP_CLIENT_SESSION_SSL = False
        env_mod.AIOHTTP_CLIENT_TIMEOUT = 30
        env_mod.AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST = 30
        env_mod.ENABLE_FORWARD_USER_INFO_HEADERS = False
        env_mod.FORWARD_SESSION_INFO_HEADER_CHAT_ID = "x-chat-id"
        env_mod.BYPASS_MODEL_ACCESS_CONTROL = False
        register("open_webui.env", env_mod)

        constants_mod = types.ModuleType("open_webui.constants")
        constants_mod.ERROR_MESSAGES = types.SimpleNamespace(OPENAI_NOT_FOUND="OPENAI_NOT_FOUND")
        register("open_webui.constants", constants_mod)

        register("open_webui.utils", package("open_webui.utils"))

        payload_mod = types.ModuleType("open_webui.utils.payload")
        payload_mod.apply_model_params_to_body_openai = lambda params, payload: payload
        payload_mod.apply_system_prompt_to_body = (
            lambda system, payload, metadata, user: payload
        )
        register("open_webui.utils.payload", payload_mod)

        misc_mod = types.ModuleType("open_webui.utils.misc")

        async def cleanup_response(_r, _session):
            return None

        misc_mod.cleanup_response = cleanup_response
        misc_mod.convert_logit_bias_input_to_json = lambda value: None
        misc_mod.stream_chunks_handler = lambda *args, **kwargs: None
        misc_mod.stream_wrapper = lambda *args, **kwargs: None
        register("open_webui.utils.misc", misc_mod)

        auth_mod = types.ModuleType("open_webui.utils.auth")
        auth_mod.get_admin_user = lambda: None
        auth_mod.get_verified_user = lambda: None
        register("open_webui.utils.auth", auth_mod)

        headers_mod = types.ModuleType("open_webui.utils.headers")
        headers_mod.include_user_info_headers = lambda headers, user: headers
        register("open_webui.utils.headers", headers_mod)

        anthropic_mod = types.ModuleType("open_webui.utils.anthropic")
        anthropic_mod.is_anthropic_url = lambda url: False

        async def get_anthropic_models(url, key, user=None):
            return {"data": []}

        anthropic_mod.get_anthropic_models = get_anthropic_models
        register("open_webui.utils.anthropic", anthropic_mod)

        register("open_webui.storage", package("open_webui.storage"))
        storage_provider_mod = types.ModuleType("open_webui.storage.provider")

        class Storage:
            @staticmethod
            def get_file(path):
                return path

        storage_provider_mod.Storage = Storage
        register("open_webui.storage.provider", storage_provider_mod)

        spec = importlib.util.spec_from_file_location(
            f"openai_router_cliproxy_test_{id(object())}",
            OPENAI_ROUTER_PATH,
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load module: {OPENAI_ROUTER_PATH}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, original in originals.items():
            if original is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


@pytest.fixture()
def openai_module():
    return _load_openai_module_with_stubs()


def _build_request(cliproxy_api: bool):
    config = types.SimpleNamespace(
        OPENAI_API_CONFIGS={"0": {"cliproxy_api": cliproxy_api}},
        OPENAI_API_BASE_URLS=["https://example.com/v1"],
        OPENAI_API_KEYS=["test-key"],
    )
    state = types.SimpleNamespace(
        config=config,
        OPENAI_MODELS={"gpt-4o": {"urlIdx": 0}},
    )
    return types.SimpleNamespace(app=types.SimpleNamespace(state=state))


class _FakeResponse:
    status = 200
    headers = {"Content-Type": "application/json"}

    async def json(self):
        return {"ok": True}

    async def text(self):
        return '{"ok":true}'


class _FakeSession:
    def __init__(self, captured_calls: list[dict]):
        self.captured_calls = captured_calls

    async def request(self, **kwargs):
        self.captured_calls.append(kwargs)
        return _FakeResponse()


@pytest.mark.asyncio
async def test_generate_chat_completion_skips_injection_when_toggle_disabled(
    openai_module, monkeypatch
):
    request = _build_request(cliproxy_api=False)
    user = types.SimpleNamespace(id="u1", role="admin", name="Admin", email="admin@test")
    form_data = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hello"}],
        "metadata": {"parent_message": {"files": [{"id": "file-1"}]}},
    }

    injected = {"called": False}

    def fake_inject(payload, metadata, current_user):
        injected["called"] = True
        payload["messages"][0]["content"] = [
            {"type": "text", "text": "hello"},
            {"type": "file", "file": {"filename": "x.txt", "file_data": "abc"}},
        ]
        return payload

    async def fake_headers_and_cookies(*args, **kwargs):
        return {}, {}

    async def fake_cleanup(*args, **kwargs):
        return None

    captured_calls = []
    monkeypatch.setattr(openai_module, "_inject_cliproxy_files_into_payload", fake_inject)
    monkeypatch.setattr(openai_module, "get_headers_and_cookies", fake_headers_and_cookies)
    monkeypatch.setattr(openai_module, "cleanup_response", fake_cleanup)
    monkeypatch.setattr(
        openai_module.aiohttp,
        "ClientSession",
        lambda *args, **kwargs: _FakeSession(captured_calls),
    )

    result = await openai_module.generate_chat_completion(
        request=request,
        form_data=form_data,
        user=user,
        bypass_filter=True,
    )

    assert result == {"ok": True}
    assert injected["called"] is False
    sent_payload = json.loads(captured_calls[0]["data"])
    assert sent_payload["messages"] == [{"role": "user", "content": "hello"}]


def test_build_cliproxy_file_parts_adds_doc_audio_video_and_skips_image(
    openai_module, monkeypatch, tmp_path
):
    pdf_path = tmp_path / "doc.pdf"
    mp3_path = tmp_path / "voice.mp3"
    mp4_path = tmp_path / "video.mp4"
    png_path = tmp_path / "image.png"
    pdf_path.write_bytes(b"pdf-binary")
    mp3_path.write_bytes(b"audio-binary")
    mp4_path.write_bytes(b"video-binary")
    png_path.write_bytes(b"image-binary")

    file_map = {
        "doc": types.SimpleNamespace(
            meta={"name": "proposal.pdf", "content_type": "application/pdf"},
            filename="doc.pdf",
            user_id="u1",
            path=str(pdf_path),
        ),
        "audio": types.SimpleNamespace(
            meta={"name": "recording.mp3", "content_type": "audio/mpeg"},
            filename="voice.mp3",
            user_id="u1",
            path=str(mp3_path),
        ),
        "video": types.SimpleNamespace(
            meta={"name": "demo.mp4", "content_type": "video/mp4"},
            filename="video.mp4",
            user_id="u1",
            path=str(mp4_path),
        ),
        "image": types.SimpleNamespace(
            meta={"name": "screen.png", "content_type": "image/png"},
            filename="image.png",
            user_id="u1",
            path=str(png_path),
        ),
    }

    payload = {"model": "gpt-4o"}
    metadata = {
        "parent_message": {
            "files": [
                {"id": "doc", "content_type": "application/pdf"},
                {"id": "audio", "content_type": "audio/mpeg"},
                {"id": "video", "content_type": "video/mp4"},
                {"id": "image", "type": "image", "content_type": "image/png"},
            ]
        }
    }
    user = types.SimpleNamespace(id="u1", role="user")

    monkeypatch.setattr(openai_module.Files, "get_file_by_id", lambda file_id: file_map.get(file_id))
    monkeypatch.setattr(openai_module.Storage, "get_file", lambda path: path)
    monkeypatch.setattr(openai_module.AccessGrants, "has_access", lambda **kwargs: True)

    parts = openai_module._build_cliproxy_file_parts(payload, metadata, user)

    assert len(parts) == 3
    assert [part["file"]["filename"] for part in parts] == [
        "proposal.pdf",
        "recording.mp3",
        "demo.mp4",
    ]
    assert parts[0]["file"]["file_data"] == base64.b64encode(pdf_path.read_bytes()).decode("utf-8")
    assert parts[1]["file"]["file_data"] == base64.b64encode(mp3_path.read_bytes()).decode("utf-8")
    assert parts[2]["file"]["file_data"] == base64.b64encode(mp4_path.read_bytes()).decode("utf-8")


def test_build_cliproxy_file_parts_uses_data_url_for_claude(openai_module, monkeypatch, tmp_path):
    source = tmp_path / "note.txt"
    source.write_bytes(b"hello-cliproxy")
    file_obj = types.SimpleNamespace(
        meta={"name": "note.txt", "content_type": "text/plain"},
        filename="note.txt",
        user_id="u1",
        path=str(source),
    )

    metadata = {"parent_message": {"files": [{"id": "f1", "content_type": "text/plain"}]}}
    user = types.SimpleNamespace(id="u1", role="user")

    monkeypatch.setattr(openai_module.Files, "get_file_by_id", lambda file_id: file_obj)
    monkeypatch.setattr(openai_module.Storage, "get_file", lambda path: path)
    monkeypatch.setattr(openai_module.AccessGrants, "has_access", lambda **kwargs: True)

    claude_parts = openai_module._build_cliproxy_file_parts(
        {"model": "claude-3-5-sonnet"},
        metadata,
        user,
    )
    gpt_parts = openai_module._build_cliproxy_file_parts(
        {"model": "gpt-4o"},
        metadata,
        user,
    )

    assert claude_parts[0]["file"]["file_data"].startswith("data:text/plain;base64,")
    assert not gpt_parts[0]["file"]["file_data"].startswith("data:")


def test_convert_to_responses_payload_maps_file_part_to_input_file(openai_module):
    payload = {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": "system prompt"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "image_url", "image_url": {"url": "https://img.local/a.png"}},
                    {
                        "type": "file",
                        "file": {
                            "filename": "doc.pdf",
                            "file_data": "BASE64_PAYLOAD",
                        },
                    },
                ],
            },
        ],
    }

    converted = openai_module.convert_to_responses_payload(payload)
    content = converted["input"][0]["content"]

    assert converted["instructions"] == "system prompt"
    assert content[0] == {"type": "input_text", "text": "hello"}
    assert content[1] == {"type": "input_image", "image_url": "https://img.local/a.png"}
    assert content[2] == {
        "type": "input_file",
        "file_data": "BASE64_PAYLOAD",
        "filename": "doc.pdf",
    }


@pytest.mark.asyncio
async def test_generate_chat_completion_ignores_inaccessible_files_and_succeeds(
    openai_module, monkeypatch, tmp_path
):
    request = _build_request(cliproxy_api=True)
    user = types.SimpleNamespace(id="u1", role="user", name="User", email="user@test")
    form_data = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hello"}],
        "metadata": {"parent_message": {"files": [{"id": "private-file"}]}},
    }

    private_source = tmp_path / "private.pdf"
    private_source.write_bytes(b"private")
    private_file = types.SimpleNamespace(
        meta={"name": "private.pdf", "content_type": "application/pdf"},
        filename="private.pdf",
        user_id="another-user",
        path=str(private_source),
    )

    async def fake_headers_and_cookies(*args, **kwargs):
        return {}, {}

    async def fake_cleanup(*args, **kwargs):
        return None

    captured_calls = []
    monkeypatch.setattr(openai_module, "get_headers_and_cookies", fake_headers_and_cookies)
    monkeypatch.setattr(openai_module, "cleanup_response", fake_cleanup)
    monkeypatch.setattr(openai_module.Files, "get_file_by_id", lambda file_id: private_file)
    monkeypatch.setattr(openai_module.AccessGrants, "has_access", lambda **kwargs: False)
    monkeypatch.setattr(
        openai_module.Storage,
        "get_file",
        lambda path: (_ for _ in ()).throw(RuntimeError("Storage should not be called")),
    )
    monkeypatch.setattr(
        openai_module.aiohttp,
        "ClientSession",
        lambda *args, **kwargs: _FakeSession(captured_calls),
    )

    result = await openai_module.generate_chat_completion(
        request=request,
        form_data=form_data,
        user=user,
        bypass_filter=True,
    )

    assert result == {"ok": True}
    sent_payload = json.loads(captured_calls[0]["data"])
    assert sent_payload["messages"] == [{"role": "user", "content": "hello"}]
