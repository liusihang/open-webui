import base64
import asyncio
import hashlib
import importlib
import json
import logging
import mimetypes
import re
import uuid
from typing import Any, Optional
from urllib.parse import urlparse

import aiohttp
from aiocache import cached
import requests

from fastapi import Depends, HTTPException, Request, APIRouter
from fastapi.responses import (
    FileResponse,
    StreamingResponse,
    JSONResponse,
    PlainTextResponse,
)
from pydantic import BaseModel, ConfigDict

from sqlalchemy.orm import Session

from open_webui.internal.db import get_session

from open_webui.models.models import Models
from open_webui.models.access_grants import AccessGrants
from open_webui.models.files import Files
from open_webui.models.groups import Groups
from ..config import (
    CACHE_DIR,
)
from ..env import (
    MODELS_CACHE_TTL,
    AIOHTTP_CLIENT_SESSION_SSL,
    AIOHTTP_CLIENT_TIMEOUT,
    AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST,
    ENABLE_FORWARD_USER_INFO_HEADERS,
    FORWARD_SESSION_INFO_HEADER_CHAT_ID,
    BYPASS_MODEL_ACCESS_CONTROL,
)
from open_webui.models.users import UserModel

from ..constants import ERROR_MESSAGES


from open_webui.utils.payload import (
    apply_model_params_to_body_openai,
    apply_system_prompt_to_body,
)
from open_webui.utils.misc import (
    cleanup_response,
    convert_logit_bias_input_to_json,
    stream_chunks_handler,
    stream_wrapper,
)

from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.headers import include_user_info_headers
from open_webui.utils.anthropic import is_anthropic_url, get_anthropic_models
from open_webui.storage.provider import Storage

log = logging.getLogger(__name__)


##########################################
#
# Utility functions
#
##########################################


