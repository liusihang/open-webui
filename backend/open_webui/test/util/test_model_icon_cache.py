import importlib.util
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
MODELS_ROUTER_PATH = ROOT / "backend" / "open_webui" / "routers" / "models.py"
_MISSING = object()


def _load_models_router_with_stubs():
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
        register("open_webui", package("open_webui"))
        register("open_webui.models", package("open_webui.models"))

        groups_mod = types.ModuleType("open_webui.models.groups")

        class Groups:
            @staticmethod
            def get_groups_by_member_id(*args, **kwargs):
                return []

        groups_mod.Groups = Groups
        register("open_webui.models.groups", groups_mod)

        models_mod = types.ModuleType("open_webui.models.models")

        class _Base:
            def __init__(self, **kwargs):
                for key, value in kwargs.items():
                    setattr(self, key, value)

            def model_dump(self):
                return dict(self.__dict__)

        class ModelForm(_Base):
            pass

        class ModelMeta(_Base):
            pass

        class ModelModel(_Base):
            pass

        class ModelParams(_Base):
            pass

        class ModelResponse(_Base):
            pass

        class ModelListResponse(_Base):
            pass

        class ModelAccessListResponse(_Base):
            pass

        class ModelAccessResponse(_Base):
            pass

        class Models:
            @staticmethod
            def get_model_by_id(*args, **kwargs):
                return None

            @staticmethod
            def update_model_by_id(*args, **kwargs):
                return None

            @staticmethod
            def insert_new_model(*args, **kwargs):
                return None

            @staticmethod
            def toggle_model_by_id(*args, **kwargs):
                return None

            @staticmethod
            def search_models(*args, **kwargs):
                return types.SimpleNamespace(items=[])

        models_mod.ModelForm = ModelForm
        models_mod.ModelMeta = ModelMeta
        models_mod.ModelModel = ModelModel
        models_mod.ModelParams = ModelParams
        models_mod.ModelResponse = ModelResponse
        models_mod.ModelListResponse = ModelListResponse
        models_mod.ModelAccessListResponse = ModelAccessListResponse
        models_mod.ModelAccessResponse = ModelAccessResponse
        models_mod.Models = Models
        register("open_webui.models.models", models_mod)

        access_grants_mod = types.ModuleType("open_webui.models.access_grants")

        class AccessGrants:
            @staticmethod
            def has_access(*args, **kwargs):
                return False

            @staticmethod
            def get_accessible_resource_ids(*args, **kwargs):
                return []

        access_grants_mod.AccessGrants = AccessGrants
        access_grants_mod.has_public_read_access_grant = lambda *args, **kwargs: False
        register("open_webui.models.access_grants", access_grants_mod)

        constants_mod = types.ModuleType("open_webui.constants")
        constants_mod.ERROR_MESSAGES = types.SimpleNamespace(
            ACCESS_PROHIBITED="ACCESS_PROHIBITED",
            UNAUTHORIZED="UNAUTHORIZED",
            NOT_FOUND="NOT_FOUND",
            DEFAULT=lambda _msg: "DEFAULT",
        )
        register("open_webui.constants", constants_mod)

        pydantic_mod = types.ModuleType("pydantic")

        class BaseModel:
            def __init__(self, **kwargs):
                for key, value in kwargs.items():
                    setattr(self, key, value)

            def model_dump(self, *args, **kwargs):
                return dict(self.__dict__)

        def Field(default=None, **kwargs):
            return default

        pydantic_mod.BaseModel = BaseModel
        pydantic_mod.Field = Field
        register("pydantic", pydantic_mod)

        fastapi_mod = types.ModuleType("fastapi")

        class HTTPException(Exception):
            def __init__(self, status_code=None, detail=None):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        class APIRouter:
            def _decorator(self, *args, **kwargs):
                def wrapped(func):
                    return func

                return wrapped

            get = post = put = patch = delete = options = head = api_route = _decorator

        def Depends(value):
            return value

        class Request:
            pass

        class Response:
            def __init__(self, status_code=200, headers=None):
                self.status_code = status_code
                self.headers = headers or {}

        status_mod = types.SimpleNamespace(
            HTTP_200_OK=200,
            HTTP_302_FOUND=302,
            HTTP_400_BAD_REQUEST=400,
            HTTP_401_UNAUTHORIZED=401,
            HTTP_404_NOT_FOUND=404,
            HTTP_500_INTERNAL_SERVER_ERROR=500,
        )

        fastapi_mod.APIRouter = APIRouter
        fastapi_mod.Depends = Depends
        fastapi_mod.HTTPException = HTTPException
        fastapi_mod.Request = Request
        fastapi_mod.status = status_mod
        fastapi_mod.Response = Response
        register("fastapi", fastapi_mod)

        fastapi_responses_mod = types.ModuleType("fastapi.responses")

        class FileResponse:
            def __init__(self, path, headers=None):
                self.path = path
                self.headers = headers or {}

        class StreamingResponse:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

        fastapi_responses_mod.FileResponse = FileResponse
        fastapi_responses_mod.StreamingResponse = StreamingResponse
        register("fastapi.responses", fastapi_responses_mod)

        register("open_webui.utils", package("open_webui.utils"))
        auth_mod = types.ModuleType("open_webui.utils.auth")
        auth_mod.get_admin_user = lambda: None
        auth_mod.get_verified_user = lambda: None
        register("open_webui.utils.auth", auth_mod)

        access_control_mod = types.ModuleType("open_webui.utils.access_control")
        access_control_mod.has_permission = lambda *args, **kwargs: True
        register("open_webui.utils.access_control", access_control_mod)

        config_mod = types.ModuleType("open_webui.config")
        config_mod.BYPASS_ADMIN_ACCESS_CONTROL = False
        config_mod.STATIC_DIR = "/tmp"
        config_mod.CACHE_DIR = Path("/tmp")
        register("open_webui.config", config_mod)

        register("open_webui.internal", package("open_webui.internal"))
        internal_db_mod = types.ModuleType("open_webui.internal.db")
        internal_db_mod.get_session = lambda: None
        register("open_webui.internal.db", internal_db_mod)

        sqlalchemy_mod = package("sqlalchemy")
        sqlalchemy_orm_mod = types.ModuleType("sqlalchemy.orm")

        class Session:
            pass

        sqlalchemy_orm_mod.Session = Session
        register("sqlalchemy", sqlalchemy_mod)
        register("sqlalchemy.orm", sqlalchemy_orm_mod)

        spec = importlib.util.spec_from_file_location(
            f"models_router_icon_cache_test_{id(object())}",
            MODELS_ROUTER_PATH,
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load module: {MODELS_ROUTER_PATH}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, original in originals.items():
            if original is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


class _FakeResponse:
    def __init__(self, status_code=200, content_type="image/png", chunks=None):
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        self._chunks = chunks if chunks is not None else [b"icon-data"]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def iter_content(self, chunk_size=8192):
        for chunk in self._chunks:
            yield chunk


def test_cache_remote_model_icon_downloads_once_and_reuses(tmp_path):
    module = _load_models_router_with_stubs()
    module.MODEL_ICON_CACHE_DIR = tmp_path
    module._is_safe_remote_icon_url = lambda _url: True

    calls = {"count": 0}

    def fake_get(url, timeout, stream):
        calls["count"] += 1
        return _FakeResponse(status_code=200, content_type="image/png", chunks=[b"abc"])

    module.requests.get = fake_get

    first = module._cache_remote_model_icon("m1", "https://example.com/icon.png")
    assert first is not None
    assert Path(first).is_file()
    assert Path(first).read_bytes() == b"abc"
    assert calls["count"] == 1

    def fail_get(*args, **kwargs):
        raise AssertionError("requests.get should not be called when cache exists")

    module.requests.get = fail_get
    second = module._cache_remote_model_icon("m1", "https://example.com/icon.png")
    assert second == first


def test_cache_remote_model_icon_rejects_non_image_content_type(tmp_path):
    module = _load_models_router_with_stubs()
    module.MODEL_ICON_CACHE_DIR = tmp_path
    module._is_safe_remote_icon_url = lambda _url: True
    module.requests.get = (
        lambda url, timeout, stream: _FakeResponse(
            status_code=200, content_type="text/html", chunks=[b"<html></html>"]
        )
    )

    cached = module._cache_remote_model_icon("m2", "https://example.com/not-image")
    assert cached is None
    assert list(tmp_path.iterdir()) == []


def test_cache_remote_model_icon_skips_unsafe_url_without_request(tmp_path):
    module = _load_models_router_with_stubs()
    module.MODEL_ICON_CACHE_DIR = tmp_path
    module._is_safe_remote_icon_url = lambda _url: False
    module.requests.get = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("requests.get must not be called for unsafe URL")
    )

    cached = module._cache_remote_model_icon("m2", "http://127.0.0.1/icon.png")
    assert cached is None
    assert list(tmp_path.iterdir()) == []


def test_get_model_profile_image_uses_cached_file_then_fallback_redirect(tmp_path):
    module = _load_models_router_with_stubs()
    cached_path = tmp_path / "cached.png"
    cached_path.write_bytes(b"icon")

    model = types.SimpleNamespace(
        id="m3",
        updated_at="2026-02-26T00:00:00Z",
        meta=types.SimpleNamespace(profile_image_url="https://example.com/icon.png"),
    )
    module.Models.get_model_by_id = lambda model_id: model
    module._cache_remote_model_icon = lambda model_id, image_url: cached_path

    response = module.get_model_profile_image(id="m3", user=types.SimpleNamespace())
    assert response.path == cached_path
    assert response.headers["Content-Disposition"] == "inline"
    assert "ETag" in response.headers

    module._cache_remote_model_icon = lambda model_id, image_url: None
    redirect = module.get_model_profile_image(id="m3", user=types.SimpleNamespace())
    assert redirect.status_code == 302
    assert redirect.headers["Location"] == "https://example.com/icon.png"