async def send_get_request(url: str, key: Optional[str] = None, user: Optional[UserModel] = None):
    timeout = aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST)
    try:
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            headers = {
                **({"Authorization": f"Bearer {key}"} if key else {}),
            }

            if ENABLE_FORWARD_USER_INFO_HEADERS and user:
                headers = include_user_info_headers(headers, user)

            async with session.get(
                url,
                headers=headers,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as response:
                return await response.json()
    except Exception as e:
        # Handle connection error here
        log.error(f"Connection error: {e}")
        return None


async def get_models_request(url: str, key: Optional[str] = None, user: Optional[UserModel] = None):
    if is_anthropic_url(url):
        if user is None:
            return await get_anthropic_models(url, key or "")
        return await get_anthropic_models(url, key or "", user=user)
    return await send_get_request(f"{url}/models", key, user=user)


def openai_reasoning_model_handler(payload):
    """
    Handle reasoning model specific parameters
    """
    if "max_tokens" in payload:
        # Convert "max_tokens" to "max_completion_tokens" for all reasoning models
        payload["max_completion_tokens"] = payload["max_tokens"]
        del payload["max_tokens"]

    # Handle system role conversion based on model type
    if payload["messages"][0]["role"] == "system":
        model_lower = payload["model"].lower()
        # Legacy models use "user" role instead of "system"
        if model_lower.startswith("o1-mini") or model_lower.startswith("o1-preview"):
            payload["messages"][0]["role"] = "user"
        else:
            payload["messages"][0]["role"] = "developer"

    return payload


def apply_default_reasoning_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Ensure reasoning is enabled by default unless explicitly configured."""
    reasoning = payload.get("reasoning")

    if reasoning is None:
        payload["reasoning"] = {"enable": True}
        return payload

    if isinstance(reasoning, dict):
        if "enable" not in reasoning:
            if "enabled" in reasoning:
                reasoning["enable"] = bool(reasoning.get("enabled"))
            else:
                reasoning["enable"] = True

        reasoning.pop("enabled", None)

    return payload


def _is_banned_reasoning_token(token: Any) -> bool:
    if not isinstance(token, str):
        return False
    normalized = re.sub(r"[^A-Z0-9]", "", token.strip().upper())
    return normalized == "REASONINGENCRYPTEDCONTENT"


def _prune_banned_reasoning_tokens(value: Any) -> tuple[Any, int]:
    removed = 0
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, nested in value.items():
            if _is_banned_reasoning_token(key):
                removed += 1
                continue

            if key == "required" and isinstance(nested, list):
                filtered_required = []
                for required_item in nested:
                    if _is_banned_reasoning_token(required_item):
                        removed += 1
                        continue
                    filtered_required.append(required_item)
                sanitized[key] = filtered_required
                continue

            cleaned_nested, nested_removed = _prune_banned_reasoning_tokens(nested)
            removed += nested_removed
            sanitized[key] = cleaned_nested
        return sanitized, removed

    if isinstance(value, list):
        sanitized_list = []
        for item in value:
            if _is_banned_reasoning_token(item):
                removed += 1
                continue
            cleaned_item, nested_removed = _prune_banned_reasoning_tokens(item)
            removed += nested_removed
            sanitized_list.append(cleaned_item)
        return sanitized_list, removed

    return value, 0


def sanitize_upstream_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    sanitized, removed = _prune_banned_reasoning_tokens(payload)
    if isinstance(sanitized, dict):
        return sanitized, removed
    return payload, removed


def dedupe_system_messages(messages: Any) -> tuple[Any, int]:
    if not isinstance(messages, list):
        return messages, 0

    deduped: list[Any] = []
    has_system = False
    removed = 0

    for message in messages:
        if isinstance(message, dict) and message.get("role") == "system":
            if has_system:
                removed += 1
                continue
            has_system = True
        deduped.append(message)

    return deduped, removed


async def get_headers_and_cookies(
    request: Request,
    url: str,
    key: Optional[str] = None,
    config: Optional[dict[str, Any]] = None,
    metadata: Optional[dict[str, Any]] = None,
    user: Optional[UserModel] = None,
):
    cookies = {}
    config = config or {}
    headers = {
        "Content-Type": "application/json",
        **(
            {
                "HTTP-Referer": "https://openwebui.com/",
                "X-Title": "Open WebUI",
            }
            if "openrouter.ai" in url
            else {}
        ),
    }

    if ENABLE_FORWARD_USER_INFO_HEADERS and user:
        headers = include_user_info_headers(headers, user)
        if metadata:
            chat_id = metadata.get("chat_id")
            if isinstance(chat_id, str) and chat_id:
                headers[FORWARD_SESSION_INFO_HEADER_CHAT_ID] = chat_id

    token = None
    auth_type = config.get("auth_type")

    if auth_type == "bearer" or auth_type is None:
        # Default to bearer if not specified
        token = f"{key}"
    elif auth_type == "none":
        token = None
    elif auth_type == "session":
        cookies = request.cookies
        token = request.state.token.credentials
    elif auth_type == "system_oauth":
        cookies = request.cookies
        
        oauth_token = None
        try:
            if user and request.cookies.get("oauth_session_id", None):
                oauth_token = await request.app.state.oauth_manager.get_oauth_token(
                    user.id,
                    request.cookies.get("oauth_session_id", None),
                )
        except Exception as e:
            log.error(f"Error getting OAuth token: {e}")

        if oauth_token:
            token = f"{oauth_token.get('access_token', '')}"

    elif auth_type in ("azure_ad", "microsoft_entra_id"):
        token = get_microsoft_entra_id_access_token()

    if token:
        headers["Authorization"] = f"Bearer {token}"

    if config.get("headers") and isinstance(config.get("headers"), dict):
        extra_headers = config.get("headers")
        if isinstance(extra_headers, dict):
            headers = {**headers, **{str(k): str(v) for k, v in extra_headers.items()}}

    return headers, cookies


def get_microsoft_entra_id_access_token():
    """
    Get Microsoft Entra ID access token using DefaultAzureCredential for Azure OpenAI.
    Returns the token string or None if authentication fails.
    """
    try:
        identity = importlib.import_module("azure.identity")
        default_credential = getattr(identity, "DefaultAzureCredential")
        bearer_token_provider = getattr(identity, "get_bearer_token_provider")
        token_provider = bearer_token_provider(
            default_credential(),
            "https://cognitiveservices.azure.com/.default",
        )
        return token_provider()
    except Exception as e:
        log.error(f"Error getting Microsoft Entra ID access token: {e}")
        return None


def _normalize_bool_config_flag(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _normalize_content_type(value, filename: str) -> str:
    if isinstance(value, list):
        value = next((item for item in value if isinstance(item, str)), None)
    if not isinstance(value, str) or not value.strip():
        guessed_type, _ = mimetypes.guess_type(filename)
        return guessed_type or "application/octet-stream"
    return value.strip().split(";")[0]


def _is_image_file(file_item: dict[str, Any], content_type: str) -> bool:
    file_type = str(file_item.get("type", "")).lower()
    if file_type == "image":
        return True
    return content_type.startswith("image/")


def _resolve_file_name(file_obj, file_item: dict[str, Any], content_type: str) -> str:
    file_meta = file_obj.meta or {}
    filename = file_meta.get("name") or file_obj.filename or str(file_item.get("id", "file"))
    if "." not in filename:
        ext = mimetypes.guess_extension(content_type or "application/octet-stream")
        if ext:
            filename = f"{filename}{ext}"
    return filename


_CLIPROXY_FILE_ID_PATTERN = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)


def _extract_file_id_from_image_url(url: str) -> Optional[str]:
    """Best-effort extraction of a local Open WebUI file id from an image_url.

    Open WebUI frontend often uses the raw file id (UUID) as `image_url.url`.
    When forwarding to an upstream service (e.g. cliproxy), we need to inline the
    image bytes as a data URL because the upstream cannot resolve local ids.
    """

    if not isinstance(url, str):
        return None
    url = url.strip()
    if not url:
        return None
    if url.startswith(("data:", "http://", "https://")):
        return None

    # If the URL is already a UUID, accept it.
    try:
        return str(uuid.UUID(url))
    except Exception:
        pass

    # Extract UUID from common path forms: /files/<id>/content, /api/v1/files/<id>/content
    match = _CLIPROXY_FILE_ID_PATTERN.search(url)
    if match:
        try:
            return str(uuid.UUID(match.group(0)))
        except Exception:
            return None
    return None


def _inline_cliproxy_image_urls_in_payload(
    payload: dict[str, Any],
    metadata: Optional[dict[str, Any]],
    user,
) -> dict[str, Any]:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return payload

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue

        for part in content:
            if not isinstance(part, dict) or part.get("type") != "image_url":
                continue

            url_data = part.get("image_url")
            if isinstance(url_data, dict):
                url = url_data.get("url")
            else:
                url = url_data

            if not isinstance(url, str):
                continue

            file_id = _extract_file_id_from_image_url(url)
            if not file_id:
                continue

            file_obj = Files.get_file_by_id(str(file_id))
            if not file_obj:
                continue

            has_access = user.role == "admin" or file_obj.user_id == user.id
            if not has_access:
                has_access = AccessGrants.has_access(
                    user_id=user.id,
                    resource_type="file",
                    resource_id=str(file_id),
                    permission="read",
                )
            if not has_access:
                continue

            try:
                file_meta = file_obj.meta or {}
                content_type = _normalize_content_type(
                    file_meta.get("content_type"),
                    file_obj.filename or "",
                )
                if not getattr(file_obj, "path", None):
                    continue
                file_path = Storage.get_file(str(file_obj.path))
                with open(file_path, "rb") as file_handle:
                    raw_content = file_handle.read()
                if not raw_content:
                    continue

                encoded_content = base64.b64encode(raw_content).decode("utf-8")
                data_url = f"data:{content_type};base64,{encoded_content}"

                if isinstance(url_data, dict):
                    url_data["url"] = data_url
                    part["image_url"] = url_data
                else:
                    part["image_url"] = {"url": data_url}
            except Exception as e:
                log.debug(
                    f"CLIProxy image inlining skipped: read failed ({file_id}): {e}"
                )

    payload["messages"] = messages
    return payload


def _append_file_parts_to_last_user_message(
    messages: list[dict[str, Any]],
    file_parts: list[dict[str, Any]],
) -> None:
    if not file_parts:
        return

    target_index = None
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], dict) and messages[index].get("role") == "user":
            target_index = index
            break

    if target_index is None:
        return

    message = messages[target_index]
    content = message.get("content", "")

    if isinstance(content, list):
        normalized_content = [*content]
    elif isinstance(content, str):
        normalized_content = [{"type": "text", "text": content}]
    elif content is None:
        normalized_content = []
    else:
        normalized_content = [{"type": "text", "text": str(content)}]

    normalized_content.extend(file_parts)
    message["content"] = normalized_content


def _build_cliproxy_file_parts(
    payload: dict[str, Any],
    metadata: Optional[dict[str, Any]],
    user,
) -> list[dict[str, Any]]:
    if not isinstance(metadata, dict):
        return []

    parent_message = metadata.get("parent_message")
    if not isinstance(parent_message, dict):
        return []

    parent_files = parent_message.get("files")
    if not isinstance(parent_files, list):
        return []

    # Use data URLs so upstream can reliably infer MIME types.
    # (Raw base64 alone often causes upstreams to treat inputs as generic files.)
    use_data_url = True
    parts = []

    for file_item in parent_files:
        if not isinstance(file_item, dict):
            continue

        file_id = file_item.get("id")
        if not file_id:
            continue

        file_obj = Files.get_file_by_id(str(file_id))
        if not file_obj:
            log.debug(f"CLIProxy file injection skipped: file not found ({file_id})")
            continue

        has_access = user.role == "admin" or file_obj.user_id == user.id
        if not has_access:
            has_access = AccessGrants.has_access(
                user_id=user.id,
                resource_type="file",
                resource_id=str(file_id),
                permission="read",
            )

        if not has_access:
            log.debug(f"CLIProxy file injection skipped: access denied ({file_id})")
            continue

        try:
            file_meta = file_obj.meta or {}
            content_type = _normalize_content_type(
                file_item.get("content_type") or file_meta.get("content_type"),
                file_obj.filename or "",
            )

            if _is_image_file(file_item, content_type):
                continue

            if not getattr(file_obj, "path", None):
                continue
            file_path = Storage.get_file(str(file_obj.path))
            with open(file_path, "rb") as file_handle:
                raw_content = file_handle.read()

            if not raw_content:
                log.debug(f"CLIProxy file injection skipped: empty file ({file_id})")
                continue

            filename = _resolve_file_name(file_obj, file_item, content_type)
            encoded_content = base64.b64encode(raw_content).decode("utf-8")
            file_data = (
                f"data:{content_type};base64,{encoded_content}"
                if use_data_url
                else encoded_content
            )

            parts.append(
                {
                    "type": "file",
                    "file": {
                        "filename": filename,
                        "file_data": file_data,
                        "content_type": content_type,
                    },
                }
            )
        except Exception as e:
            log.debug(f"CLIProxy file injection skipped: read failed ({file_id}): {e}")

    return parts


def _inject_cliproxy_files_into_payload(
    payload: dict[str, Any],
    metadata: Optional[dict[str, Any]],
    user,
) -> dict[str, Any]:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return payload

    file_parts = _build_cliproxy_file_parts(payload, metadata, user)
    _append_file_parts_to_last_user_message(messages, file_parts)
    payload["messages"] = messages
    return payload


##########################################
#
# API routes
#
##########################################

router = APIRouter()


@router.get("/config")
async def get_config(request: Request, user=Depends(get_admin_user)):
    return {
        "ENABLE_OPENAI_API": request.app.state.config.ENABLE_OPENAI_API,
        "OPENAI_API_BASE_URLS": request.app.state.config.OPENAI_API_BASE_URLS,
        "OPENAI_API_KEYS": request.app.state.config.OPENAI_API_KEYS,
        "OPENAI_API_CONFIGS": request.app.state.config.OPENAI_API_CONFIGS,
    }


class OpenAIConfigForm(BaseModel):
    ENABLE_OPENAI_API: Optional[bool] = None
    OPENAI_API_BASE_URLS: list[str]
    OPENAI_API_KEYS: list[str]
    OPENAI_API_CONFIGS: dict[str, Any]


@router.post("/config/update")
async def update_config(
    request: Request, form_data: OpenAIConfigForm, user=Depends(get_admin_user)
):
    request.app.state.config.ENABLE_OPENAI_API = form_data.ENABLE_OPENAI_API
    request.app.state.config.OPENAI_API_BASE_URLS = form_data.OPENAI_API_BASE_URLS
    request.app.state.config.OPENAI_API_KEYS = form_data.OPENAI_API_KEYS

    # Check if API KEYS length is same than API URLS length
    if len(request.app.state.config.OPENAI_API_KEYS) != len(
        request.app.state.config.OPENAI_API_BASE_URLS
    ):
        if len(request.app.state.config.OPENAI_API_KEYS) > len(
            request.app.state.config.OPENAI_API_BASE_URLS
        ):
            request.app.state.config.OPENAI_API_KEYS = (
                request.app.state.config.OPENAI_API_KEYS[
                    : len(request.app.state.config.OPENAI_API_BASE_URLS)
                ]
            )
        else:
            request.app.state.config.OPENAI_API_KEYS += [""] * (
                len(request.app.state.config.OPENAI_API_BASE_URLS)
                - len(request.app.state.config.OPENAI_API_KEYS)
            )

    request.app.state.config.OPENAI_API_CONFIGS = form_data.OPENAI_API_CONFIGS

    # Remove the API configs that are not in the API URLS
    keys = list(map(str, range(len(request.app.state.config.OPENAI_API_BASE_URLS))))
    request.app.state.config.OPENAI_API_CONFIGS = {
        key: value
        for key, value in request.app.state.config.OPENAI_API_CONFIGS.items()
        if key in keys
    }

    return {
        "ENABLE_OPENAI_API": request.app.state.config.ENABLE_OPENAI_API,
        "OPENAI_API_BASE_URLS": request.app.state.config.OPENAI_API_BASE_URLS,
        "OPENAI_API_KEYS": request.app.state.config.OPENAI_API_KEYS,
        "OPENAI_API_CONFIGS": request.app.state.config.OPENAI_API_CONFIGS,
    }


@router.post("/audio/speech")
async def speech(request: Request, user=Depends(get_verified_user)):
    idx = None
    try:
        idx = request.app.state.config.OPENAI_API_BASE_URLS.index(
            "https://api.openai.com/v1"
        )

        body = await request.body()
        name = hashlib.sha256(body).hexdigest()

        SPEECH_CACHE_DIR = CACHE_DIR / "audio" / "speech"
        SPEECH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        file_path = SPEECH_CACHE_DIR.joinpath(f"{name}.mp3")
        file_body_path = SPEECH_CACHE_DIR.joinpath(f"{name}.json")

        # Check if the file already exists in the cache
        if file_path.is_file():
            return FileResponse(file_path)

        url = request.app.state.config.OPENAI_API_BASE_URLS[idx]
        key = request.app.state.config.OPENAI_API_KEYS[idx]
        api_config = request.app.state.config.OPENAI_API_CONFIGS.get(
            str(idx),
            request.app.state.config.OPENAI_API_CONFIGS.get(url, {}),  # Legacy support
        )

        headers, cookies = await get_headers_and_cookies(
            request, url, key, api_config, user=user
        )

        r = None
        try:
            r = requests.post(
                url=f"{url}/audio/speech",
                data=body,
                headers=headers,
                cookies=cookies,
                stream=True,
            )

            r.raise_for_status()

            # Save the streaming content to a file
            with open(file_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

            with open(file_body_path, "w") as f:
                json.dump(json.loads(body.decode("utf-8")), f)

            # Return the saved file
            return FileResponse(file_path)

        except Exception as e:
            log.exception(e)

            detail = None
            if r is not None:
                try:
                    res = r.json()
                    if "error" in res:
                        detail = f"External: {res['error']}"
                except Exception:
                    detail = f"External: {e}"

            raise HTTPException(
                status_code=r.status_code if r else 500,
                detail=detail if detail else "Open WebUI: Server Connection Error",
            )

    except ValueError:
        raise HTTPException(status_code=401, detail=ERROR_MESSAGES.OPENAI_NOT_FOUND)


async def get_all_models_responses(
    request: Request, user: UserModel
) -> list[dict[str, Any]]:
    if not request.app.state.config.ENABLE_OPENAI_API:
        return []

    # Cache config values locally to avoid repeated Redis lookups.
    # Each access to request.app.state.config.<KEY> triggers a Redis GET;
    # caching here avoids hundreds of redundant round-trips.
    api_base_urls = request.app.state.config.OPENAI_API_BASE_URLS
    api_keys = list(request.app.state.config.OPENAI_API_KEYS)
    api_configs = request.app.state.config.OPENAI_API_CONFIGS

    # Check if API KEYS length is same than API URLS length
    num_urls = len(api_base_urls)
    num_keys = len(api_keys)

    if num_keys != num_urls:
        # if there are more keys than urls, remove the extra keys
        if num_keys > num_urls:
            api_keys = api_keys[:num_urls]
            request.app.state.config.OPENAI_API_KEYS = api_keys
        # if there are more urls than keys, add empty keys
        else:
            api_keys += [""] * (num_urls - num_keys)
            request.app.state.config.OPENAI_API_KEYS = api_keys

    request_tasks = []
    for idx, url in enumerate(api_base_urls):
        if (str(idx) not in api_configs) and (url not in api_configs):  # Legacy support
            request_tasks.append(get_models_request(url, api_keys[idx], user=user))
        else:
            api_config = api_configs.get(
                str(idx),
                api_configs.get(url, {}),  # Legacy support
            )

            enable = api_config.get("enable", True)
            model_ids = api_config.get("model_ids", [])

            if enable:
                if len(model_ids) == 0:
                    request_tasks.append(
                        get_models_request(url, api_keys[idx], user=user)
                    )
                else:
                    model_list = {
                        "object": "list",
                        "data": [
                            {
                                "id": model_id,
                                "name": model_id,
                                "owned_by": "openai",
                                "openai": {"id": model_id},
                                "urlIdx": idx,
                            }
                            for model_id in model_ids
                        ],
                    }

                    request_tasks.append(
                        asyncio.ensure_future(asyncio.sleep(0, model_list))
                    )
            else:
                request_tasks.append(asyncio.ensure_future(asyncio.sleep(0, None)))

    responses = await asyncio.gather(*request_tasks)

    for idx, response in enumerate(responses):
        if response:
            url = api_base_urls[idx]
            api_config = api_configs.get(
                str(idx),
                api_configs.get(url, {}),  # Legacy support
            )

            connection_type = api_config.get("connection_type", "external")
            prefix_id = api_config.get("prefix_id", None)
            tags = api_config.get("tags", [])

            model_list = (
                response if isinstance(response, list) else response.get("data", [])
            )
            if not isinstance(model_list, list):
                # Catch non-list responses
                model_list = []

            for model in model_list:
                # Remove name key if its value is None #16689
                if "name" in model and model["name"] is None:
                    del model["name"]

                if prefix_id:
                    model["id"] = (
                        f"{prefix_id}.{model.get('id', model.get('name', ''))}"
                    )

                if tags:
                    model["tags"] = tags

                if connection_type:
                    model["connection_type"] = connection_type

    log.debug(f"get_all_models:responses() {responses}")
    return responses


async def get_filtered_models(models, user, db=None):
    # Filter models based on user access control
    model_ids = [model["id"] for model in models.get("data", [])]
    model_infos = {
        model_info.id: model_info
        for model_info in Models.get_models_by_ids(model_ids, db=db)
    }
    user_group_ids = {
        group.id for group in Groups.get_groups_by_member_id(user.id, db=db)
    }

    # Batch-fetch accessible resource IDs in a single query instead of N has_access calls
    accessible_model_ids = AccessGrants.get_accessible_resource_ids(
        user_id=user.id,
        resource_type="model",
        resource_ids=list(model_infos.keys()),
        permission="read",
        user_group_ids=user_group_ids,
        db=db,
    )

    filtered_models = []
    for model in models.get("data", []):
        model_info = model_infos.get(model["id"])
        if model_info:
            if user.id == model_info.user_id or model_info.id in accessible_model_ids:
                filtered_models.append(model)
    return filtered_models


@cached(
    ttl=MODELS_CACHE_TTL,
    key=lambda _, user: f"openai_all_models_{user.id}" if user else "openai_all_models",
)
async def get_all_models(request: Request, user: UserModel) -> dict[str, list[dict[str, Any]]]:
    log.info("get_all_models()")

    if not request.app.state.config.ENABLE_OPENAI_API:
        return {"data": []}

    # Cache config value locally to avoid repeated Redis lookups inside
    # the nested loop in get_merged_models (one GET per model otherwise).
    api_base_urls = request.app.state.config.OPENAI_API_BASE_URLS

    responses = await get_all_models_responses(request, user=user)

    def extract_data(response):
        if response and "data" in response:
            return response["data"]
        if isinstance(response, list):
            return response
        return None

    def is_supported_openai_models(model_id):
        if any(
            name in model_id
            for name in [
                "babbage",
                "dall-e",
                "davinci",
                "embedding",
                "tts",
                "whisper",
            ]
        ):
            return False
        return True

    def get_merged_models(model_lists):
        log.debug(f"merge_models_lists {model_lists}")
        models = {}

        for idx, model_list in enumerate(model_lists):
            if model_list is not None and "error" not in model_list:
                for model in model_list:
                    model_id = model.get("id") or model.get("name")

                    base_url = api_base_urls[idx]
                    hostname = urlparse(base_url).hostname if base_url else None
                    if hostname == "api.openai.com" and not is_supported_openai_models(
                        model_id
                    ):
                        # Skip unwanted OpenAI models
                        continue

                    if model_id and model_id not in models:
                        models[model_id] = {
                            **model,
                            "name": model.get("name", model_id),
                            "owned_by": "openai",
                            "openai": model,
                            "connection_type": model.get("connection_type", "external"),
                            "urlIdx": idx,
                        }

        return models

    models = get_merged_models(map(extract_data, responses))
    log.debug(f"models: {models}")

    request.app.state.OPENAI_MODELS = models
    return {"data": list(models.values())}


@router.get("/models")
@router.get("/models/{url_idx}")
async def get_models(
    request: Request, url_idx: Optional[int] = None, user=Depends(get_verified_user)
):
    if not request.app.state.config.ENABLE_OPENAI_API:
        raise HTTPException(status_code=503, detail="OpenAI API is disabled")

    models = {
        "data": [],
    }

    if url_idx is None:
        models = await get_all_models(request, user=user)
    else:
        url = request.app.state.config.OPENAI_API_BASE_URLS[url_idx]
        key = request.app.state.config.OPENAI_API_KEYS[url_idx]

        api_config = request.app.state.config.OPENAI_API_CONFIGS.get(
            str(url_idx),
            request.app.state.config.OPENAI_API_CONFIGS.get(url, {}),  # Legacy support
        )

        r = None
        async with aiohttp.ClientSession(
            trust_env=True,
            timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST),
        ) as session:
            try:
                headers, cookies = await get_headers_and_cookies(
                    request, url, key, api_config, user=user
                )

                if api_config.get("azure", False):
                    models = {
                        "data": api_config.get("model_ids", []) or [],
                        "object": "list",
                    }
                elif is_anthropic_url(url):
                    models = await get_anthropic_models(url, key, user=user)
                    if models is None:
                        raise Exception("Failed to connect to Anthropic API")
                else:
                    async with session.get(
                        f"{url}/models",
                        headers=headers,
                        cookies=cookies,
                        ssl=AIOHTTP_CLIENT_SESSION_SSL,
                    ) as r:
                        if r.status != 200:
                            error_detail = f"HTTP Error: {r.status}"
                            try:
                                res = await r.json()
                                if "error" in res:
                                    error_detail = f"External Error: {res['error']}"
                            except Exception:
                                pass
                            raise Exception(error_detail)

                        response_data = await r.json()

                        if "api.openai.com" in url:
                            response_data["data"] = [
                                model
                                for model in response_data.get("data", [])
                                if not any(
                                    name in model["id"]
                                    for name in [
                                        "babbage",
                                        "dall-e",
                                        "davinci",
                                        "embedding",
                                        "tts",
                                        "whisper",
                                    ]
                                )
                            ]

                        models = response_data
            except aiohttp.ClientError as e:
                # ClientError covers all aiohttp requests issues
                log.exception(f"Client error: {str(e)}")
                raise HTTPException(
                    status_code=500, detail="Open WebUI: Server Connection Error"
                )
            except Exception as e:
                log.exception(f"Unexpected error: {e}")
                error_detail = f"Unexpected error: {str(e)}"
                raise HTTPException(status_code=500, detail=error_detail)

    if user.role == "user" and not BYPASS_MODEL_ACCESS_CONTROL:
        models["data"] = await get_filtered_models(models, user)

    return models


class ConnectionVerificationForm(BaseModel):
    url: str
    key: str

    config: Optional[dict[str, Any]] = None


@router.post("/verify")
async def verify_connection(
    request: Request,
    form_data: ConnectionVerificationForm,
    user=Depends(get_admin_user),
):
    url = form_data.url
    key = form_data.key

    api_config = form_data.config or {}

    async with aiohttp.ClientSession(
        trust_env=True,
        timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST),
    ) as session:
        try:
            headers, cookies = await get_headers_and_cookies(
                request, url, key, api_config, user=user
            )

            if api_config.get("azure", False):
                # Only set api-key header if not using Azure Entra ID authentication
                auth_type = api_config.get("auth_type", "bearer")
                if auth_type not in ("azure_ad", "microsoft_entra_id"):
                    headers["api-key"] = key

                api_version = api_config.get("api_version", "") or "2023-03-15-preview"
                async with session.get(
                    url=f"{url}/openai/models?api-version={api_version}",
                    headers=headers,
                    cookies=cookies,
                    ssl=AIOHTTP_CLIENT_SESSION_SSL,
                ) as r:
                    try:
                        response_data = await r.json()
                    except Exception:
                        response_data = await r.text()

                    if r.status != 200:
                        if isinstance(response_data, (dict, list)):
                            return JSONResponse(
                                status_code=r.status, content=response_data
                            )
                        else:
                            return PlainTextResponse(
                                status_code=r.status, content=response_data
                            )

                    return response_data
            elif is_anthropic_url(url):
                result = await get_anthropic_models(url, key)
                if result is None:
                    raise HTTPException(
                        status_code=500, detail="Failed to connect to Anthropic API"
                    )
                if "error" in result:
                    raise HTTPException(status_code=500, detail=result["error"])
                return result
            else:
                async with session.get(
                    f"{url}/models",
                    headers=headers,
                    cookies=cookies,
                    ssl=AIOHTTP_CLIENT_SESSION_SSL,
                ) as r:
                    try:
                        response_data = await r.json()
                    except Exception:
                        response_data = await r.text()

                    if r.status != 200:
                        if isinstance(response_data, (dict, list)):
                            return JSONResponse(
                                status_code=r.status, content=response_data
                            )
                        else:
                            return PlainTextResponse(
                                status_code=r.status, content=response_data
                            )

                    return response_data

        except aiohttp.ClientError as e:
            # ClientError covers all aiohttp requests issues
            log.exception(f"Client error: {str(e)}")
            raise HTTPException(
                status_code=500, detail="Open WebUI: Server Connection Error"
            )
        except Exception as e:
            log.exception(f"Unexpected error: {e}")
            raise HTTPException(
                status_code=500, detail="Open WebUI: Server Connection Error"
            )


def get_azure_allowed_params(api_version: str) -> set[str]:
    allowed_params = {
        "messages",
        "temperature",
        "role",
        "content",
        "contentPart",
        "contentPartImage",
        "enhancements",
        "dataSources",
        "n",
        "stream",
        "stop",
        "max_tokens",
        "presence_penalty",
        "frequency_penalty",
        "logit_bias",
        "user",
        "function_call",
        "functions",
        "tools",
        "tool_choice",
        "top_p",
        "log_probs",
        "top_logprobs",
        "response_format",
        "seed",
        "max_completion_tokens",
        "reasoning_effort",
    }

    try:
        if api_version >= "2024-09-01-preview":
            allowed_params.add("stream_options")
    except ValueError:
        log.debug(
            f"Invalid API version {api_version} for Azure OpenAI. Defaulting to allowed parameters."
        )

    return allowed_params


def is_openai_reasoning_model(model: str) -> bool:
    return model.lower().startswith(("o1", "o3", "o4", "gpt-5"))


def convert_to_azure_payload(
    url: str, payload: dict[str, Any], api_version: str
) -> tuple[str, dict[str, Any]]:
    model = payload.get("model", "")

    # Filter allowed parameters based on Azure OpenAI API
    allowed_params = get_azure_allowed_params(api_version)

    # Special handling for o-series models
    if is_openai_reasoning_model(model):
        # Convert max_tokens to max_completion_tokens for o-series models
        if "max_tokens" in payload:
            payload["max_completion_tokens"] = payload["max_tokens"]
            del payload["max_tokens"]

        # Remove temperature if not 1 for o-series models
        if "temperature" in payload and payload["temperature"] != 1:
            log.debug(
                f"Removing temperature parameter for o-series model {model} as only default value (1) is supported"
            )
            del payload["temperature"]

    # Filter out unsupported parameters
    payload = {k: v for k, v in payload.items() if k in allowed_params}

    url = f"{url}/openai/deployments/{model}"
    return url, payload


def convert_to_responses_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Convert Chat Completions payload to Responses API format.

    Chat Completions: { messages: [{role, content}], ... }
    Responses API: { input: [{type: "message", role, content: [...]}], instructions: "system" }
    """
    messages = payload.pop("messages", [])

    system_content = ""
    input_items = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # Check for stored output items (from previous Responses API turn)
        stored_output = msg.get("output")
        if stored_output and isinstance(stored_output, list):
            input_items.extend(stored_output)
            continue

        if role == "system":
            if isinstance(content, str):
                system_content = content
            elif isinstance(content, list):
                system_content = "\n".join(
                    p.get("text", "") for p in content if p.get("type") == "text"
                )
            continue

        # Convert content format
        text_type = "output_text" if role == "assistant" else "input_text"

        if isinstance(content, str):
            content_parts = [{"type": text_type, "text": content}]
        elif isinstance(content, list):
            content_parts = []
            for part in content:
                if part.get("type") == "text":
                    content_parts.append(
                        {"type": text_type, "text": part.get("text", "")}
                    )
                elif part.get("type") == "image_url":
                    url_data = part.get("image_url", {})
                    url = (
                        url_data.get("url", "")
                        if isinstance(url_data, dict)
                        else url_data
                    )
                    content_parts.append({"type": "input_image", "image_url": url})
                elif part.get("type") == "file":
                    file_obj = part.get("file") or {}
                    if isinstance(file_obj, dict):
                        file_data = file_obj.get("file_data")
                        filename = file_obj.get("filename")
                        if isinstance(file_data, str) and file_data:
                            input_file = {
                                "type": "input_file",
                                "file_data": file_data,
                            }
                            if isinstance(filename, str) and filename:
                                input_file["filename"] = filename
                            content_parts.append(input_file)
        else:
            content_parts = [{"type": text_type, "text": str(content)}]

        input_items.append({"type": "message", "role": role, "content": content_parts})

    responses_payload = {**payload, "input": input_items}

    if system_content:
        responses_payload["instructions"] = system_content

    if "max_tokens" in responses_payload:
        responses_payload["max_output_tokens"] = responses_payload.pop("max_tokens")

    # Remove Chat Completions-only parameters not supported by the Responses API
    for unsupported_key in (
        "stream_options",
        "logit_bias",
        "frequency_penalty",
        "presence_penalty",
        "stop",
    ):
        responses_payload.pop(unsupported_key, None)

    # Convert Chat Completions tools format to Responses API format
    # Chat Completions: {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
    # Responses API:    {"type": "function", "name": ..., "description": ..., "parameters": ...}
    if "tools" in responses_payload and isinstance(responses_payload["tools"], list):
        converted_tools = []
        for tool in responses_payload["tools"]:
            if isinstance(tool, dict) and "function" in tool:
                func = tool["function"]
                converted_tool = {"type": tool.get("type", "function")}
                if isinstance(func, dict):
                    converted_tool["name"] = func.get("name", "")
                    if "description" in func:
                        converted_tool["description"] = func["description"]
                    if "parameters" in func:
                        converted_tool["parameters"] = func["parameters"]
                    if "strict" in func:
                        converted_tool["strict"] = func["strict"]
                converted_tools.append(converted_tool)
            else:
                # Already in correct format or unknown format, pass through
                converted_tools.append(tool)
        responses_payload["tools"] = converted_tools

    return responses_payload


def convert_responses_result(response: dict[str, Any]) -> dict[str, Any]:
    """
    Convert non-streaming Responses API result.
    Just add done flag - pass through raw response, frontend handles output.
    """
    response["done"] = True
    return response


@router.post("/chat/completions")
async def generate_chat_completion(
    request: Request,
    form_data: dict[str, Any],
    user=Depends(get_verified_user),
    bypass_filter: Optional[bool] = False,
    bypass_system_prompt: bool = False,
):
    # NOTE: We intentionally do NOT use Depends(get_session) here.
    # Database operations (get_model_by_id, AccessGrants.has_access) manage their own short-lived sessions.
    # This prevents holding a connection during the entire LLM call (30-60+ seconds),
    # which would exhaust the connection pool under concurrent load.
    if BYPASS_MODEL_ACCESS_CONTROL:
        bypass_filter = True

    idx = 0

    payload = {**form_data}
    metadata = payload.pop("metadata", None)
    payload["messages"], removed_duplicate_system = dedupe_system_messages(
        payload.get("messages", [])
    )
    if removed_duplicate_system > 0:
        log.warning(
            f"Removed {removed_duplicate_system} duplicate system message(s) before chat request"
        )

    model_id = form_data.get("model")
    if not isinstance(model_id, str) or not model_id:
        raise HTTPException(status_code=400, detail="Model not provided")
    model_info = Models.get_model_by_id(model_id)

    # Check model info and override the payload
    if model_info:
        if model_info.base_model_id:
            request_base_model_id = getattr(request, "base_model_id", None)
            base_model_id = (
                request_base_model_id
                if isinstance(request_base_model_id, str) and request_base_model_id
                else model_info.base_model_id
            )
            payload["model"] = base_model_id
            model_id = base_model_id

        params = model_info.params.model_dump()

        if params:
            system = params.pop("system", None)

            payload = apply_model_params_to_body_openai(params, payload)
            if not bypass_system_prompt:
                payload = apply_system_prompt_to_body(system, payload, metadata, user)

        # Check if user has access to the model
        if not bypass_filter and user.role == "user":
            user_group_ids = {
                group.id for group in Groups.get_groups_by_member_id(user.id)
            }
            if not (
                user.id == model_info.user_id
                or AccessGrants.has_access(
                    user_id=user.id,
                    resource_type="model",
                    resource_id=model_info.id,
                    permission="read",
                    user_group_ids=user_group_ids,
                )
            ):
                raise HTTPException(
                    status_code=403,
                    detail="Model not found",
                )
    elif not bypass_filter:
        if user.role != "admin":
            raise HTTPException(
                status_code=403,
                detail="Model not found",
            )

    # Check if model is already in app state cache to avoid expensive get_all_models() call
    models = request.app.state.OPENAI_MODELS
    if not models or model_id not in models:
        await get_all_models(request, user=user)
        models = request.app.state.OPENAI_MODELS
    model = models.get(model_id)

    if model:
        idx = model["urlIdx"]
    else:
        raise HTTPException(
            status_code=404,
            detail="Model not found",
        )

    # Get the API config for the model
    api_config = request.app.state.config.OPENAI_API_CONFIGS.get(
        str(idx),
        request.app.state.config.OPENAI_API_CONFIGS.get(
            request.app.state.config.OPENAI_API_BASE_URLS[idx], {}
        ),  # Legacy support
    )

    prefix_id = api_config.get("prefix_id", None)
    if prefix_id:
        payload["model"] = payload["model"].replace(f"{prefix_id}.", "")

    cliproxy_api_enabled = _normalize_bool_config_flag(api_config.get("cliproxy_api", False))
    api_type = str(api_config.get("api_type") or "").lower()

    # For OpenAI reasoning models (o1/o3/o4/gpt-5), Chat Completions may expose
    # reasoning token usage without textual reasoning deltas. When api_type is not
    # explicitly configured, prefer Responses API so reasoning summaries can be
    # surfaced consistently in Open WebUI.
    if not api_type and is_openai_reasoning_model(payload.get("model", "")):
        api_type = "responses"

    # Normalize local Open WebUI image references (UUID/path) into data URLs for
    # all upstream modes, not only cliproxy.
    payload = _inline_cliproxy_image_urls_in_payload(payload, metadata, user)

    if cliproxy_api_enabled:
        payload = _inject_cliproxy_files_into_payload(payload, metadata, user)
    elif api_type == "responses":
        # Responses API can consume input_file items after conversion, so reuse
        # the same file-part builder for non-cliproxy providers.
        payload = _inject_cliproxy_files_into_payload(payload, metadata, user)

    # Add user info to the payload if the model is a pipeline
    if "pipeline" in model and model.get("pipeline"):
        payload["user"] = {
            "name": user.name,
            "id": user.id,
            "email": user.email,
            "role": user.role,
        }

    url = request.app.state.config.OPENAI_API_BASE_URLS[idx]
    key = request.app.state.config.OPENAI_API_KEYS[idx]

    # Check if model is a reasoning model that needs special handling
    if is_openai_reasoning_model(payload["model"]):
        payload = openai_reasoning_model_handler(payload)
    elif "api.openai.com" not in url:
        # Remove "max_completion_tokens" from the payload for backward compatibility
        if "max_completion_tokens" in payload:
            payload["max_tokens"] = payload["max_completion_tokens"]
            del payload["max_completion_tokens"]

    if "max_tokens" in payload and "max_completion_tokens" in payload:
        del payload["max_tokens"]

    payload = apply_default_reasoning_payload(payload)

    # Convert the modified body back to JSON
    if "logit_bias" in payload and payload["logit_bias"]:
        logit_bias = convert_logit_bias_input_to_json(payload["logit_bias"])

        if logit_bias:
            payload["logit_bias"] = json.loads(logit_bias)

    headers, cookies = await get_headers_and_cookies(
        request, url, key, api_config, metadata, user=user
    )

    is_responses = api_type == "responses"

    if api_config.get("azure", False):
        api_version = api_config.get("api_version", "2023-03-15-preview")
        request_url, payload = convert_to_azure_payload(url, payload, api_version)

        # Only set api-key header if not using Azure Entra ID authentication
        auth_type = api_config.get("auth_type", "bearer")
        if auth_type not in ("azure_ad", "microsoft_entra_id"):
            headers["api-key"] = key

        headers["api-version"] = api_version

        if is_responses:
            payload = convert_to_responses_payload(payload)
            request_url = f"{request_url}/responses?api-version={api_version}"
        else:
            request_url = f"{request_url}/chat/completions?api-version={api_version}"
    else:
        if is_responses:
            payload = convert_to_responses_payload(payload)
            request_url = f"{url}/responses"
        else:
            request_url = f"{url}/chat/completions"

    payload, removed_banned_tokens = sanitize_upstream_payload(payload)
    if removed_banned_tokens > 0:
        log.warning(
            f"Removed {removed_banned_tokens} banned reasoning token(s) from chat payload before upstream request"
        )

    payload = json.dumps(payload)

    r = None
    session = None
    streaming = False
    response = None

    try:
        session = aiohttp.ClientSession(
            trust_env=True, timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT)
        )

        r = await session.request(
            method="POST",
            url=request_url,
            data=payload,
            headers=headers,
            cookies=cookies,
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
        )

        # Check if response is SSE
        if "text/event-stream" in r.headers.get("Content-Type", ""):
            streaming = True
            return StreamingResponse(
                stream_wrapper(r, session, stream_chunks_handler),
                status_code=r.status,
                headers=dict(r.headers),
            )
        else:
            try:
                response = await r.json()
            except Exception as e:
                log.error(e)
                response = await r.text()

            if r.status >= 400:
                if isinstance(response, (dict, list)):
                    return JSONResponse(status_code=r.status, content=response)
                else:
                    return PlainTextResponse(status_code=r.status, content=response)

            # Convert Responses API result to simple format
            if is_responses and isinstance(response, dict):
                response = convert_responses_result(response)

            return response
    except Exception as e:
        log.exception(e)

        raise HTTPException(
            status_code=r.status if r else 500,
            detail="Open WebUI: Server Connection Error",
        )
    finally:
        if not streaming:
            await cleanup_response(r, session)


async def embeddings(request: Request, form_data: dict[str, Any], user: UserModel):
    """
    Calls the embeddings endpoint for OpenAI-compatible providers.

    Args:
        request (Request): The FastAPI request context.
        form_data (dict): OpenAI-compatible embeddings payload.
        user (UserModel): The authenticated user.

    Returns:
        dict: OpenAI-compatible embeddings response.
    """
    idx = 0
    # Prepare payload/body
    body = json.dumps(form_data)
    # Find correct backend url/key based on model
    model_id = form_data.get("model")
    # Check if model is already in app state cache to avoid expensive get_all_models() call
    models = request.app.state.OPENAI_MODELS
    if not models or model_id not in models:
        await get_all_models(request, user=user)
        models = request.app.state.OPENAI_MODELS
    if model_id in models:
        idx = models[model_id]["urlIdx"]

    url = request.app.state.config.OPENAI_API_BASE_URLS[idx]
    key = request.app.state.config.OPENAI_API_KEYS[idx]
    api_config = request.app.state.config.OPENAI_API_CONFIGS.get(
        str(idx),
        request.app.state.config.OPENAI_API_CONFIGS.get(url, {}),  # Legacy support
    )

    r = None
    session = None
    streaming = False

    headers, cookies = await get_headers_and_cookies(
        request, url, key, api_config, user=user
    )
    try:
        session = aiohttp.ClientSession(
            trust_env=True,
            timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT),
        )
        r = await session.request(
            method="POST",
            url=f"{url}/embeddings",
            data=body,
            headers=headers,
            cookies=cookies,
        )

        if "text/event-stream" in r.headers.get("Content-Type", ""):
            streaming = True
            return StreamingResponse(
                stream_wrapper(r, session),
                status_code=r.status,
                headers=dict(r.headers),
            )
        else:
            try:
                response_data = await r.json()
            except Exception:
                response_data = await r.text()

            if r.status >= 400:
                if isinstance(response_data, (dict, list)):
                    return JSONResponse(status_code=r.status, content=response_data)
                else:
                    return PlainTextResponse(
                        status_code=r.status, content=response_data
                    )

            return response_data
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=r.status if r else 500,
            detail="Open WebUI: Server Connection Error",
        )
    finally:
        if not streaming:
            await cleanup_response(r, session)


class ResponsesForm(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    input: Optional[list[Any] | str] = None
    instructions: Optional[str] = None
    stream: Optional[bool] = None
    temperature: Optional[float] = None
    max_output_tokens: Optional[int] = None
    top_p: Optional[float] = None
    tools: Optional[list[dict[str, Any]]] = None
    tool_choice: Optional[str | dict[str, Any]] = None
    text: Optional[dict[str, Any]] = None
    truncation: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    store: Optional[bool] = None
    reasoning: Optional[dict[str, Any]] = None
    previous_response_id: Optional[str] = None


@router.post("/responses")
async def responses(
    request: Request,
    form_data: ResponsesForm,
    user=Depends(get_verified_user),
):
    """
    Forward requests to the OpenAI Responses API endpoint.
    Routes to the correct upstream backend based on the model field.
    """
    payload = form_data.model_dump(exclude_none=True)
    payload = apply_default_reasoning_payload(payload)
    payload, removed_banned_tokens = sanitize_upstream_payload(payload)
    if removed_banned_tokens > 0:
        log.warning(
            f"Removed {removed_banned_tokens} banned reasoning token(s) from responses payload before upstream request"
        )
    body = json.dumps(payload)

    idx = 0
    model_id = form_data.model
    if model_id:
        models = request.app.state.OPENAI_MODELS
        if not models or model_id not in models:
            await get_all_models(request, user=user)
            models = request.app.state.OPENAI_MODELS
        if model_id in models:
            idx = models[model_id]["urlIdx"]

    url = request.app.state.config.OPENAI_API_BASE_URLS[idx]
    key = request.app.state.config.OPENAI_API_KEYS[idx]
    api_config = request.app.state.config.OPENAI_API_CONFIGS.get(
        str(idx),
        request.app.state.config.OPENAI_API_CONFIGS.get(url, {}),  # Legacy support
    )

    r = None
    session = None
    streaming = False

    try:
        headers, cookies = await get_headers_and_cookies(
            request, url, key, api_config, user=user
        )

        if api_config.get("azure", False):
            api_version = api_config.get("api_version", "2023-03-15-preview")

            auth_type = api_config.get("auth_type", "bearer")
            if auth_type not in ("azure_ad", "microsoft_entra_id"):
                headers["api-key"] = key

            headers["api-version"] = api_version

            model = payload.get("model", "")
            request_url = (
                f"{url}/openai/deployments/{model}/responses?api-version={api_version}"
            )
        else:
            request_url = f"{url}/responses"

        session = aiohttp.ClientSession(
            trust_env=True,
            timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT),
        )
        r = await session.request(
            method="POST",
            url=request_url,
            data=body,
            headers=headers,
            cookies=cookies,
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
        )

        # Check if response is SSE
        if "text/event-stream" in r.headers.get("Content-Type", ""):
            streaming = True
            return StreamingResponse(
                stream_wrapper(r, session),
                status_code=r.status,
                headers=dict(r.headers),
            )
        else:
            try:
                response_data = await r.json()
            except Exception:
                response_data = await r.text()

            if r.status >= 400:
                if isinstance(response_data, (dict, list)):
                    return JSONResponse(status_code=r.status, content=response_data)
                else:
                    return PlainTextResponse(
                        status_code=r.status, content=response_data
                    )

            return response_data

    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=r.status if r else 500,
            detail="Open WebUI: Server Connection Error",
        )
    finally:
        if not streaming:
            await cleanup_response(r, session)


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy(path: str, request: Request, user=Depends(get_verified_user)):
    """
    Deprecated: proxy all requests to OpenAI API
    """

    body = await request.body()

    # Parse JSON body to resolve model-based routing
    payload = None
    if body:
        try:
            payload = json.loads(body)
            if isinstance(payload, dict) and isinstance(payload.get("model"), str):
                payload = apply_default_reasoning_payload(payload)
                payload["messages"], removed_duplicate_system = dedupe_system_messages(
                    payload.get("messages", [])
                )
                if removed_duplicate_system > 0:
                    log.warning(
                        f"Removed {removed_duplicate_system} duplicate system message(s) from proxy payload before upstream request"
                    )
                payload, removed_banned_tokens = sanitize_upstream_payload(payload)
                if removed_banned_tokens > 0:
                    log.warning(
                        f"Removed {removed_banned_tokens} banned reasoning token(s) from proxy payload before upstream request"
                    )
                body = json.dumps(payload).encode()
        except (json.JSONDecodeError, ValueError):
            payload = None

    idx = 0
    model_id = payload.get("model") if isinstance(payload, dict) else None
    if model_id:
        models = request.app.state.OPENAI_MODELS
        if not models or model_id not in models:
            await get_all_models(request, user=user)
            models = request.app.state.OPENAI_MODELS
        if model_id in models:
            idx = models[model_id]["urlIdx"]

    url = request.app.state.config.OPENAI_API_BASE_URLS[idx]
    key = request.app.state.config.OPENAI_API_KEYS[idx]
    api_config = request.app.state.config.OPENAI_API_CONFIGS.get(
        str(idx),
        request.app.state.config.OPENAI_API_CONFIGS.get(
            request.app.state.config.OPENAI_API_BASE_URLS[idx], {}
        ),  # Legacy support
    )

    r = None
    session = None
    streaming = False

    try:
        headers, cookies = await get_headers_and_cookies(
            request, url, key, api_config, user=user
        )

        if api_config.get("azure", False):
            api_version = api_config.get("api_version", "2023-03-15-preview")

            # Only set api-key header if not using Azure Entra ID authentication
            auth_type = api_config.get("auth_type", "bearer")
            if auth_type not in ("azure_ad", "microsoft_entra_id"):
                headers["api-key"] = key

            headers["api-version"] = api_version

            payload = json.loads(body)
            url, payload = convert_to_azure_payload(url, payload, api_version)
            payload, removed_banned_tokens = sanitize_upstream_payload(payload)
            if removed_banned_tokens > 0:
                log.warning(
                    f"Removed {removed_banned_tokens} banned reasoning token(s) from azure proxy payload before upstream request"
                )
            body = json.dumps(payload).encode()

            request_url = f"{url}/{path}?api-version={api_version}"
        else:
            request_url = f"{url}/{path}"

        session = aiohttp.ClientSession(
            trust_env=True,
            timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT),
        )
        r = await session.request(
            method=request.method,
            url=request_url,
            data=body,
            headers=headers,
            cookies=cookies,
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
        )

        # Check if response is SSE
        if "text/event-stream" in r.headers.get("Content-Type", ""):
            streaming = True
            return StreamingResponse(
                stream_wrapper(r, session),
                status_code=r.status,
                headers=dict(r.headers),
            )
        else:
            try:
                response_data = await r.json()
            except Exception:
                response_data = await r.text()

            if r.status >= 400:
                if isinstance(response_data, (dict, list)):
                    return JSONResponse(status_code=r.status, content=response_data)
                else:
                    return PlainTextResponse(
                        status_code=r.status, content=response_data
                    )

            return response_data

    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=r.status if r else 500,
            detail="Open WebUI: Server Connection Error",
        )
    finally:
        if not streaming:
            await cleanup_response(r, session)
