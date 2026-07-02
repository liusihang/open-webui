"""
title: Bifrost Unified Manifold Pipe (Chat + Responses + Reasoning Fallback)
authors: you
version: 0.2.16
required_open_webui_version: 0.8.5
license: MIT
"""

from __future__ import annotations

import copy
import base64
import hashlib
import html
import importlib
import inspect
import asyncio
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
import uuid
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Dict, Generator, Iterator, List, Optional, Tuple, Union
from urllib.parse import urlparse

import requests
from pydantic import BaseModel, Field

try:
    import anyio
except Exception:
    anyio = None


def _load_pop_system_message():
    try:
        module = importlib.import_module("open_webui.utils.misc")
        return module.pop_system_message
    except Exception:

        def _fallback(messages):
            return None, messages

        return _fallback


pop_system_message = _load_pop_system_message()


class Pipe:
    class Valves(BaseModel):
        BIFROST_API_KEY: str = Field(
            default="",
            description="Bifrost API key (Bearer token)",
        )
        BIFROST_BASE_URL: str = Field(
            default="https://api.maximhq.com/v1",
            description="Bifrost OpenAI-compatible base URL",
        )
        ROUTE_MODE: str = Field(
            default="auto",
            description="Routing mode: responses | chat | auto",
        )
        REQUEST_TIMEOUT_SECS: int = Field(
            default=300,
            description="HTTP timeout in seconds",
        )
        INLINE_FILE_MAX_BYTES: int = Field(
            default=20 * 1024 * 1024,
            description="Max bytes per uploaded file when sending inline file_data/image/audio",
        )
        ATTACHMENT_TEXT_FALLBACK_MAX_CHARS: int = Field(
            default=60000,
            description="Max chars for document text fallback",
        )
        ENABLE_DOC_TO_PDF_CONVERSION: bool = Field(
            default=True,
            description="Try converting doc/docx to PDF via soffice/libreoffice before upload",
        )
        DOC_TO_PDF_TIMEOUT_SECS: int = Field(
            default=120,
            description="Timeout for doc/docx -> pdf conversion command",
        )
        DEFAULT_REASONING_ENABLED: bool = Field(
            default=True,
            description="Inject reasoning.enable=true when request does not provide reasoning config",
        )
        REASONING_ENABLE_PARAM_KEY: str = Field(
            default="enabled",
            description="Reasoning enable flag key to send upstream: enabled | enable | enabel | both | auto",
        )
        DEFAULT_MAX_AGENT_DEPTH: int = Field(
            default=5,
            description="Default max_agent_depth for Agent Mode when not provided in request",
        )
        ENABLE_AGENT_PSEUDO_STREAM_STATUS: bool = Field(
            default=True,
            description="When stream=true + Agent Mode, emit status events and emulate stream via non-stream call",
        )
        REASONING_PARAM_MODE: str = Field(
            default="auto",
            description="Reasoning payload mode: auto | passthrough | minimal",
        )
        DEFAULT_REASONING_MAX_TOKENS: int = Field(
            default=0,
            description="Optional default reasoning.max_tokens when request does not provide one",
        )
        DEFAULT_REASONING_EFFORT: str = Field(
            default="",
            description="Optional default effort: low | medium | high",
        )
        DEFAULT_REASONING_SUMMARY: str = Field(
            default="",
            description="Optional default reasoning summary mode (e.g. auto)",
        )
        SHOW_REASONING_CONTENT: bool = Field(
            default=True,
            description="Emit reasoning deltas/content to OpenWebUI reasoning panel",
        )
        FORCE_NON_STREAM_IMAGE_MODE: bool = Field(
            default=True,
            description="For image generation models, use non-stream upstream call and repackage as stream to ensure image rendering",
        )
        RETRY_WITHOUT_REASONING_ON_ERROR: bool = Field(
            default=True,
            description="If upstream rejects reasoning params, retry once without reasoning payload",
        )
        RETRY_WITHOUT_TOOLS_ON_INVALID_PARAMS: bool = Field(
            default=True,
            description="If upstream returns invalid_params for tools, retry once without tools",
        )
        ENABLE_DEFAULT_WEB_SEARCH_TOOL: bool = Field(
            default=True,
            description="Always inject a default web_search tool so models supporting native web search can use it",
        )
        DEBUG_MODE: bool = Field(
            default=False,
            description="Enable debug logs",
        )
        ENABLE_PROMPT_CACHE_MARKERS: bool = Field(
            default=True,
            description="Inject Anthropic-style cache_control markers for prompt caching on supported models",
        )
        PROMPT_CACHE_MIN_TEXT_CHARS: int = Field(
            default=1024,
            description="Minimum text length before a text block is marked cacheable",
        )
        PROMPT_CACHE_MAX_MARKERS: int = Field(
            default=4,
            description="Maximum cache_control markers to inject into a single request",
        )
        ENABLE_GEMINI_CACHED_CONTENT: bool = Field(
            default=False,
            description="Enable direct Gemini cached_content creation for direct gemini/* models",
        )
        GEMINI_CACHE_API_KEY: str = Field(
            default="",
            description="Direct Gemini API key for cached content operations",
        )
        GEMINI_CACHE_BASE_URL: str = Field(
            default="https://generativelanguage.googleapis.com/v1beta",
            description="Direct Gemini API base URL for cached content operations",
        )
        GEMINI_CACHE_TTL_SECS: int = Field(
            default=3600,
            description="TTL in seconds for Gemini cached content objects",
        )

    def __init__(self):
        self.type = "manifold"
        self.id = "bifrost_unified"
        self.name = "bifrost/"
        self.valves = self.Valves(
            BIFROST_API_KEY=os.getenv(
                "BIFROST_API_KEY", os.getenv("OPENAI_API_KEY", "")
            ),
            BIFROST_BASE_URL=os.getenv(
                "BIFROST_BASE_URL",
                os.getenv("OPENAI_BASE_URL", "https://api.maximhq.com/v1"),
            ),
        )
        self._models_cache: List[dict] = []
        self._models_cache_source: str = ""
        self._models_cache_updated_at: float = 0.0
        self._models_fail_backoff_secs: float = 15.0
        self._gemini_cached_content_index: Dict[str, dict] = {}

    # -------------------------------------------------------------------------
    # Public entrypoints
    # -------------------------------------------------------------------------

    def pipes(self) -> List[dict]:
        now = time.time()
        has_key = bool((self.valves.BIFROST_API_KEY or "").strip())

        if has_key:
            should_retry_remote = True
            if (
                self._models_cache_source == "fallback"
                and (now - self._models_cache_updated_at)
                < self._models_fail_backoff_secs
            ):
                should_retry_remote = False

            if should_retry_remote:
                remote = self._fetch_remote_models()
                if remote:
                    self._models_cache = remote
                    self._models_cache_source = "remote"
                    self._models_cache_updated_at = now
                    return self._models_cache

                self._models_cache_source = "fallback"
                self._models_cache_updated_at = now

        if self._models_cache and self._models_cache_source == "remote":
            return self._models_cache

        self._models_cache = self._fallback_models()
        return self._models_cache

    def pipe(
        self,
        body: dict,
        __files__=None,
        __metadata__=None,
        __tools__=None,
        __request__=None,
        __event_emitter__=None,
        __user__=None,
    ) -> Union[str, dict, Generator, Iterator]:
        if not self.valves.BIFROST_API_KEY:
            return "Error: BIFROST_API_KEY is required"

        raw_messages = body.get("messages", []) or []
        system_message, messages = pop_system_message(raw_messages)
        model = self._normalize_model(body.get("model", ""))
        stream = bool(body.get("stream", False))
        route_mode = self._resolve_route_mode(body)
        force_image_non_stream = (
            stream
            and self._force_non_stream_image_mode()
            and self._is_image_generation_model(model)
        )

        runtime_tool_meta = dict(__metadata__) if isinstance(__metadata__, dict) else {}
        if not isinstance(runtime_tool_meta.get("tool_ids"), list):
            body_tool_ids = body.get("tool_ids")
            if isinstance(body_tool_ids, list):
                runtime_tool_meta["tool_ids"] = body_tool_ids

        function_specs = self._collect_function_specs(
            body.get("tools"), __tools__, runtime_tool_meta
        )
        force_image_tool_non_stream = self._should_force_image_tool_non_stream(
            body=body,
            stream=stream,
            model=model,
            function_specs=function_specs,
        )
        file_items = self._collect_file_candidates(body, __files__, __metadata__)
        attachments = self._prepare_attachments(
            file_items=file_items, user=__user__, model=model
        )
        body, system_message, messages = self._maybe_prepare_gemini_cached_content(
            body=body,
            model=model,
            system_message=system_message,
            messages=messages,
            function_specs=function_specs,
            attachments=attachments,
        )
        agent_settings = self._resolve_agent_settings(body)
        tool_name_prefix = self._single_selected_mcp_prefix(runtime_tool_meta) or ""
        agent_mode = bool(agent_settings.get("agent_mode"))
        force_agent_non_stream = (
            stream and agent_mode and self._agent_pseudo_stream_status_enabled()
        )
        agent_depth = agent_settings.get("max_agent_depth")

        def _emit_agent_start(route_name: str) -> None:
            if not force_agent_non_stream:
                return
            depth_text = (
                str(agent_depth)
                if isinstance(agent_depth, int) and agent_depth > 0
                else "auto"
            )
            self._emit_status(
                __event_emitter__,
                action="agent_loop",
                description=(
                    f"Agent pseudo-stream running on {route_name} "
                    f"(max_agent_depth={depth_text})"
                ),
                done=False,
                extra={"route": route_name, "max_agent_depth": depth_text},
            )

        def _emit_agent_done(route_name: str, result: Union[str, dict]) -> None:
            if not force_agent_non_stream:
                return
            if isinstance(result, str) and result.strip().lower().startswith("error"):
                self._emit_status(
                    __event_emitter__,
                    action="agent_loop",
                    description="Agent pseudo-stream failed",
                    done=True,
                    error=True,
                    extra={"route": route_name},
                )
                return
            has_tool_calls = self._result_has_tool_calls(result)
            description = (
                "Agent round completed with tool calls"
                if has_tool_calls
                else "Agent round completed"
            )
            self._emit_status(
                __event_emitter__,
                action="agent_loop",
                description=description,
                done=True,
                extra={
                    "route": route_name,
                    "pending_tool_calls": bool(has_tool_calls),
                },
            )

        if route_mode == "chat":
            effective_cache_settings = self._resolve_effective_cache_settings(
                body=body,
                model=model,
                route_mode=route_mode,
                system_message=system_message,
                messages=messages,
                attachments=attachments,
                function_specs=function_specs,
            )
            payload = self._build_chat_payload(
                body=body,
                model=model,
                system_message=system_message,
                messages=messages,
                attachments=attachments,
                function_specs=function_specs,
                agent_settings=agent_settings,
                effective_cache_settings=effective_cache_settings,
            )
            payload, _ = self._sanitize_upstream_payload(payload)
            if (
                force_image_non_stream
                or force_agent_non_stream
                or force_image_tool_non_stream
            ):
                payload["stream"] = False
                _emit_agent_start("chat")
                out = self._chat_non_stream_with_fallback(
                    payload, tool_name_prefix=tool_name_prefix
                )
                _emit_agent_done("chat", out)
                return self._stream_from_nonstream_result(out, model)
            if stream:
                return self._chat_stream_with_fallback(
                    payload, tool_name_prefix=tool_name_prefix, __event_emitter__=__event_emitter__
                )
            return self._chat_non_stream_with_fallback(
                payload, tool_name_prefix=tool_name_prefix
            )

        if route_mode == "responses":
            effective_cache_settings = self._resolve_effective_cache_settings(
                body=body,
                model=model,
                route_mode=route_mode,
                system_message=system_message,
                messages=messages,
                attachments=attachments,
                function_specs=function_specs,
            )
            payload = self._build_responses_payload(
                body=body,
                model=model,
                system_message=system_message,
                messages=messages,
                attachments=attachments,
                function_specs=function_specs,
                agent_settings=agent_settings,
                effective_cache_settings=effective_cache_settings,
            )
            payload, _ = self._sanitize_upstream_payload(payload)
            if (
                force_image_non_stream
                or force_agent_non_stream
                or force_image_tool_non_stream
            ):
                payload["stream"] = False
                _emit_agent_start("responses")
                out = self._responses_non_stream_with_fallback(
                    payload, tool_name_prefix=tool_name_prefix
                )
                _emit_agent_done("responses", out)
                return self._stream_from_nonstream_result(out, model)
            if stream:
                return self._responses_stream_with_fallback(
                    payload, tool_name_prefix=tool_name_prefix, __event_emitter__=__event_emitter__
                )
            return self._responses_non_stream_with_fallback(
                payload, tool_name_prefix=tool_name_prefix
            )

        # auto mode: prefer responses first, fallback to chat if endpoint unsupported.
        effective_cache_settings = self._resolve_effective_cache_settings(
            body=body,
            model=model,
            route_mode=route_mode,
            system_message=system_message,
            messages=messages,
            attachments=attachments,
            function_specs=function_specs,
        )
        if stream:
            responses_payload = self._build_responses_payload(
                body=body,
                model=model,
                system_message=system_message,
                messages=messages,
                attachments=attachments,
                function_specs=function_specs,
                agent_settings=agent_settings,
                effective_cache_settings=effective_cache_settings,
            )
            responses_payload, _ = self._sanitize_upstream_payload(responses_payload)
            if (
                force_image_non_stream
                or force_agent_non_stream
                or force_image_tool_non_stream
            ):
                responses_payload["stream"] = False
                _emit_agent_start("responses")
                out = self._responses_non_stream_with_fallback(
                    responses_payload, tool_name_prefix=tool_name_prefix
                )
                used_route = "responses"
                if self._is_endpoint_missing_result(out):
                    chat_payload = self._build_chat_payload(
                        body=body,
                        model=model,
                        system_message=system_message,
                        messages=messages,
                        attachments=attachments,
                        function_specs=function_specs,
                        agent_settings=agent_settings,
                        effective_cache_settings=effective_cache_settings,
                    )
                    chat_payload, _ = self._sanitize_upstream_payload(chat_payload)
                    chat_payload["stream"] = False
                    out = self._chat_non_stream_with_fallback(
                        chat_payload, tool_name_prefix=tool_name_prefix
                    )
                    used_route = "chat"
                _emit_agent_done(used_route, out)
                return self._stream_from_nonstream_result(out, model)
            return self._auto_stream_with_fallback(
                responses_payload=responses_payload,
                chat_payload_builder=lambda: self._sanitize_upstream_payload(
                    self._build_chat_payload(
                        body=body,
                        model=model,
                        system_message=system_message,
                        messages=messages,
                        attachments=attachments,
                        function_specs=function_specs,
                        agent_settings=agent_settings,
                        effective_cache_settings=effective_cache_settings,
                    )
                )[0],
                tool_name_prefix=tool_name_prefix,
                __event_emitter__=__event_emitter__,
            )

        responses_payload = self._build_responses_payload(
            body=body,
            model=model,
            system_message=system_message,
            messages=messages,
            attachments=attachments,
            function_specs=function_specs,
            agent_settings=agent_settings,
            effective_cache_settings=effective_cache_settings,
        )
        responses_payload, _ = self._sanitize_upstream_payload(responses_payload)
        out = self._responses_non_stream_with_fallback(
            responses_payload, tool_name_prefix=tool_name_prefix
        )
        if not self._is_endpoint_missing_result(out):
            return out

        chat_payload = self._build_chat_payload(
            body=body,
            model=model,
            system_message=system_message,
            messages=messages,
            attachments=attachments,
            function_specs=function_specs,
            agent_settings=agent_settings,
            effective_cache_settings=effective_cache_settings,
        )
        chat_payload, _ = self._sanitize_upstream_payload(chat_payload)
        return self._chat_non_stream_with_fallback(
            chat_payload, tool_name_prefix=tool_name_prefix
        )

    # -------------------------------------------------------------------------
    # Basic helpers
    # -------------------------------------------------------------------------

    def _debug(self, message: str) -> None:
        if self.valves.DEBUG_MODE:
            print(f"[BifrostPipe] {message}")

    def _emit_status(
        self,
        event_emitter: Any,
        action: str,
        description: str,
        done: bool,
        error: bool = False,
        extra: Optional[dict] = None,
    ) -> None:
        if not event_emitter:
            return
        payload = {
            "type": "status",
            "data": {
                "action": action,
                "description": description,
                "done": bool(done),
            },
        }
        if error:
            payload["data"]["error"] = True
        if isinstance(extra, dict):
            payload["data"].update(extra)

        try:
            if inspect.iscoroutinefunction(event_emitter):
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(event_emitter(payload))
                except RuntimeError:
                    if anyio is not None:
                        try:
                            anyio.from_thread.run(event_emitter, payload)
                            return
                        except Exception:
                            pass
                    asyncio.run(event_emitter(payload))
                return

            ret = event_emitter(payload)
            if inspect.isawaitable(ret):
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(ret)
                except RuntimeError:
                    asyncio.run(ret)
        except Exception as e:
            self._debug(f"status emit failed: {e}")

    def _valve_bool(self, value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        if isinstance(value, str):
            v = value.strip().lower()
            if v in ("1", "true", "yes", "on"):
                return True
            if v in ("0", "false", "no", "off"):
                return False
            return default
        return bool(value)

    def _show_reasoning_enabled(self) -> bool:
        return self._valve_bool(self.valves.SHOW_REASONING_CONTENT, True)

    def _force_non_stream_image_mode(self) -> bool:
        return self._valve_bool(self.valves.FORCE_NON_STREAM_IMAGE_MODE, True)

    def _retry_without_reasoning_enabled(self) -> bool:
        return self._valve_bool(self.valves.RETRY_WITHOUT_REASONING_ON_ERROR, True)

    def _retry_without_tools_enabled(self) -> bool:
        return self._valve_bool(self.valves.RETRY_WITHOUT_TOOLS_ON_INVALID_PARAMS, True)

    def _agent_pseudo_stream_status_enabled(self) -> bool:
        return self._valve_bool(self.valves.ENABLE_AGENT_PSEUDO_STREAM_STATUS, True)

    def _prompt_cache_markers_enabled(self) -> bool:
        return self._valve_bool(self.valves.ENABLE_PROMPT_CACHE_MARKERS, True)

    def _prompt_cache_min_text_chars(self) -> int:
        try:
            value = int(self.valves.PROMPT_CACHE_MIN_TEXT_CHARS or 0)
        except Exception:
            value = 1024
        return max(1, value)

    def _prompt_cache_max_markers(self) -> int:
        try:
            value = int(self.valves.PROMPT_CACHE_MAX_MARKERS or 0)
        except Exception:
            value = 4
        return max(1, value)

    def _gemini_cached_content_enabled(self) -> bool:
        return self._valve_bool(self.valves.ENABLE_GEMINI_CACHED_CONTENT, False)

    def _gemini_cache_api_key(self) -> str:
        return str(self.valves.GEMINI_CACHE_API_KEY or "").strip()

    def _gemini_cache_api_base(self) -> str:
        base = str(self.valves.GEMINI_CACHE_BASE_URL or "").strip().rstrip("/")
        return base or "https://generativelanguage.googleapis.com/v1beta"

    def _gemini_cache_ttl_secs(self) -> int:
        try:
            value = int(self.valves.GEMINI_CACHE_TTL_SECS or 0)
        except Exception:
            value = 3600
        return max(60, value)

    def _coerce_positive_int(self, value: Any) -> Optional[int]:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            iv = int(value)
            return iv if iv > 0 else None
        if isinstance(value, str):
            s = value.strip()
            if not s:
                return None
            if s.isdigit():
                iv = int(s)
                return iv if iv > 0 else None
        return None

    def _resolve_agent_settings(self, body: dict) -> dict:
        settings: Dict[str, Any] = {}
        agent_mode = False

        raw_auto_execute = self._body_param(body, "tools_to_auto_execute")
        if isinstance(raw_auto_execute, list):
            tools_to_auto_execute = [
                str(item).strip()
                for item in raw_auto_execute
                if isinstance(item, (str, int, float)) and str(item).strip()
            ]
            if tools_to_auto_execute:
                settings["tools_to_auto_execute"] = tools_to_auto_execute
                agent_mode = True
        elif isinstance(raw_auto_execute, str) and raw_auto_execute.strip():
            settings["tools_to_auto_execute"] = [raw_auto_execute.strip()]
            agent_mode = True

        if agent_mode:
            request_max_depth = self._coerce_positive_int(
                self._body_param(body, "max_agent_depth")
            )
            if isinstance(request_max_depth, int):
                settings["max_agent_depth"] = request_max_depth
            else:
                default_depth = self._coerce_positive_int(
                    self.valves.DEFAULT_MAX_AGENT_DEPTH
                )
                if isinstance(default_depth, int):
                    settings["max_agent_depth"] = default_depth

        settings["agent_mode"] = agent_mode
        return settings

    def _apply_agent_settings_to_payload(
        self, payload: dict, agent_settings: dict
    ) -> None:
        if not isinstance(payload, dict) or not isinstance(agent_settings, dict):
            return
        if isinstance(agent_settings.get("max_agent_depth"), int):
            payload["max_agent_depth"] = int(agent_settings["max_agent_depth"])
        tools_to_auto_execute = agent_settings.get("tools_to_auto_execute")
        if isinstance(tools_to_auto_execute, list) and tools_to_auto_execute:
            payload["tools_to_auto_execute"] = list(tools_to_auto_execute)

    def _result_has_tool_calls(self, result: Union[str, dict]) -> bool:
        if not isinstance(result, dict):
            return False

        choices = result.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                message = choice.get("message")
                if isinstance(message, dict):
                    if isinstance(message.get("tool_calls"), list) and message.get(
                        "tool_calls"
                    ):
                        return True
                if isinstance(choice.get("tool_calls"), list) and choice.get(
                    "tool_calls"
                ):
                    return True

        return bool(self._extract_responses_tool_calls(result))

    def _has_function_spec(self, function_specs: Any, name: str) -> bool:
        expected = str(name or "").strip()
        if not expected:
            return False
        for spec in self._ordered_function_specs(function_specs):
            if str(spec.get("name") or "").strip() == expected:
                return True
        return False

    def _should_force_image_tool_non_stream(
        self,
        body: dict,
        stream: bool,
        model: str,
        function_specs: Any,
    ) -> bool:
        if not stream or not self._force_non_stream_image_mode():
            return False
        if self._is_image_generation_model(model):
            return False
        if not self._has_function_spec(function_specs, "generate_image"):
            return False
        features = self._body_param(body, "features")
        if isinstance(features, dict):
            return self._valve_bool(features.get("image_generation"), False)
        # A selected generate_image tool is already enough evidence that this is
        # native image planning, even if an older caller did not forward features.
        return True

    def _office_to_pdf_enabled(self) -> bool:
        return self._valve_bool(self.valves.ENABLE_DOC_TO_PDF_CONVERSION, True)

    def _doc_to_pdf_timeout_secs(self) -> int:
        try:
            value = int(self.valves.DOC_TO_PDF_TIMEOUT_SECS or 0)
        except Exception:
            value = 120
        return max(10, value)

    def _api_base_url(self) -> str:
        base = str(self.valves.BIFROST_BASE_URL or "").strip().rstrip("/")
        if not base:
            return "https://api.maximhq.com/v1"
        if base.endswith("/v1"):
            return base
        return f"{base}/v1"

    def _headers(
        self,
        json_content: bool = True,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> dict:
        headers = {"Authorization": f"Bearer {self.valves.BIFROST_API_KEY}"}
        if json_content:
            headers["Content-Type"] = "application/json; charset=utf-8"
        if isinstance(extra_headers, dict):
            for key, value in extra_headers.items():
                if isinstance(key, str) and key and isinstance(value, str) and value:
                    headers[key] = value
        return headers

    def _utf8_json_bytes(self, payload: dict) -> bytes:
        # Keep payload ASCII-only to avoid gateway charset edge cases.
        return json.dumps(payload, ensure_ascii=True).encode("utf-8")

    def _as_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    def _coerce_json_object(self, value: Any) -> Optional[dict]:
        if isinstance(value, dict):
            return dict(value)
        if not isinstance(value, str):
            return None
        s = value.strip()
        if not s:
            return None
        try:
            parsed = json.loads(s)
        except Exception:
            return None
        return dict(parsed) if isinstance(parsed, dict) else None

    def _body_param(self, body: dict, key: str, default: Any = None) -> Any:
        if not isinstance(body, dict):
            return default
        if key in body:
            return body.get(key)
        params = body.get("params")
        if isinstance(params, dict):
            if key in params:
                return params.get(key)
            custom_params = params.get("custom_params")
            if isinstance(custom_params, dict) and key in custom_params:
                return custom_params.get(key)
        return default

    def _detect_cache_provider(self, model: Any) -> str:
        normalized = self._normalize_model(model)
        if self._is_anthropic_model_name(normalized):
            return "anthropic"
        if self._is_gemini_model_name(normalized):
            return "gemini"
        if self._is_gpt_model_name(normalized):
            return "openai"
        return "other"

    def _extract_cache_settings(self, body: dict, model: Any = "") -> dict:
        def _as_text(value: Any) -> str:
            if not isinstance(value, str):
                return ""
            return value.strip()

        def _as_bool(value: Any, default: bool = False) -> bool:
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                v = value.strip().lower()
                if v in ("1", "true", "yes", "on"):
                    return True
                if v in ("0", "false", "no", "off"):
                    return False
            return default

        prompt_cache_key = _as_text(self._body_param(body, "prompt_cache_key"))
        semantic_cache_key = _as_text(self._body_param(body, "semantic_cache_key"))
        enable_semantic_cache = _as_bool(
            self._body_param(body, "enable_semantic_cache"),
            default=bool(semantic_cache_key),
        )
        prompt_cache_retention = _as_text(
            self._body_param(body, "prompt_cache_retention")
        )
        cached_content = _as_text(self._body_param(body, "cached_content"))
        enable_prompt_caching = _as_bool(
            self._body_param(body, "enable_prompt_caching"),
            default=bool(prompt_cache_key),
        )
        cache_debug = _as_bool(self._body_param(body, "cache_debug"), default=False)
        no_store = _as_bool(self._body_param(body, "no_store"), default=False)

        return {
            "provider": self._detect_cache_provider(model),
            "prompt_cache_key": prompt_cache_key,
            "prompt_cache_retention": prompt_cache_retention,
            "cached_content": cached_content,
            "semantic_cache_key": semantic_cache_key,
            "enable_semantic_cache": enable_semantic_cache,
            "enable_prompt_caching": enable_prompt_caching,
            "cache_debug": cache_debug,
            "no_store": no_store,
        }

    def _stable_sort_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip().lower()
        return str(value).strip().lower()

    def _coerce_function_spec(self, spec: Any) -> Optional[dict]:
        if not isinstance(spec, dict):
            return None
        raw_spec = (
            spec.get("function") if isinstance(spec.get("function"), dict) else spec
        )
        name = str(raw_spec.get("name") or spec.get("name") or "").strip()
        if not name:
            return None
        params = raw_spec.get("parameters")
        if params is None:
            params = spec.get("parameters")
        if params is None:
            params = spec.get("input_schema")
        return {
            "name": name,
            "description": str(
                raw_spec.get("description") or spec.get("description") or ""
            ),
            "parameters": self._normalize_tool_parameters(params),
        }

    def _function_spec_sort_key(self, spec: Any) -> Tuple[str, str, str, str]:
        coerced = self._coerce_function_spec(spec)
        if not coerced:
            return ("", "", "", "")
        return (
            self._stable_sort_text(coerced.get("name")),
            str(coerced.get("name") or "").strip(),
            self._stable_sort_text(coerced.get("description")),
            json.dumps(coerced.get("parameters"), ensure_ascii=False, sort_keys=True),
        )

    def _ordered_function_specs(self, function_specs: Any) -> List[dict]:
        if not isinstance(function_specs, list):
            return []
        coerced = []
        for spec in function_specs:
            normalized = self._coerce_function_spec(spec)
            if normalized:
                coerced.append(normalized)
        return sorted(coerced, key=self._function_spec_sort_key)

    def _attachment_sort_key(
        self, attachment: Any
    ) -> Tuple[str, str, str, str, str, str, str, str]:
        if not isinstance(attachment, dict):
            return ("", "", "", "", "", "", "", "")
        chat_part = attachment.get("chat_part")
        responses_part = attachment.get("responses_part")
        return (
            self._stable_sort_text(attachment.get("openwebui_file_id")),
            self._stable_sort_text(attachment.get("filename")),
            self._stable_sort_text(attachment.get("name")),
            self._stable_sort_text(attachment.get("mime_type")),
            self._stable_sort_text(attachment.get("type")),
            self._stable_sort_text(attachment.get("kind")),
            self._stable_sort_text(
                chat_part.get("type") if isinstance(chat_part, dict) else ""
            ),
            self._stable_sort_text(
                responses_part.get("type") if isinstance(responses_part, dict) else ""
            ),
        )

    def _ordered_attachments(self, attachments: Any) -> List[dict]:
        if not isinstance(attachments, list):
            return []
        out = []
        for item in attachments:
            if isinstance(item, dict):
                out.append(item)
        return sorted(out, key=self._attachment_sort_key)

    def _stable_cache_tool_summary(self, function_specs: Any) -> List[dict]:
        out: List[dict] = []
        for spec in self._ordered_function_specs(function_specs):
            out.append(
                {
                    "name": str(spec.get("name") or "").strip(),
                    "description": str(spec.get("description") or "").strip(),
                    "parameters": self._normalize_tool_parameters(
                        spec.get("parameters")
                    ),
                }
            )
        return out

    def _stable_cache_attachment_summary(self, attachments: Any) -> List[dict]:
        out: List[dict] = []
        for item in self._ordered_attachments(attachments):
            summary: Dict[str, str] = {}
            for key in (
                "openwebui_file_id",
                "type",
                "kind",
                "mime_type",
                "filename",
                "name",
            ):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    summary[key] = value.strip()
            chat_part = item.get("chat_part")
            if isinstance(chat_part, dict):
                value = chat_part.get("type")
                if isinstance(value, str) and value.strip():
                    summary["chat_part_type"] = value.strip()
            responses_part = item.get("responses_part")
            if isinstance(responses_part, dict):
                value = responses_part.get("type")
                if isinstance(value, str) and value.strip():
                    summary["responses_part_type"] = value.strip()
            if summary:
                out.append(summary)
        return out

    def _generate_prompt_cache_key(
        self,
        model: Any,
        route_mode: str,
        system_message: Any,
        attachments: Any,
        function_specs: Any,
    ) -> str:
        payload = {
            "provider": "openai",
            "model": self._normalize_model(model),
            "system": self._content_to_text(system_message).strip(),
            "tools": self._stable_cache_tool_summary(function_specs),
            "attachments": self._stable_cache_attachment_summary(attachments),
        }
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        # Keep generated keys within the 64-char upstream limit while preserving
        # deterministic hashing over the same stable-prefix inputs.
        return f"owg:{digest[:60]}"

    def _prompt_cache_thread_id(self, body: dict) -> str:
        def _as_id(value: Any) -> str:
            if isinstance(value, bool):
                return ""
            if isinstance(value, (int, float)):
                value = str(value)
            if not isinstance(value, str):
                return ""
            return unicodedata.normalize("NFKC", value).strip()

        keys = ("chat_id", "thread_id", "session_id", "conversation_id")
        containers: List[dict] = []

        metadata = self._body_param(body, "metadata")
        if isinstance(metadata, dict):
            containers.append(metadata)
        if isinstance(body, dict):
            containers.append(body)
            params = body.get("params")
            if isinstance(params, dict):
                containers.append(params)
                custom_params = params.get("custom_params")
                if isinstance(custom_params, dict):
                    containers.append(custom_params)
            custom_params = body.get("custom_params")
            if isinstance(custom_params, dict):
                containers.append(custom_params)

        for container in containers:
            for key in keys:
                durable_id = _as_id(container.get(key))
                if durable_id:
                    return durable_id
        return ""

    def _thread_prompt_cache_key(self, thread_id: str) -> str:
        payload = {
            "provider": "openai",
            "thread_id": unicodedata.normalize("NFKC", str(thread_id)).strip(),
        }
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return f"owc:{digest[:60]}"

    def _default_prompt_cache_retention(self, model: Any) -> str:
        if not isinstance(model, str):
            return ""
        name = self._normalize_model(model).strip().lower()
        parts = [part for part in name.split("/") if part]
        tail = parts[-1] if parts else name
        if "mini" in tail:
            return ""
        if tail == "gpt-5.4" or tail.startswith("gpt-5.4-"):
            return "24h"
        if tail == "gpt-5.5" or tail.startswith("gpt-5.5-"):
            return "24h"
        return ""

    def _resolve_effective_cache_settings(
        self,
        body: dict,
        model: Any,
        route_mode: str,
        system_message: Any,
        messages: Any,
        attachments: Any,
        function_specs: Any,
    ) -> dict:
        settings = dict(self._extract_cache_settings(body, model=model))
        if not str(settings.get("prompt_cache_retention") or "").strip():
            default_retention = self._default_prompt_cache_retention(model)
            if default_retention:
                settings["prompt_cache_retention"] = default_retention
        prompt_cache_key = str(settings.get("prompt_cache_key") or "").strip()
        if prompt_cache_key:
            settings["prompt_cache_key"] = prompt_cache_key
            return settings
        if not self._is_gpt_model_name(model):
            return settings
        thread_id = self._prompt_cache_thread_id(body)
        if thread_id:
            settings["prompt_cache_key"] = self._thread_prompt_cache_key(thread_id)
            return settings
        settings["prompt_cache_key"] = self._generate_prompt_cache_key(
            model=model,
            route_mode=route_mode,
            system_message=system_message,
            attachments=attachments,
            function_specs=function_specs,
        )
        return settings

    def _cache_request_headers(
        self, body: Optional[dict] = None, model: Any = ""
    ) -> dict:
        settings = self._extract_cache_settings(body or {}, model=model)
        headers: Dict[str, str] = {}
        cache_key = settings.get("semantic_cache_key")
        if (
            settings.get("enable_semantic_cache") is True
            and isinstance(cache_key, str)
            and cache_key
        ):
            headers["x-bf-cache-key"] = cache_key
        if settings.get("cache_debug") is True:
            headers["x-bf-send-back-raw-response"] = "true"
        if settings.get("no_store") is True:
            headers["x-bf-no-store"] = "true"
        return headers

    def _retain_cache_metadata(self, normalized: Any, source: Any) -> Any:
        if not isinstance(normalized, dict) or not isinstance(source, dict):
            return normalized

        for key in ("extra_fields", "cache_debug", "cache"):
            if key in source:
                normalized[key] = copy.deepcopy(source.get(key))

        extra_fields = source.get("extra_fields")
        if (
            "cache_debug" not in normalized
            and isinstance(extra_fields, dict)
            and "cache_debug" in extra_fields
        ):
            normalized["cache_debug"] = copy.deepcopy(extra_fields.get("cache_debug"))

        return normalized

    def _normalize_model(self, raw_model_id: Any) -> str:
        model_id = str(raw_model_id or "").strip()
        if model_id.startswith("bifrostapi.") or model_id.startswith(
            "bifrost_unified."
        ):
            model_id = model_id.split(".", 1)[1]
        for prefix in ("bifrost/", "bifrost_unified.", "openai/"):
            if model_id.startswith(prefix):
                model_id = model_id[len(prefix) :]
        return model_id or "gpt-4.1-mini"

    def _is_anthropic_cache_model(self, model: str) -> bool:
        name = str(model or "").strip().lower()
        return (
            name.startswith("anthropic/") or "/anthropic/" in name or "claude" in name
        )

    def _is_direct_gemini_model(self, model: str) -> bool:
        name = str(model or "").strip().lower()
        return name.startswith("gemini/") or "/gemini" in name

    def _should_apply_prompt_cache_markers(self, body: dict, model: str) -> bool:
        if not self._prompt_cache_markers_enabled():
            return False
        if not self._is_anthropic_cache_model(model):
            return False
        explicit = self._body_param(body, "enable_prompt_caching")
        if isinstance(explicit, bool):
            return explicit
        return False

    def _cache_control_marker(self) -> dict:
        return {"type": "ephemeral"}

    def _gemini_cached_content_model_name(self, model: str) -> str:
        name = str(model or "").strip()
        if "/" in name:
            name = name.split("/", 1)[1]
        return f"models/{name}"

    def _gemini_cached_content_request_url(self) -> str:
        return f"{self._gemini_cache_api_base()}/cachedContents"

    def _gemini_cached_content_lookup_key(
        self, model: str, prompt_cache_key: str, system_text: str, prefix_text: str
    ) -> str:
        digest = hashlib.sha256(
            json.dumps(
                {
                    "model": model,
                    "prompt_cache_key": prompt_cache_key,
                    "system": system_text,
                    "prefix": prefix_text,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return digest

    def _split_cacheable_text(self, text: str, min_chars: int) -> Tuple[str, str]:
        value = str(text or "")
        if len(value.strip()) < min_chars:
            return "", value
        tail_keep = min(256, max(64, len(value) // 8))
        search_start = max(0, len(value) - max(512, tail_keep * 2))
        split_at = value.rfind("\n", search_start)
        if split_at <= 0 or split_at >= len(value) - 32:
            split_at = len(value) - tail_keep
        split_at = max(0, min(split_at, len(value)))
        prefix = value[:split_at].rstrip()
        suffix = value[split_at:].lstrip()
        if len(prefix.strip()) < min_chars or not suffix.strip():
            return "", value
        return prefix, suffix

    def _extract_simple_user_text_for_gemini_cache(
        self, messages: List[dict]
    ) -> Tuple[Optional[int], str]:
        if not isinstance(messages, list) or len(messages) != 1:
            return None, ""
        msg = messages[0]
        if not isinstance(msg, dict):
            return None, ""
        role = str(msg.get("role") or "").strip().lower()
        if role != "user":
            return None, ""
        content = msg.get("content")
        if isinstance(content, str):
            return 0, content
        if isinstance(content, list):
            text = self._content_to_text(content)
            non_text_parts = [
                part
                for part in content
                if isinstance(part, dict)
                and str(part.get("type") or "").strip().lower()
                not in ("text", "input_text", "output_text")
            ]
            if non_text_parts:
                return None, ""
            return 0, text
        return None, ""

    def _get_cached_content_entry(self, lookup_key: str) -> Optional[dict]:
        entry = self._gemini_cached_content_index.get(lookup_key)
        if not isinstance(entry, dict):
            return None
        expire_at = entry.get("expire_at")
        if isinstance(expire_at, (int, float)) and expire_at > time.time():
            return entry
        self._gemini_cached_content_index.pop(lookup_key, None)
        return None

    def _create_gemini_cached_content(
        self,
        model: str,
        system_text: str,
        prefix_text: str,
        prompt_cache_key: str,
    ) -> Optional[str]:
        api_key = self._gemini_cache_api_key()
        if not api_key:
            return None
        payload: Dict[str, Any] = {
            "model": self._gemini_cached_content_model_name(model),
            "contents": [{"role": "user", "parts": [{"text": prefix_text}]}],
            "ttl": f"{self._gemini_cache_ttl_secs()}s",
            "displayName": f"openwebui-{prompt_cache_key[:64]}",
        }
        if system_text.strip():
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}
        try:
            response = requests.post(
                self._gemini_cached_content_request_url(),
                headers={"Content-Type": "application/json; charset=utf-8"},
                params={"key": api_key},
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                timeout=(10, 60),
            )
            if response.status_code >= 400:
                self._debug(
                    f"gemini cached content create failed: HTTP {response.status_code} {response.text[:400]}"
                )
                return None
            data = response.json()
        except Exception as e:
            self._debug(
                f"gemini cached content create exception: {type(e).__name__}: {e}"
            )
            return None
        name = str(data.get("name") or "").strip()
        if not name:
            return None
        return name

    def _maybe_prepare_gemini_cached_content(
        self,
        body: dict,
        model: str,
        system_message: Any,
        messages: List[dict],
        function_specs: List[dict],
        attachments: List[dict],
    ) -> Tuple[dict, Any, List[dict]]:
        if not isinstance(body, dict):
            return body, system_message, messages
        if not self._gemini_cached_content_enabled():
            return body, system_message, messages
        if not self._is_direct_gemini_model(model):
            return body, system_message, messages
        if function_specs or attachments:
            return body, system_message, messages
        explicit_cached = self._body_param(body, "cached_content")
        if isinstance(explicit_cached, str) and explicit_cached.strip():
            return body, system_message, messages

        prompt_cache_key = self._body_param(body, "prompt_cache_key")
        if not isinstance(prompt_cache_key, str) or not prompt_cache_key.strip():
            return body, system_message, messages

        msg_index, user_text = self._extract_simple_user_text_for_gemini_cache(messages)
        if msg_index is None or not user_text.strip():
            return body, system_message, messages

        system_text = self._content_to_text(
            system_message.get("content")
            if isinstance(system_message, dict)
            else system_message
        ).strip()
        prefix_text, suffix_text = self._split_cacheable_text(
            user_text, self._prompt_cache_min_text_chars()
        )
        if not prefix_text:
            return body, system_message, messages

        lookup_key = self._gemini_cached_content_lookup_key(
            model, prompt_cache_key.strip(), system_text, prefix_text
        )
        entry = self._get_cached_content_entry(lookup_key)
        cache_name = str(entry.get("name") or "").strip() if entry else ""
        if not cache_name:
            cache_name = (
                self._create_gemini_cached_content(
                    model=model,
                    system_text=system_text,
                    prefix_text=prefix_text,
                    prompt_cache_key=prompt_cache_key.strip(),
                )
                or ""
            )
            if not cache_name:
                return body, system_message, messages
            self._gemini_cached_content_index[lookup_key] = {
                "name": cache_name,
                "expire_at": time.time() + self._gemini_cache_ttl_secs(),
            }

        new_body = copy.deepcopy(body)
        new_body["cached_content"] = cache_name
        new_messages = copy.deepcopy(messages or [])
        msg = dict(new_messages[msg_index])
        msg["content"] = suffix_text
        new_messages[msg_index] = msg
        return new_body, None, new_messages

    def _mark_chat_content_for_cache(
        self, content: Any, min_chars: int
    ) -> Tuple[Any, bool]:
        if isinstance(content, str):
            if len(content.strip()) >= min_chars:
                return (
                    [
                        {
                            "type": "text",
                            "text": content,
                            "cache_control": self._cache_control_marker(),
                        }
                    ],
                    True,
                )
            return content, False

        if isinstance(content, list):
            out: List[Any] = []
            marked = False
            for part in content:
                if isinstance(part, dict):
                    cloned = copy.deepcopy(part)
                    ptype = str(cloned.get("type") or "").strip().lower()
                    text = cloned.get("text")
                    if (
                        not marked
                        and ptype in ("text", "input_text", "output_text")
                        and isinstance(text, str)
                        and len(text.strip()) >= min_chars
                        and not isinstance(cloned.get("cache_control"), dict)
                    ):
                        cloned["cache_control"] = self._cache_control_marker()
                        marked = True
                    out.append(cloned)
                else:
                    out.append(part)
            return out, marked

        return content, False

    def _mark_responses_content_for_cache(
        self, content: Any, min_chars: int
    ) -> Tuple[Any, bool]:
        if not isinstance(content, list):
            return content, False
        out: List[Any] = []
        marked = False
        for part in content:
            if isinstance(part, dict):
                cloned = copy.deepcopy(part)
                ptype = str(cloned.get("type") or "").strip().lower()
                text = cloned.get("text")
                if (
                    not marked
                    and ptype in ("input_text", "output_text", "text")
                    and isinstance(text, str)
                    and len(text.strip()) >= min_chars
                    and not isinstance(cloned.get("cache_control"), dict)
                ):
                    cloned["cache_control"] = self._cache_control_marker()
                    marked = True
                out.append(cloned)
            else:
                out.append(part)
        return out, marked

    def _mark_tools_for_cache(self, tools: Any, remaining: int) -> int:
        if remaining <= 0 or not isinstance(tools, list):
            return remaining
        for tool in tools:
            if remaining <= 0:
                break
            if not isinstance(tool, dict):
                continue
            if isinstance(tool.get("cache_control"), dict):
                continue
            tool["cache_control"] = self._cache_control_marker()
            remaining -= 1
        return remaining

    def _apply_prompt_cache_markers(
        self, payload: dict, route_mode: str, body: dict, model: str
    ) -> None:
        if not isinstance(payload, dict):
            return
        if not self._should_apply_prompt_cache_markers(body, model):
            return

        remaining = self._prompt_cache_max_markers()
        min_chars = self._prompt_cache_min_text_chars()

        if route_mode == "chat":
            messages = payload.get("messages")
            if isinstance(messages, list):
                for message in messages:
                    if remaining <= 0:
                        break
                    if not isinstance(message, dict):
                        continue
                    role = str(message.get("role") or "").strip().lower()
                    if role not in ("system", "user"):
                        continue
                    new_content, marked = self._mark_chat_content_for_cache(
                        message.get("content"), min_chars
                    )
                    if marked:
                        message["content"] = new_content
                        remaining -= 1
            if remaining > 0:
                remaining = self._mark_tools_for_cache(payload.get("tools"), remaining)
            return

        if route_mode == "responses":
            input_items = payload.get("input")
            if isinstance(input_items, list):
                for item in input_items:
                    if remaining <= 0:
                        break
                    if not isinstance(item, dict):
                        continue
                    if str(item.get("type") or "").strip().lower() != "message":
                        continue
                    role = str(item.get("role") or "").strip().lower()
                    if role not in ("system", "user"):
                        continue
                    new_content, marked = self._mark_responses_content_for_cache(
                        item.get("content"), min_chars
                    )
                    if marked:
                        item["content"] = new_content
                        remaining -= 1
            if remaining > 0:
                self._mark_tools_for_cache(payload.get("tools"), remaining)

    def _resolve_route_mode(self, body: dict) -> str:
        request_mode = (
            str(
                self._body_param(
                    body, "route_mode", self._body_param(body, "api_mode", "")
                )
            )
            .strip()
            .lower()
        )
        valve_mode = str(self.valves.ROUTE_MODE or "").strip().lower()
        mode = request_mode or valve_mode or "auto"
        if mode not in ("chat", "responses", "auto"):
            mode = "auto"

        if mode == "auto" and not request_mode:
            model = self._normalize_model(body.get("model", "")).lower()
            if model.startswith("zenmuxoai/google/gemini"):
                return "chat"
            # 新增：让所有 deepseek 系列模型都走 chat 端点
            if "deepseek" in model:
                return "chat"
            if model.startswith("zenmuxoai/z-ai/glm") or model.startswith("z-ai/glm"):
                return "responses"

        return mode

    # -------------------------------------------------------------------------
    # Model list
    # -------------------------------------------------------------------------

    def _fetch_remote_models(self) -> List[dict]:
        url = f"{self._api_base_url()}/models"
        try:
            response = requests.get(
                url,
                headers=self._headers(json_content=False),
                timeout=(10, 30),
            )
            if response.status_code != 200:
                self._debug(f"models fetch failed: HTTP {response.status_code}")
                return []
            data = response.json()
        except Exception as e:
            self._debug(f"models fetch exception: {type(e).__name__}: {e}")
            return []

        out: List[dict] = []
        seen = set()
        for item in data.get("data", []) or []:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or "").strip()
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            model_name = str(item.get("name") or "").strip() or model_id
            out.append({"id": model_id, "name": model_name})
        out.sort(key=lambda x: x["id"])
        return out[:500]

    def _fallback_models(self) -> List[dict]:
        return [
            {"id": "gpt-4.1", "name": "gpt-4.1"},
            {"id": "gpt-4.1-mini", "name": "gpt-4.1-mini"},
            {"id": "gpt-5", "name": "gpt-5"},
            {"id": "gpt-5-mini", "name": "gpt-5-mini"},
            {"id": "claude-3-7-sonnet", "name": "claude-3-7-sonnet"},
            {"id": "gemini-2.5-flash", "name": "gemini-2.5-flash"},
        ]

    # -------------------------------------------------------------------------
    # Tool conversion
    # -------------------------------------------------------------------------

    def _clean_tool_schema_node(self, schema: Any) -> None:
        if not isinstance(schema, dict):
            return

        any_of = schema.get("anyOf")
        if isinstance(any_of, list):
            non_null_types = [
                item
                for item in any_of
                if isinstance(item, dict) and item.get("type") != "null"
            ]
            if len(non_null_types) == 1:
                schema.update(copy.deepcopy(non_null_types[0]))
                schema.pop("anyOf", None)
            else:
                schema["anyOf"] = non_null_types

        if "default" in schema and schema.get("default") is None:
            del schema["default"]

        if (
            "type" not in schema
            and "anyOf" not in schema
            and "properties" not in schema
        ):
            schema["type"] = "string"

        properties = schema.get("properties")
        if isinstance(properties, dict):
            for prop_schema in properties.values():
                self._clean_tool_schema_node(prop_schema)

        items = schema.get("items")
        if items is not None:
            self._clean_tool_schema_node(items)

    def _normalize_tool_parameters(self, params: Any) -> dict:
        if not isinstance(params, dict):
            return {"type": "object", "properties": {}}

        schema = copy.deepcopy(params)
        schema_type = str(schema.get("type") or "").strip().lower()
        if not schema_type:
            schema["type"] = "object"
            schema_type = "object"

        # Bifrost rejects object schemas that omit `properties`.
        if schema_type == "object" and not isinstance(schema.get("properties"), dict):
            schema["properties"] = {}

        if isinstance(schema.get("required"), list):
            schema["required"] = [k for k in schema["required"] if isinstance(k, str)]

        self._clean_tool_schema_node(schema)
        return schema

    def _single_selected_mcp_prefix(self, metadata: Any) -> Optional[str]:
        if not isinstance(metadata, dict):
            return None
        tool_ids = metadata.get("tool_ids")
        if not isinstance(tool_ids, list) or len(tool_ids) != 1:
            return None
        tool_id = tool_ids[0]
        if not isinstance(tool_id, str) or not tool_id.startswith("server:mcp:"):
            return None
        server_part = tool_id[len("server:mcp:") :]
        if "|" in server_part:
            server_part = server_part.split("|", 1)[0]
        server_id = str(server_part or "").strip()
        if not server_id:
            return None
        return f"{server_id}_"

    def _metadata_tool_ids(self, metadata: Any) -> set[str]:
        if not isinstance(metadata, dict):
            return set()
        tool_ids = metadata.get("tool_ids")
        if not isinstance(tool_ids, list):
            return set()

        out: set[str] = set()
        for raw in tool_ids:
            if not isinstance(raw, str):
                continue
            tool_id = raw.split("|", 1)[0].strip()
            if tool_id:
                out.add(tool_id)
        return out

    def _is_bifrost_mcp_tool_selected(self, metadata: Any) -> bool:
        tool_ids = self._metadata_tool_ids(metadata)
        if not tool_ids:
            return False
        return any(
            str(tool_id).strip().lower() == "server:mcp:bifrostmcp"
            for tool_id in tool_ids
        )

    def _is_bifrost_meta_tool_name(self, name: Any) -> bool:
        if not isinstance(name, str):
            return False
        key = re.sub(r"[^a-z0-9]", "", name.strip().lower())
        return key in {
            "listtoolfiles",
            "readtoolfile",
            "gettooldocs",
            "executetoolcode",
            "bifrostmcplisttoolfiles",
            "bifrostmcpreadtoolfile",
            "bifrostmcpgettooldocs",
            "bifrostmcpexecutetoolcode",
        }

    def _maybe_prefix_mcp_tool_name(self, name: str, metadata: Any) -> str:
        normalized = str(name or "").strip()
        if not normalized:
            return normalized
        if self._is_bifrost_meta_tool_name(normalized):
            selected_prefix = self._single_selected_mcp_prefix(metadata)
            if isinstance(selected_prefix, str) and selected_prefix:
                # Keep prefixing idempotent: avoid turning
                # `BifrostMCP_listToolFiles` into
                # `BifrostMCP_BifrostMCP_listToolFiles`.
                lowered = normalized.lower()
                selected_prefix_lower = selected_prefix.lower()
                if lowered.startswith(selected_prefix_lower):
                    return normalized
                if "_" in normalized:
                    return normalized
                return f"{selected_prefix}{normalized}"
        return normalized

    def _collect_function_specs(
        self, body_tools: Any, runtime_tools: Any, metadata: Any = None
    ) -> List[dict]:
        candidates: List[dict] = []

        if isinstance(body_tools, list) and body_tools:
            for tool in body_tools:
                if not isinstance(tool, dict):
                    continue
                if tool.get("type") == "function" and isinstance(
                    tool.get("function"), dict
                ):
                    fn = tool["function"]
                    name = self._maybe_prefix_mcp_tool_name(
                        str(fn.get("name") or "").strip(), metadata
                    )
                    if not name:
                        continue
                    params = fn.get("parameters")
                    candidates.append(
                        {
                            "name": name,
                            "description": str(fn.get("description") or ""),
                            "parameters": self._normalize_tool_parameters(params),
                        }
                    )
                    continue
                if tool.get("type") == "function" and isinstance(tool.get("name"), str):
                    name = self._maybe_prefix_mcp_tool_name(
                        str(tool.get("name") or "").strip(), metadata
                    )
                    if not name:
                        continue
                    params = tool.get("parameters")
                    candidates.append(
                        {
                            "name": name,
                            "description": str(tool.get("description") or ""),
                            "parameters": self._normalize_tool_parameters(params),
                        }
                    )
                    continue
                if isinstance(tool.get("name"), str) and isinstance(
                    tool.get("input_schema"), dict
                ):
                    name = self._maybe_prefix_mcp_tool_name(
                        str(tool.get("name") or "").strip(), metadata
                    )
                    if not name:
                        continue
                    candidates.append(
                        {
                            "name": name,
                            "description": str(tool.get("description") or ""),
                            "parameters": self._normalize_tool_parameters(
                                tool.get("input_schema")
                            ),
                        }
                    )
        elif isinstance(runtime_tools, dict):
            for item in runtime_tools.values():
                if not isinstance(item, dict):
                    continue
                spec = item.get("spec")
                if not isinstance(spec, dict):
                    continue
                name = str(spec.get("name") or "").strip()
                if not name:
                    continue
                if self._is_bifrost_meta_tool_name(
                    name
                ) and not self._is_bifrost_mcp_tool_selected(metadata):
                    continue
                params = spec.get("parameters")
                candidates.append(
                    {
                        "name": name,
                        "description": str(spec.get("description") or ""),
                        "parameters": self._normalize_tool_parameters(params),
                    }
                )

        # Inject default web_search tool if enabled (before dedup so user-provided web_search takes priority)
        if self._valve_bool(self.valves.ENABLE_DEFAULT_WEB_SEARCH_TOOL, True):
            candidates.append({
                "name": "web_search",
                "description": "Search the internet for current, up-to-date information. ONLY use this tool when: (1) the user explicitly asks you to search the web, or (2) the question clearly requires real-time or recent information beyond your knowledge cutoff. DO NOT use for general knowledge, coding, math, creative writing, etc. IMPORTANT: When referencing information from search results in your response, cite the source using the format [N] where N is the result number shown in the search results. For example: 'According to the report [1], the market has grown 20%. Multiple sources agree [2,3].' Always include at least one citation when using search results.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query. Be specific and concise."
                        },
                        "explanation": {
                            "type": "string",
                            "description": "Briefly explain why web search is necessary for this query."
                        }
                    },
                    "required": ["query"]
                }
            })

        deduped: List[dict] = []
        seen = set()
        for spec in candidates:
            name = str(spec.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            deduped.append(spec)
        return self._ordered_function_specs(deduped)

    def _tools_for_chat(self, function_specs: List[dict]) -> List[dict]:
        out: List[dict] = []
        for spec in self._ordered_function_specs(function_specs):
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": spec.get("name"),
                        "description": spec.get("description", ""),
                        "parameters": self._normalize_tool_parameters(
                            spec.get("parameters")
                        ),
                    },
                }
            )
        return out

    # Native Responses API tool types that should NOT be wrapped as functions
    _NATIVE_RESPONSES_TOOL_TYPES: frozenset = frozenset({"web_search"})

    def _tools_for_responses(self, function_specs: List[dict]) -> List[dict]:
        out: List[dict] = []
        for spec in self._ordered_function_specs(function_specs):
            name = str(spec.get("name") or "").strip()
            # Native tool types: convert to bare Responses API format (e.g. {"type": "web_search"})
            if name in self._NATIVE_RESPONSES_TOOL_TYPES:
                out.append({"type": name})
                continue
            # Standard function tool
            out.append(
                {
                    "type": "function",
                    "name": name,
                    "description": spec.get("description", ""),
                    "parameters": self._normalize_tool_parameters(
                        spec.get("parameters")
                    ),
                }
            )
        return out

    def _tool_choice_for_chat(self, tool_choice: Any) -> Any:
        if tool_choice is None:
            return None
        if isinstance(tool_choice, str):
            val = tool_choice.strip().lower()
            return val if val in ("auto", "none", "required") else "auto"
        if isinstance(tool_choice, dict):
            t = str(tool_choice.get("type") or "").strip().lower()
            if t in ("auto", "none", "required"):
                return t
            if t in ("function", "tool"):
                fn = (
                    tool_choice.get("function")
                    if isinstance(tool_choice.get("function"), dict)
                    else {}
                )
                name = str(fn.get("name") or tool_choice.get("name") or "").strip()
                if name:
                    return {"type": "function", "function": {"name": name}}
        return None

    def _tool_choice_for_responses(self, tool_choice: Any) -> Any:
        if tool_choice is None:
            return None
        if isinstance(tool_choice, str):
            val = tool_choice.strip().lower()
            return val if val in ("auto", "none", "required") else "auto"
        if isinstance(tool_choice, dict):
            t = str(tool_choice.get("type") or "").strip().lower()
            if t in ("auto", "none", "required"):
                return t
            if t in ("function", "tool"):
                fn = (
                    tool_choice.get("function")
                    if isinstance(tool_choice.get("function"), dict)
                    else {}
                )
                name = str(fn.get("name") or tool_choice.get("name") or "").strip()
                if name:
                    return {"type": "function", "name": name}
        return None

    # -------------------------------------------------------------------------
    # Attachment discovery + conversion
    # -------------------------------------------------------------------------

    def _collect_file_candidates(
        self, body: dict, files_arg: Any, metadata_arg: Any
    ) -> List[dict]:
        candidates: List[dict] = []

        if isinstance(files_arg, list):
            candidates.extend(item for item in files_arg if isinstance(item, dict))

        if isinstance(metadata_arg, dict) and isinstance(
            metadata_arg.get("files"), list
        ):
            candidates.extend(
                item
                for item in (metadata_arg.get("files") or [])
                if isinstance(item, dict)
            )

        if isinstance(body.get("metadata"), dict) and isinstance(
            body["metadata"].get("files"), list
        ):
            candidates.extend(
                item
                for item in (body["metadata"].get("files") or [])
                if isinstance(item, dict)
            )

        if isinstance(body.get("files"), list):
            candidates.extend(
                item for item in (body.get("files") or []) if isinstance(item, dict)
            )

        out: List[dict] = []
        for item in candidates:
            if item.get("_adaptive_excluded") is True:
                continue
            out.append(item)
        return out

    def _extract_openwebui_file_id(self, item: dict) -> str:
        if not isinstance(item, dict):
            return ""

        fid = str(item.get("id") or "").strip()
        if fid:
            return fid

        nested = item.get("file")
        if isinstance(nested, dict):
            fid = str(nested.get("id") or "").strip()
            if fid:
                return fid

        for key in ("url", "file_url"):
            value = str(item.get(key) or "").strip()
            if not value:
                continue
            if "/" not in value and "\\" not in value and " " not in value:
                return value
            parsed_path = value
            if value.lower().startswith(("http://", "https://", "gs://")):
                try:
                    parsed_path = urlparse(value).path or value
                except Exception:
                    parsed_path = value
            for pattern in (
                r"/api(?:/v1)?/files/([^/?#]+)(?:/content(?:/[^?#]*)?)?(?:[?#].*)?$",
                r"/files/([^/?#]+)(?:/content(?:/[^?#]*)?)?(?:[?#].*)?$",
            ):
                match = re.search(pattern, parsed_path, flags=re.IGNORECASE)
                if match:
                    candidate = str(match.group(1) or "").strip()
                    if candidate:
                        return candidate
        return ""

    def _can_read_openwebui_file(
        self, file_record: Any, file_id: str, user: Any
    ) -> bool:
        if not isinstance(user, dict):
            return True
        role = str(user.get("role") or "").strip().lower()
        if role == "admin":
            return True

        user_id = str(user.get("id") or "").strip()
        if not user_id:
            return False

        owner_id = str(getattr(file_record, "user_id", "") or "").strip()
        if owner_id and owner_id == user_id:
            return True

        try:
            from open_webui.models.access_grants import AccessGrants

            return bool(
                AccessGrants.has_access(
                    user_id=user_id,
                    resource_type="file",
                    resource_id=str(file_id),
                    permission="read",
                )
            )
        except Exception:
            return False

    def _resolve_openwebui_file_by_id(self, file_id: str, user: Any) -> Optional[dict]:
        try:
            from open_webui.models.files import Files
            from open_webui.storage.provider import Storage
        except Exception:
            return None

        record = Files.get_file_by_id(file_id)
        if not record or not getattr(record, "path", None):
            return None

        if not self._can_read_openwebui_file(record, file_id, user):
            self._debug(f"skip inaccessible file id={file_id}")
            return None

        try:
            local_path = Storage.get_file(record.path)
        except Exception:
            return None

        if not local_path or not os.path.isfile(local_path):
            return None

        filename = getattr(record, "filename", None) or os.path.basename(local_path)
        content_type = (
            ((getattr(record, "meta", {}) or {}).get("content_type"))
            or mimetypes.guess_type(filename)[0]
            or "application/octet-stream"
        )

        return {
            "openwebui_file_id": file_id,
            "local_path": local_path,
            "filename": filename,
            "content_type": content_type,
        }

    def _resolve_file_item(self, item: dict, user: Any) -> Optional[dict]:
        file_id = self._extract_openwebui_file_id(item)
        if not file_id:
            return None

        resolved = self._resolve_openwebui_file_by_id(file_id, user)
        if not resolved:
            return None

        name = item.get("name")
        if isinstance(name, str) and name.strip():
            resolved["filename"] = name.strip()

        content_type = item.get("content_type")
        if isinstance(content_type, str) and content_type.strip():
            resolved["content_type"] = content_type.strip()

        return resolved

    def _prepare_attachments(
        self, file_items: List[dict], user: Any, model: Any = None
    ) -> List[dict]:
        attachments: List[dict] = []
        seen_ids = set()
        normalized_model = self._normalize_model(model or "").lower()
        suppress_document_parts = normalized_model.startswith(
            "zenmuxoai/z-ai/glm"
        ) or normalized_model.startswith("z-ai/glm")

        for item in file_items:
            if not isinstance(item, dict):
                continue

            resolved = self._resolve_file_item(item, user)
            if not resolved:
                # Keep best-effort support for metadata-only text snippets.
                text = self._content_to_text(item.get("content"))
                if text:
                    attachments.append(
                        {
                            "kind": "text",
                            "name": str(
                                item.get("name") or item.get("id") or "context"
                            ),
                            "chat_part": None,
                            "responses_part": None,
                            "fallback_text": text[: self._max_text_fallback_chars()],
                        }
                    )
                continue

            file_id = str(resolved.get("openwebui_file_id") or "").strip()
            if file_id and file_id in seen_ids:
                continue
            if file_id:
                seen_ids.add(file_id)

            office_file = self._is_doc_or_docx_file(resolved)
            pdf_file = self._is_pdf_file(resolved)
            office_document_file = self._is_office_document_file(resolved)
            working_resolved = resolved
            office_converted = False
            temp_path = ""
            try:
                if office_document_file:
                    continue
                if office_file and self._office_to_pdf_enabled():
                    try:
                        working_resolved = self._convert_doc_or_docx_to_pdf(resolved)
                        office_converted = True
                        temp_path = str(
                            working_resolved.get("_temporary_local_path") or ""
                        )
                    except Exception as e:
                        self._debug(f"office->pdf conversion failed: {e}")

                name = str(working_resolved.get("filename") or "attachment")
                safe_name = self._safe_filename(name)

                if self._is_image_file(working_resolved):
                    data_url = self._build_data_url(working_resolved)
                    attachments.append(
                        {
                            "kind": "image",
                            "name": name,
                            "chat_part": {
                                "type": "image_url",
                                "image_url": {"url": data_url},
                            },
                            "responses_part": {
                                "type": "input_image",
                                "image_url": data_url,
                            },
                            "fallback_text": None,
                        }
                    )
                    continue

                # For Office files, prefer converted PDF; if conversion failed, fallback to text.
                if office_file and (not office_converted):
                    text = self._extract_text_fallback(resolved)
                    if text:
                        attachments.append(
                            {
                                "kind": "text",
                                "name": str(resolved.get("filename") or "attachment"),
                                "chat_part": None,
                                "responses_part": None,
                                "fallback_text": text,
                            }
                        )
                        continue

                if self._is_text_like_file(working_resolved):
                    text = self._extract_text_fallback(working_resolved)
                    if text:
                        attachments.append(
                            {
                                "kind": "text",
                                "name": name,
                                "chat_part": None,
                                "responses_part": None,
                                "fallback_text": text,
                            }
                        )
                        continue

                if suppress_document_parts:
                    continue

                data_url = self._build_data_url(working_resolved)
                attachments.append(
                    {
                        "kind": "document",
                        "name": name,
                        "chat_part": {
                            "type": "file",
                            "file": {"filename": safe_name, "file_data": data_url},
                        },
                        "responses_part": {
                            "type": "input_file",
                            "filename": safe_name,
                            "file_data": data_url,
                        },
                        "fallback_text": None,
                    }
                )
            except Exception as e:
                name = str(resolved.get("filename") or "attachment")
                self._debug(f"attachment encode failed for {name}: {e}")
                fallback_source = resolved if office_file else working_resolved
                text = self._extract_text_fallback(fallback_source)
                if text:
                    attachments.append(
                        {
                            "kind": "text",
                            "name": name,
                            "chat_part": None,
                            "responses_part": None,
                            "fallback_text": text,
                        }
                    )
            finally:
                if temp_path:
                    self._cleanup_temporary_file(temp_path)

        return self._ordered_attachments(attachments)

    def _attach_attachments_to_last_user_chat(
        self, chat_messages: List[dict], attachments: List[dict]
    ) -> List[dict]:
        if not attachments:
            return chat_messages

        out = [dict(m) if isinstance(m, dict) else m for m in (chat_messages or [])]
        idx = None
        for i in range(len(out) - 1, -1, -1):
            msg = out[i]
            if (
                isinstance(msg, dict)
                and str(msg.get("role") or "").strip().lower() == "user"
            ):
                idx = i
                break

        if idx is None:
            out.append({"role": "user", "content": []})
            idx = len(out) - 1

        target = out[idx]
        content = target.get("content")
        content_parts: List[dict] = []
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    content_parts.append(part)
                elif isinstance(part, str) and part:
                    content_parts.append({"type": "text", "text": part})
        elif isinstance(content, str) and content:
            content_parts.append({"type": "text", "text": content})

        for attachment in self._ordered_attachments(attachments):
            chat_part = attachment.get("chat_part")
            if isinstance(chat_part, dict):
                content_parts.append(chat_part)
                continue

            fallback_text = str(attachment.get("fallback_text") or "").strip()
            if fallback_text:
                content_parts.append(
                    {
                        "type": "text",
                        "text": f"[Attachment: {attachment.get('name') or 'file'}]\n{fallback_text}",
                    }
                )

        target["content"] = content_parts
        out[idx] = target
        return out

    def _attach_attachments_to_last_user_responses(
        self, input_items: List[dict], attachments: List[dict]
    ) -> List[dict]:
        if not attachments:
            return input_items

        out = [
            dict(item) if isinstance(item, dict) else item
            for item in (input_items or [])
        ]
        idx = None
        for i in range(len(out) - 1, -1, -1):
            item = out[i]
            if (
                isinstance(item, dict)
                and str(item.get("type") or "").strip().lower() == "message"
                and str(item.get("role") or "").strip().lower() == "user"
            ):
                idx = i
                break

        if idx is None:
            out.append({"type": "message", "role": "user", "content": []})
            idx = len(out) - 1

        target = out[idx]
        content = target.get("content")
        content_parts: List[dict] = []
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    content_parts.append(part)

        for attachment in self._ordered_attachments(attachments):
            responses_part = attachment.get("responses_part")
            if isinstance(responses_part, dict):
                content_parts.append(responses_part)
                continue
            fallback_text = str(attachment.get("fallback_text") or "").strip()
            if fallback_text:
                content_parts.append(
                    {
                        "type": "input_text",
                        "text": f"[Attachment: {attachment.get('name') or 'file'}]\n{fallback_text}",
                    }
                )

        target["content"] = content_parts
        out[idx] = target
        return out

    def _max_inline_bytes(self) -> int:
        try:
            value = int(self.valves.INLINE_FILE_MAX_BYTES or 0)
            return max(1, value)
        except Exception:
            return 20 * 1024 * 1024

    def _max_text_fallback_chars(self) -> int:
        try:
            value = int(self.valves.ATTACHMENT_TEXT_FALLBACK_MAX_CHARS or 0)
            return max(200, value)
        except Exception:
            return 60000

    def _safe_filename(self, filename: str) -> str:
        raw = os.path.basename(str(filename or "")).strip()
        if not raw:
            raw = "upload"

        root, ext = os.path.splitext(raw)
        ascii_root = (
            unicodedata.normalize("NFKD", root)
            .encode("ascii", "ignore")
            .decode("ascii")
        )
        ascii_root = "".join(
            ch if (ch.isalnum() or ch in ("-", "_", ".")) else "_" for ch in ascii_root
        ).strip("._")
        if not ascii_root:
            ascii_root = "upload"
        out = f"{ascii_root}{ext}"
        if len(out) > 160:
            out = f"{ascii_root[:120]}{ext}"
        return out

    def _is_image_file(self, resolved: dict) -> bool:
        content_type = str(resolved.get("content_type") or "").strip().lower()
        if content_type.startswith("image/"):
            return True
        filename = str(resolved.get("filename") or "").lower()
        return filename.endswith(
            (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic")
        )

    def _is_docx_file(self, resolved: dict) -> bool:
        content_type = str(resolved.get("content_type") or "").strip().lower()
        if (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            in content_type
        ):
            return True
        filename = str(resolved.get("filename") or "").strip().lower()
        return filename.endswith(".docx")

    def _is_doc_file(self, resolved: dict) -> bool:
        content_type = str(resolved.get("content_type") or "").strip().lower()
        if "application/msword" in content_type:
            return True
        filename = str(resolved.get("filename") or "").strip().lower()
        return filename.endswith(".doc")

    def _is_doc_or_docx_file(self, resolved: dict) -> bool:
        return self._is_docx_file(resolved) or self._is_doc_file(resolved)

    def _is_pdf_file(self, resolved: dict) -> bool:
        content_type = str(resolved.get("content_type") or "").strip().lower()
        if content_type == "application/pdf":
            return True
        filename = str(resolved.get("filename") or "").strip().lower()
        return filename.endswith(".pdf")

    def _is_office_document_file(self, resolved: dict) -> bool:
        content_type = str(resolved.get("content_type") or "").strip().lower()
        if content_type in {
            "application/pdf",
            "application/msword",
            "application/vnd.ms-excel",
            "application/vnd.ms-powerpoint",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        }:
            return True
        filename = str(resolved.get("filename") or "").strip().lower()
        return filename.endswith(
            (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx")
        )

    def _is_text_like_file(self, resolved: dict) -> bool:
        content_type = str(resolved.get("content_type") or "").strip().lower()
        if content_type.startswith("text/"):
            return True
        filename = str(resolved.get("filename") or "").lower()
        return filename.endswith(
            (
                ".txt",
                ".md",
                ".markdown",
                ".json",
                ".csv",
                ".tsv",
                ".yaml",
                ".yml",
                ".xml",
                ".html",
                ".htm",
                ".log",
                ".py",
                ".js",
                ".ts",
                ".java",
                ".go",
                ".rs",
                ".sql",
            )
        )

    def _read_binary(self, resolved: dict) -> bytes:
        path = str(resolved.get("local_path") or "")
        if not path or not os.path.isfile(path):
            raise RuntimeError("Local file not found")
        size = os.path.getsize(path)
        limit = self._max_inline_bytes()
        if size > limit:
            raise RuntimeError(f"File exceeds inline limit: {size} > {limit} bytes")
        with open(path, "rb") as file:
            data = file.read()
        if not data:
            raise RuntimeError("Empty file")
        return data

    def _build_data_url(self, resolved: dict) -> str:
        content_type = (
            str(resolved.get("content_type") or "").strip()
            or "application/octet-stream"
        )
        data = self._read_binary(resolved)
        return f"data:{content_type};base64,{base64.b64encode(data).decode('ascii')}"

    def _cleanup_temporary_file(self, path: str) -> None:
        try:
            if path and os.path.isfile(path):
                os.remove(path)
        except Exception:
            pass

    def _convert_doc_or_docx_to_pdf(self, resolved: dict) -> dict:
        src_path = str(resolved.get("local_path") or "")
        if not src_path or not os.path.isfile(src_path):
            raise RuntimeError("office source file not found")
        if not self._is_doc_or_docx_file(resolved):
            raise RuntimeError("source is not a doc/docx file")

        soffice_bin = shutil.which("soffice") or shutil.which("libreoffice")
        if not soffice_bin:
            raise RuntimeError("soffice/libreoffice not found in PATH")

        workdir = tempfile.mkdtemp(prefix="bifrost_doc2pdf_")
        src_name = os.path.basename(src_path)
        work_src = os.path.join(workdir, src_name)
        shutil.copy2(src_path, work_src)

        cmd = [
            soffice_bin,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            workdir,
            work_src,
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self._doc_to_pdf_timeout_secs(),
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"office->pdf failed rc={proc.returncode}: {(proc.stderr or '')[:200]}"
            )

        expected_pdf = os.path.join(workdir, f"{os.path.splitext(src_name)[0]}.pdf")
        pdf_path = expected_pdf
        if not os.path.isfile(pdf_path):
            candidates = [
                os.path.join(workdir, item)
                for item in os.listdir(workdir)
                if item.lower().endswith(".pdf")
            ]
            if not candidates:
                raise RuntimeError("office->pdf produced no pdf")
            pdf_path = candidates[0]

        final_pdf = os.path.join(
            tempfile.gettempdir(),
            f"bifrost_doc2pdf_{uuid.uuid4().hex}.pdf",
        )
        shutil.copy2(pdf_path, final_pdf)
        try:
            shutil.rmtree(workdir, ignore_errors=True)
        except Exception:
            pass

        src_filename = str(resolved.get("filename") or "document.docx")
        pdf_name = f"{os.path.splitext(src_filename)[0]}.pdf"
        return {
            **resolved,
            "filename": pdf_name,
            "content_type": "application/pdf",
            "local_path": final_pdf,
            "_temporary_local_path": final_pdf,
        }

    def _extract_text_fallback(self, resolved: dict) -> str:
        path = str(resolved.get("local_path") or "")
        if not path or not os.path.isfile(path):
            return ""
        if self._is_docx_file(resolved):
            text = self._extract_docx_text(path)
            return text[: self._max_text_fallback_chars()] if text else ""
        if self._is_doc_file(resolved):
            text = self._extract_doc_text(path)
            return text[: self._max_text_fallback_chars()] if text else ""

        max_chars = self._max_text_fallback_chars()
        max_bytes = min(self._max_inline_bytes(), 3 * 1024 * 1024)
        try:
            with open(path, "rb") as file:
                data = file.read(max_bytes)
            text = data.decode("utf-8", errors="replace")
            text = text.strip()
            if not text:
                return ""
            if len(text) > max_chars:
                return text[:max_chars]
            return text
        except Exception:
            return ""

    def _extract_doc_text(self, path: str) -> str:
        for bin_name in ("antiword", "catdoc"):
            tool = shutil.which(bin_name)
            if not tool:
                continue
            try:
                proc = subprocess.run(
                    [tool, path],
                    capture_output=True,
                    timeout=30,
                )
            except Exception:
                continue
            if proc.returncode != 0:
                continue
            text = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
            if text:
                return text
        return ""

    def _extract_docx_text(self, path: str) -> str:
        try:
            with zipfile.ZipFile(path, "r") as archive:
                xml_bytes = archive.read("word/document.xml")
        except Exception:
            return ""
        try:
            root = ET.fromstring(xml_bytes)
        except Exception:
            return ""

        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        lines: List[str] = []
        for paragraph in root.findall(".//w:p", ns):
            segs: List[str] = []
            for text_node in paragraph.findall(".//w:t", ns):
                if text_node.text:
                    segs.append(text_node.text)
            line = "".join(segs).strip()
            if line:
                lines.append(line)
        return "\n".join(lines)

    # -------------------------------------------------------------------------
    # Payload builders
    # -------------------------------------------------------------------------

    def _merge_payload_sections(
        self, stable_prefix: Dict[str, Any], volatile_tail: Dict[str, Any]
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        for key, value in (stable_prefix or {}).items():
            payload[key] = value
        for key, value in (volatile_tail or {}).items():
            payload[key] = value
        return payload

    def _responses_continuation_input_items(self, input_items: List[dict]) -> List[dict]:
        if not isinstance(input_items, list) or not input_items:
            return input_items

        for idx in range(len(input_items) - 1, -1, -1):
            item = input_items[idx]
            if (
                isinstance(item, dict)
                and str(item.get("type") or "").strip().lower() == "message"
                and str(item.get("role") or "").strip().lower() == "user"
            ):
                return copy.deepcopy(input_items[idx:])

        function_outputs = [
            copy.deepcopy(item)
            for item in input_items
            if isinstance(item, dict)
            and str(item.get("type") or "").strip().lower() == "function_call_output"
        ]
        return function_outputs or input_items

    def _build_chat_payload(
        self,
        body: dict,
        model: str,
        system_message: Any,
        messages: List[dict],
        attachments: List[dict],
        function_specs: List[dict],
        agent_settings: Optional[dict] = None,
        effective_cache_settings: Optional[dict] = None,
    ) -> dict:
        chat_messages = self._messages_to_chat_messages(messages, system_message)
        chat_messages = self._attach_attachments_to_last_user_chat(
            chat_messages, attachments
        )
        stable_payload: Dict[str, Any] = {
            "model": model,
            "messages": chat_messages,
        }
        volatile_payload: Dict[str, Any] = {
            "stream": bool(body.get("stream", False)),
        }
        cache_settings = (
            dict(effective_cache_settings)
            if isinstance(effective_cache_settings, dict)
            else self._resolve_effective_cache_settings(
                body=body,
                model=model,
                route_mode="chat",
                system_message=system_message,
                messages=messages,
                attachments=attachments,
                function_specs=function_specs,
            )
        )

        max_tokens = self._body_param(body, "max_completion_tokens")
        if not (isinstance(max_tokens, (int, float)) and int(max_tokens) > 0):
            max_tokens = self._body_param(body, "max_tokens")
        if isinstance(max_tokens, (int, float)) and int(max_tokens) > 0:
            # 统一使用 max_completion_tokens 发给 Bifrost
            stable_payload["max_completion_tokens"] = int(max_tokens)
            if self._is_anthropic_model_name(model):
                stable_payload["max_tokens"] = int(max_tokens)

        temperature = self._body_param(body, "temperature")
        if isinstance(temperature, (int, float)):
            stable_payload["temperature"] = temperature

        top_p = self._body_param(body, "top_p")
        if isinstance(top_p, (int, float)):
            stable_payload["top_p"] = top_p

        prompt_cache_key = cache_settings.get("prompt_cache_key")
        if isinstance(prompt_cache_key, str) and prompt_cache_key.strip():
            stable_payload["prompt_cache_key"] = prompt_cache_key.strip()

        prompt_cache_retention = cache_settings.get("prompt_cache_retention")
        if (
            self._is_gpt_model_name(model)
            and isinstance(prompt_cache_retention, str)
            and prompt_cache_retention.strip()
        ):
            stable_payload["prompt_cache_retention"] = prompt_cache_retention.strip()

        cached_content = cache_settings.get("cached_content")
        if isinstance(cached_content, str) and cached_content.strip():
            stable_payload["cached_content"] = cached_content.strip()

        metadata = self._body_param(body, "metadata")
        if isinstance(metadata, dict) and metadata:
            volatile_payload["metadata"] = copy.deepcopy(metadata)

        store = self._body_param(body, "store")
        if isinstance(store, bool):
            stable_payload["store"] = store

        seed = self._body_param(body, "seed")
        if isinstance(seed, (int, float)) and not isinstance(seed, bool):
            stable_payload["seed"] = int(seed)

        service_tier = self._body_param(body, "service_tier")
        if isinstance(service_tier, str) and service_tier.strip():
            stable_payload["service_tier"] = service_tier.strip()

        stream_options = self._body_param(body, "stream_options")
        if isinstance(stream_options, dict) and stream_options:
            volatile_payload["stream_options"] = copy.deepcopy(stream_options)

        response_format = self._body_param(body, "response_format")
        if response_format is not None:
            stable_payload["response_format"] = copy.deepcopy(response_format)

        verbosity = self._body_param(body, "verbosity")
        if isinstance(verbosity, str) and verbosity.strip():
            stable_payload["verbosity"] = verbosity.strip()

        reasoning = self._resolve_reasoning_config(body, model=model)
        if isinstance(reasoning, dict) and reasoning:
            stable_payload["reasoning"] = reasoning

        chat_tools = (
            []
            if self._is_image_generation_model(model)
            else self._tools_for_chat(function_specs)
        )
        if chat_tools:
            stable_payload["tools"] = chat_tools
            choice = self._tool_choice_for_chat(body.get("tool_choice"))
            if choice is not None:
                stable_payload["tool_choice"] = choice

        parallel_tool_calls = self._body_param(body, "parallel_tool_calls")
        if isinstance(parallel_tool_calls, bool):
            volatile_payload["parallel_tool_calls"] = parallel_tool_calls

        payload = self._merge_payload_sections(stable_payload, volatile_payload)

        self._apply_prompt_cache_markers(
            payload, route_mode="chat", body=body, model=model
        )
        self._apply_agent_settings_to_payload(payload, agent_settings or {})
        return payload

    def _build_responses_payload(
        self,
        body: dict,
        model: str,
        system_message: Any,
        messages: List[dict],
        attachments: List[dict],
        function_specs: List[dict],
        agent_settings: Optional[dict] = None,
        effective_cache_settings: Optional[dict] = None,
    ) -> dict:
        input_items, instructions = self._messages_to_responses_input(
            messages,
            system_message,
            omit_call_ids=self._is_gemini_model_name(model),
            degrade_tool_history_to_messages=self._is_gemini_model_name(model),
        )
        input_items = self._attach_attachments_to_last_user_responses(
            input_items, attachments
        )
        previous_response_id = self._body_param(body, "previous_response_id")
        if isinstance(previous_response_id, str) and previous_response_id.strip():
            input_items = self._responses_continuation_input_items(input_items)
        stable_payload: Dict[str, Any] = {
            "model": model,
            "input": input_items,
        }
        volatile_payload: Dict[str, Any] = {
            "stream": bool(body.get("stream", False)),
        }
        cache_settings = (
            dict(effective_cache_settings)
            if isinstance(effective_cache_settings, dict)
            else self._resolve_effective_cache_settings(
                body=body,
                model=model,
                route_mode="responses",
                system_message=system_message,
                messages=messages,
                attachments=attachments,
                function_specs=function_specs,
            )
        )

        if instructions:
            stable_payload["instructions"] = instructions

        max_tokens = self._body_param(body, "max_completion_tokens")
        if not (isinstance(max_tokens, (int, float)) and int(max_tokens) > 0):
            max_tokens = self._body_param(body, "max_tokens")
        if isinstance(max_tokens, (int, float)) and int(max_tokens) > 0:
            # Responses API expects max_output_tokens.
            stable_payload["max_output_tokens"] = int(max_tokens)

        temperature = self._body_param(body, "temperature")
        if isinstance(temperature, (int, float)):
            stable_payload["temperature"] = temperature

        top_p = self._body_param(body, "top_p")
        if isinstance(top_p, (int, float)):
            stable_payload["top_p"] = top_p

        prompt_cache_key = cache_settings.get("prompt_cache_key")
        if isinstance(prompt_cache_key, str) and prompt_cache_key.strip():
            stable_payload["prompt_cache_key"] = prompt_cache_key.strip()

        prompt_cache_retention = cache_settings.get("prompt_cache_retention")
        if (
            self._is_gpt_model_name(model)
            and isinstance(prompt_cache_retention, str)
            and prompt_cache_retention.strip()
        ):
            stable_payload["prompt_cache_retention"] = prompt_cache_retention.strip()

        cached_content = cache_settings.get("cached_content")
        if isinstance(cached_content, str) and cached_content.strip():
            stable_payload["cached_content"] = cached_content.strip()

        metadata = self._body_param(body, "metadata")
        if isinstance(metadata, dict) and metadata:
            volatile_payload["metadata"] = copy.deepcopy(metadata)

        store = self._body_param(body, "store")
        if isinstance(store, bool):
            stable_payload["store"] = store

        seed = self._body_param(body, "seed")
        if isinstance(seed, (int, float)) and not isinstance(seed, bool):
            stable_payload["seed"] = int(seed)

        service_tier = self._body_param(body, "service_tier")
        if isinstance(service_tier, str) and service_tier.strip():
            stable_payload["service_tier"] = service_tier.strip()

        if isinstance(previous_response_id, str) and previous_response_id.strip():
            volatile_payload["previous_response_id"] = previous_response_id.strip()

        reasoning = self._resolve_reasoning_config(body, model=model)
        if isinstance(reasoning, dict) and reasoning:
            stable_payload["reasoning"] = reasoning

        responses_tools = (
            []
            if self._is_image_generation_model(model)
            else self._tools_for_responses(function_specs)
        )
        if responses_tools:
            stable_payload["tools"] = responses_tools
            choice = self._tool_choice_for_responses(body.get("tool_choice"))
            if choice is not None:
                stable_payload["tool_choice"] = choice

        parallel_tool_calls = self._body_param(body, "parallel_tool_calls")
        if isinstance(parallel_tool_calls, bool):
            volatile_payload["parallel_tool_calls"] = parallel_tool_calls

        verbosity = self._body_param(body, "verbosity")
        if isinstance(verbosity, str) and verbosity.strip():
            text_cfg = stable_payload.get("text")
            if not isinstance(text_cfg, dict):
                text_cfg = {}
            text_cfg["verbosity"] = verbosity.strip()
            stable_payload["text"] = text_cfg

        payload = self._merge_payload_sections(stable_payload, volatile_payload)

        self._apply_prompt_cache_markers(
            payload, route_mode="responses", body=body, model=model
        )
        self._apply_agent_settings_to_payload(payload, agent_settings or {})
        return payload

    def _is_image_generation_model(self, model: str) -> bool:
        name = str(model or "").strip().lower()
        if "/" in name:
            name = name.split("/", 1)[1]
        return any(
            key in name
            for key in (
                "imagen",
                "image-preview",
                "flash-image",
                "gpt-image",
                "nano-banana",
                "veo",
            )
        )

    def _is_gpt_model_name(self, model: Any) -> bool:
        if not isinstance(model, str):
            return False
        name = model.strip().lower()
        if not name:
            return False
        parts = [part for part in name.split("/") if part]
        tail = parts[-1] if parts else name
        if tail.startswith("gpt"):
            return True
        return "openai/gpt" in name or "/gpt-" in name

    def _is_gemini_model_name(self, model: Any) -> bool:
        if not isinstance(model, str):
            return False
        name = model.strip().lower()
        if not name:
            return False
        if name.startswith("gemini/"):
            return True
        return "/gemini" in name

    def _is_anthropic_model_name(self, model: Any) -> bool:
        if not isinstance(model, str):
            return False
        name = model.strip().lower()
        if not name:
            return False
        return name.startswith("anthropic/") or "/claude" in name or "claude " in name

    def _tool_call_to_history_text(self, tool_call: Any) -> str:
        if not isinstance(tool_call, dict):
            return ""
        function = (
            tool_call.get("function")
            if isinstance(tool_call.get("function"), dict)
            else {}
        )
        name = str(function.get("name") or tool_call.get("name") or "").strip()
        if not name:
            return ""
        arguments = self._ensure_json_string(
            function.get("arguments", tool_call.get("arguments", {}))
        )
        return f"Called tool {name} with arguments {arguments}."

    def _extract_reasoning_tokens_from_usage(self, usage: Any) -> int:
        if not isinstance(usage, dict):
            return 0

        candidates: List[Any] = [
            usage.get("reasoning_tokens"),
            (
                usage.get("completion_tokens_details", {}).get("reasoning_tokens")
                if isinstance(usage.get("completion_tokens_details"), dict)
                else None
            ),
            (
                usage.get("output_tokens_details", {}).get("reasoning_tokens")
                if isinstance(usage.get("output_tokens_details"), dict)
                else None
            ),
        ]

        for value in candidates:
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                return max(0, int(value))
            if isinstance(value, str):
                s = value.strip()
                if not s:
                    continue
                try:
                    return max(0, int(float(s)))
                except Exception:
                    continue
        return 0

    def _gpt_reasoning_placeholder(self, reasoning_tokens: int = 0) -> str:
        if reasoning_tokens > 0:
            return (
                f"GPT思考中（已使用 {reasoning_tokens} 推理 tokens，"
                "上游未返回可见思考内容）"
            )
        return "GPT思考中（上游未返回可见思考内容）"

    def _should_emit_gpt_reasoning_placeholder(
        self,
        model: Any,
        reasoning_requested: bool = False,
        usage: Optional[dict] = None,
    ) -> bool:
        if not self._show_reasoning_enabled():
            return False
        if not self._is_gpt_model_name(model):
            return False
        reasoning_tokens = self._extract_reasoning_tokens_from_usage(usage)
        default_reasoning_enabled = bool(self.valves.DEFAULT_REASONING_ENABLED)
        return bool(
            reasoning_requested or default_reasoning_enabled or reasoning_tokens > 0
        )

    def _stream_reasoning_placeholder_chunk(
        self,
        state: Any,
        usage: Optional[dict] = None,
    ) -> Optional[dict]:
        if not isinstance(state, dict):
            return None
        if state.get("reasoning_seen") or state.get("reasoning_placeholder_emitted"):
            return None

        usage_reasoning_tokens = self._extract_reasoning_tokens_from_usage(usage)
        if usage_reasoning_tokens > 0:
            state["reasoning_tokens"] = max(
                int(state.get("reasoning_tokens") or 0),
                usage_reasoning_tokens,
            )

        if not self._should_emit_gpt_reasoning_placeholder(
            model=state.get("model_name"),
            reasoning_requested=bool(state.get("reasoning_requested")),
            usage=(
                usage
                if isinstance(usage, dict)
                else {"reasoning_tokens": state.get("reasoning_tokens", 0)}
            ),
        ):
            return None

        placeholder = self._gpt_reasoning_placeholder(
            int(state.get("reasoning_tokens") or 0)
        )
        if not placeholder:
            return None

        state["reasoning_placeholder_emitted"] = True
        return {"choices": [{"delta": {"reasoning_content": placeholder}}]}

    def _reasoning_payload_has_optional_fields(self, payload: dict) -> bool:
        reasoning = payload.get("reasoning")
        if not isinstance(reasoning, dict):
            return False
        allowed = {"enabled", "enable", "enabel", "max_tokens"}
        for key, value in reasoning.items():
            if key not in allowed and value not in (None, "", [], {}):
                return True
        return False

    def _payload_with_minimal_reasoning(self, payload: dict) -> dict:
        fallback = copy.deepcopy(payload or {})
        reasoning = fallback.get("reasoning")
        if not isinstance(reasoning, dict):
            return fallback

        minimal: Dict[str, Any] = {}
        for key in ("enabled", "enable", "enabel"):
            if isinstance(reasoning.get(key), bool):
                minimal[key] = reasoning[key]
        if not any(k in minimal for k in ("enabled", "enable", "enabel")):
            minimal["enabled"] = True

        max_tokens = reasoning.get("max_tokens")
        if isinstance(max_tokens, (int, float)) and int(max_tokens) > 0:
            minimal["max_tokens"] = int(max_tokens)

        fallback["reasoning"] = minimal
        return fallback

    def _resolve_reasoning_config(self, body: dict, model: str = "") -> Optional[dict]:
        raw_reasoning = self._coerce_json_object(self._body_param(body, "reasoning"))
        reasoning: Dict[str, Any] = {}
        reasoning_enabled_value: Optional[bool] = None
        reasoning_enabled_source_key: Optional[str] = None

        # 1. 从前端传递的 reasoning 对象中解析
        if isinstance(raw_reasoning, dict):
            enable = raw_reasoning.get("enable")
            enabled = raw_reasoning.get("enabled")
            enabel = raw_reasoning.get("enabel")
            if isinstance(enabled, bool):
                reasoning_enabled_value = enabled
                reasoning_enabled_source_key = "enabled"
            elif isinstance(enable, bool):
                reasoning_enabled_value = enable
                reasoning_enabled_source_key = "enable"
            elif isinstance(enabel, bool):
                reasoning_enabled_value = enabel
                reasoning_enabled_source_key = "enabel"

            effort = raw_reasoning.get("effort")
            summary = raw_reasoning.get("summary")
            if isinstance(effort, str) and effort.strip():
                reasoning["effort"] = effort.strip().lower()
            if isinstance(summary, str) and summary.strip():
                reasoning["summary"] = summary.strip().lower()

            # 提取推理专属的 max_tokens
            max_tokens = raw_reasoning.get(
                "max_tokens", raw_reasoning.get("max_output_tokens")
            )
            if isinstance(max_tokens, (int, float)) and int(max_tokens) > 0:
                reasoning["max_tokens"] = int(max_tokens)

        # 2. 从顶层参数解析（如果在 Open WebUI 的高级参数中设置）
        body_enabled = self._body_param(body, "reasoning_enabled")
        if isinstance(body_enabled, bool):
            reasoning_enabled_value = body_enabled
            if reasoning_enabled_source_key is None:
                reasoning_enabled_source_key = "enabled"

        body_effort = self._body_param(body, "reasoning_effort")
        if isinstance(body_effort, str) and body_effort.strip():
            reasoning["effort"] = body_effort.strip().lower()

        body_summary = self._body_param(body, "reasoning_summary")
        if isinstance(body_summary, str) and body_summary.strip():
            reasoning["summary"] = body_summary.strip().lower()

        # 处理顶层的 reasoning_max_tokens
        if "max_tokens" not in reasoning:
            body_max_tokens = self._body_param(body, "reasoning_max_tokens")
            if isinstance(body_max_tokens, (int, float)) and int(body_max_tokens) > 0:
                reasoning["max_tokens"] = int(body_max_tokens)

        # 3. 补充默认值 (Valves)
        if reasoning_enabled_value is None and self.valves.DEFAULT_REASONING_ENABLED:
            reasoning_enabled_value = True
            if reasoning_enabled_source_key is None:
                reasoning_enabled_source_key = "enabled"

        if isinstance(reasoning_enabled_value, bool):
            key_mode = str(self.valves.REASONING_ENABLE_PARAM_KEY or "").strip().lower()
            if key_mode in ("", "auto"):
                key_mode = reasoning_enabled_source_key or "enabled"
            if key_mode == "enable":
                reasoning["enable"] = reasoning_enabled_value
            elif key_mode == "enabel":
                reasoning["enabel"] = reasoning_enabled_value
            elif key_mode == "both":
                reasoning["enabled"] = reasoning_enabled_value
                reasoning["enable"] = reasoning_enabled_value
            else:
                reasoning["enabled"] = reasoning_enabled_value

        if "effort" not in reasoning:
            default_effort = (
                str(self.valves.DEFAULT_REASONING_EFFORT or "").strip().lower()
            )
            if default_effort:
                reasoning["effort"] = default_effort

        if "summary" not in reasoning:
            default_summary = (
                str(self.valves.DEFAULT_REASONING_SUMMARY or "").strip().lower()
            )
            if default_summary:
                reasoning["summary"] = default_summary

        if "max_tokens" not in reasoning:
            try:
                default_reasoning_max_tokens = int(
                    self.valves.DEFAULT_REASONING_MAX_TOKENS or 0
                )
            except Exception:
                default_reasoning_max_tokens = 0
            if default_reasoning_max_tokens > 0:
                reasoning["max_tokens"] = default_reasoning_max_tokens

        mode = str(self.valves.REASONING_PARAM_MODE or "").strip().lower()
        if "effort" not in reasoning and mode != "minimal":
            return None

        if mode == "minimal":
            minimal_reasoning: Dict[str, Any] = {}
            for key in ("enabled", "enable", "enabel"):
                if isinstance(reasoning.get(key), bool):
                    minimal_reasoning[key] = reasoning[key]
            if not any(k in minimal_reasoning for k in ("enabled", "enable", "enabel")):
                minimal_reasoning["enabled"] = True
            if (
                isinstance(reasoning.get("max_tokens"), (int, float))
                and int(reasoning.get("max_tokens")) > 0
            ):
                minimal_reasoning["max_tokens"] = int(reasoning.get("max_tokens"))
            reasoning = minimal_reasoning

        return reasoning if reasoning else None

    # -------------------------------------------------------------------------
    # Message conversion
    # -------------------------------------------------------------------------

    def _messages_to_chat_messages(
        self, messages: List[dict], system_message: Any
    ) -> List[dict]:
        out: List[dict] = []

        if system_message is not None:
            system_text = self._content_to_text(
                system_message.get("content")
                if isinstance(system_message, dict)
                else system_message
            )
            if system_text:
                out.append({"role": "system", "content": system_text})

        for msg in messages or []:
            if not isinstance(msg, dict):
                continue

            role = str(msg.get("role") or "user").strip().lower()
            if role not in ("system", "user", "assistant", "tool"):
                role = "user"

            content = msg.get("content")
            normalized_content = self._normalize_chat_content(content, role=role)

            item: Dict[str, Any] = {"role": role, "content": normalized_content}

            if role == "assistant":
                tool_calls = msg.get("tool_calls")
                if isinstance(tool_calls, list) and tool_calls:
                    item["tool_calls"] = tool_calls
            elif role == "tool":
                tool_call_id = str(msg.get("tool_call_id") or "").strip()
                if tool_call_id:
                    item["tool_call_id"] = tool_call_id

            out.append(item)

        return out

    def _messages_to_responses_input(
        self,
        messages: List[dict],
        system_message: Any,
        omit_call_ids: bool = False,
        degrade_tool_history_to_messages: bool = False,
    ) -> Tuple[List[dict], str]:
        instructions = ""
        if system_message is not None:
            instructions = self._content_to_text(
                system_message.get("content")
                if isinstance(system_message, dict)
                else system_message
            ).strip()

        input_items: List[dict] = []
        for msg_index, msg in enumerate(messages or []):
            if not isinstance(msg, dict):
                continue

            role = str(msg.get("role") or "user").strip().lower()
            content = msg.get("content")

            if role == "system":
                if not instructions:
                    instructions = self._content_to_text(content).strip()
                continue

            if role == "tool":
                output_text = self._content_to_text(content)
                if degrade_tool_history_to_messages:
                    input_items.append(
                        {
                            "type": "message",
                            "role": "tool",
                            "content": [{"type": "input_text", "text": output_text}],
                        }
                    )
                    continue
                call_id = str(msg.get("tool_call_id") or msg.get("id") or "").strip()
                if call_id:
                    item = {
                        "type": "function_call_output",
                        "output": output_text,
                    }
                    if not omit_call_ids:
                        item["call_id"] = call_id
                    input_items.append(item)
                else:
                    input_items.append(
                        {
                            "type": "message",
                            "role": "tool",
                            "content": [{"type": "input_text", "text": output_text}],
                        }
                    )
                continue

            if role == "assistant":
                tool_calls = msg.get("tool_calls")
                has_tool_calls = isinstance(tool_calls, list) and bool(tool_calls)
                if has_tool_calls:
                    if degrade_tool_history_to_messages:
                        history_lines = []
                        for tc in tool_calls:
                            text = self._tool_call_to_history_text(tc)
                            if text:
                                history_lines.append(text)
                        if history_lines:
                            input_items.append(
                                {
                                    "type": "message",
                                    "role": "assistant",
                                    "content": [
                                        {
                                            "type": "output_text",
                                            "text": "\n".join(history_lines),
                                        }
                                    ],
                                }
                            )
                        continue
                    for tool_index, tc in enumerate(tool_calls):
                        if not isinstance(tc, dict):
                            continue
                        fn = (
                            tc.get("function")
                            if isinstance(tc.get("function"), dict)
                            else {}
                        )
                        name = str(fn.get("name") or "").strip()
                        if not name:
                            continue
                        call_id = str(
                            tc.get("id") or f"call_m{msg_index}_t{tool_index}"
                        ).strip()
                        arguments = fn.get("arguments", "{}")
                        item = {
                            "type": "function_call",
                            "name": name,
                            "arguments": self._ensure_json_string(arguments),
                        }
                        if not omit_call_ids:
                            item["call_id"] = call_id
                        thought_signature = self._extract_tool_thought_signature(tc)
                        if thought_signature:
                            item = self._apply_tool_thought_signature(
                                item, thought_signature
                            )
                        input_items.append(item)
                    continue

                assistant_parts = self._content_to_responses_parts(
                    content, role="assistant"
                )
                if assistant_parts:
                    input_items.append(
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": assistant_parts,
                        }
                    )
                continue

            user_parts = self._content_to_responses_parts(content, role="user")
            if user_parts:
                input_items.append(
                    {"type": "message", "role": "user", "content": user_parts}
                )

        if not input_items:
            input_items.append(
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": ""}],
                }
            )

        return input_items, instructions

    def _normalize_chat_content(self, content: Any, role: str) -> Any:
        if isinstance(content, str):
            return (
                self._strip_openwebui_internal_details(content)
                if role == "assistant"
                else content
            )
        if isinstance(content, list):
            parts: List[dict] = []
            for part in content:
                if isinstance(part, str):
                    text = (
                        self._strip_openwebui_internal_details(part)
                        if role == "assistant"
                        else part
                    )
                    if text:
                        parts.append({"type": "text", "text": text})
                    continue
                if not isinstance(part, dict):
                    continue
                ptype = str(part.get("type") or "").strip().lower()
                if ptype in ("text", "image_url", "input_audio", "file"):
                    if (
                        role == "assistant"
                        and ptype == "text"
                        and isinstance(part.get("text"), str)
                    ):
                        cleaned = self._strip_openwebui_internal_details(
                            str(part.get("text") or "")
                        )
                        if cleaned:
                            part = {**part, "text": cleaned}
                        else:
                            continue
                    parts.append(part)
                    continue
                if ptype == "input_text":
                    text = str(part.get("text") or "")
                    if role == "assistant":
                        text = self._strip_openwebui_internal_details(text)
                    if text:
                        parts.append({"type": "text", "text": text})
                    continue
                if ptype == "output_text":
                    text = str(part.get("text") or "")
                    if role == "assistant":
                        text = self._strip_openwebui_internal_details(text)
                    if text:
                        parts.append({"type": "text", "text": text})
                    continue
                if isinstance(part.get("text"), str):
                    text = str(part.get("text") or "")
                    if role == "assistant":
                        text = self._strip_openwebui_internal_details(text)
                    if text:
                        parts.append({"type": "text", "text": text})
            if parts:
                return parts
        text = self._content_to_text(content)
        if role == "assistant":
            text = self._strip_openwebui_internal_details(text)
        return text if text else ("" if role != "assistant" else None)

    def _content_to_responses_parts(self, content: Any, role: str) -> List[dict]:
        out: List[dict] = []
        text_part_type = "output_text" if role == "assistant" else "input_text"

        if isinstance(content, str):
            if role == "assistant":
                content = self._strip_openwebui_internal_details(content)
            if content:
                out.append({"type": text_part_type, "text": content})
            return out

        if isinstance(content, list):
            for part in content:
                if isinstance(part, str):
                    if role == "assistant":
                        part = self._strip_openwebui_internal_details(part)
                    if part:
                        out.append({"type": text_part_type, "text": part})
                    continue
                if not isinstance(part, dict):
                    continue
                ptype = str(part.get("type") or "").strip().lower()
                if ptype in ("text", "input_text", "output_text"):
                    text = part.get("text")
                    if isinstance(text, str) and text:
                        if role == "assistant":
                            text = self._strip_openwebui_internal_details(text)
                        if not text:
                            continue
                        out.append({"type": text_part_type, "text": text})
                    continue
                if role != "assistant" and ptype == "image_url":
                    image_url = part.get("image_url")
                    if isinstance(image_url, dict):
                        image_url = image_url.get("url")
                    if isinstance(image_url, str) and image_url:
                        out.append({"type": "input_image", "image_url": image_url})
                    continue
                if role != "assistant" and ptype == "file":
                    file_obj = (
                        part.get("file") if isinstance(part.get("file"), dict) else {}
                    )
                    file_data = file_obj.get("file_data")
                    file_id = file_obj.get("file_id")
                    filename = file_obj.get("filename")
                    if isinstance(file_data, str) and file_data:
                        item = {"type": "input_file", "file_data": file_data}
                        if isinstance(filename, str) and filename:
                            item["filename"] = filename
                        out.append(item)
                    elif isinstance(file_id, str) and file_id:
                        item = {"type": "input_file", "file_id": file_id}
                        if isinstance(filename, str) and filename:
                            item["filename"] = filename
                        out.append(item)
                    continue
                if role != "assistant" and ptype == "input_audio":
                    audio_obj = part.get("input_audio")
                    if isinstance(audio_obj, dict):
                        out.append({"type": "input_audio", "input_audio": audio_obj})
                    continue
                if isinstance(part.get("text"), str) and part.get("text"):
                    text = part.get("text")
                    if role == "assistant":
                        text = self._strip_openwebui_internal_details(text)
                    if text:
                        out.append({"type": text_part_type, "text": text})

            if out:
                return out

        text = self._content_to_text(content)
        if role == "assistant":
            text = self._strip_openwebui_internal_details(text)
        if text:
            out.append({"type": text_part_type, "text": text})
        return out

    def _strip_openwebui_internal_details(self, text: str) -> str:
        if not isinstance(text, str) or "<details" not in text.lower():
            return text

        pattern = re.compile(
            r"<details\b(?=[^>]*\btype\s*=\s*(['\"]?)(?:tool_calls|reasoning)\1)[^>]*>.*?</details>\s*",
            flags=re.IGNORECASE | re.DOTALL,
        )
        previous = None
        cleaned = text
        while previous != cleaned:
            previous = cleaned
            cleaned = pattern.sub("", cleaned)
        return cleaned.strip()

    def _content_to_text(self, content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks: List[str] = []
            for item in content:
                if isinstance(item, str):
                    chunks.append(item)
                    continue
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if isinstance(text, str):
                    chunks.append(text)
                    continue
                nested = item.get("content")
                if isinstance(nested, str):
                    chunks.append(nested)
            return "".join(chunks)
        if isinstance(content, dict):
            for key in ("text", "content", "value"):
                val = content.get(key)
                if isinstance(val, str):
                    return val
            return json.dumps(content, ensure_ascii=False)
        return str(content)

    # -------------------------------------------------------------------------
    # Compatibility guards
    # -------------------------------------------------------------------------

    def _is_banned_reasoning_token(self, token: Any) -> bool:
        if not isinstance(token, str):
            return False
        up = token.strip().upper()
        if up == "REASONING_ENCRYPTED_CONTENT" or up.startswith("REASONING_"):
            return True
        normalized = re.sub(r"[^A-Z0-9]", "", up)
        return normalized == "REASONINGENCRYPTEDCONTENT"

    def _prune_banned_reasoning_tokens(self, value: Any) -> Tuple[Any, int]:
        removed = 0
        if isinstance(value, dict):
            out: Dict[str, Any] = {}
            for k, v in value.items():
                if self._is_banned_reasoning_token(k):
                    removed += 1
                    continue
                if k == "required" and isinstance(v, list):
                    filtered = []
                    for req in v:
                        if self._is_banned_reasoning_token(req):
                            removed += 1
                            continue
                        filtered.append(req)
                    out[k] = filtered
                    continue
                cleaned, sub_removed = self._prune_banned_reasoning_tokens(v)
                removed += sub_removed
                out[k] = cleaned
            return out, removed
        if isinstance(value, list):
            out_list: List[Any] = []
            for item in value:
                if self._is_banned_reasoning_token(item):
                    removed += 1
                    continue
                cleaned, sub_removed = self._prune_banned_reasoning_tokens(item)
                removed += sub_removed
                out_list.append(cleaned)
            return out_list, removed
        return value, 0

    def _sanitize_upstream_payload(self, payload: dict) -> Tuple[dict, int]:
        cleaned, removed = self._prune_banned_reasoning_tokens(payload)
        if isinstance(cleaned, dict):
            return cleaned, removed
        return payload, removed

    def _payload_without_reasoning(self, payload: dict) -> dict:
        fallback = copy.deepcopy(payload or {})
        for key in (
            "reasoning",
            "reasoning_effort",
            "reasoning_summary",
            "reasoning_enabled",
            "reasoning_max_tokens",
        ):
            fallback.pop(key, None)
        text_obj = fallback.get("text")
        if isinstance(text_obj, dict):
            text_obj.pop("reasoning", None)
        return fallback

    def _payload_without_tools(self, payload: dict) -> dict:
        fallback = copy.deepcopy(payload or {})
        fallback.pop("tools", None)
        fallback.pop("tool_choice", None)
        fallback.pop("parallel_tool_calls", None)
        return fallback

    def _is_reasoning_param_error(self, message: str) -> bool:
        up = str(message or "").upper()
        if "REASONING_ENCRYPTED_CONTENT" in up:
            return True
        if "LEVEL" in up and "NOT SUPPORTED" in up and "VALID LEVELS" in up:
            return True
        if "UNKNOWN PARAMETER" in up and "REASONING" in up:
            return True
        if "UNRECOGNIZED" in up and "REASONING" in up:
            return True
        if "INVALID_PARAMS" in up and "REASONING" in up:
            return True
        if "UNSUPPORTED" in up and "REASONING" in up:
            return True
        return False

    def _is_invalid_params_error(self, message: str) -> bool:
        up = str(message or "").upper()
        normalized = re.sub(r"[^A-Z0-9]", "", up)
        return (
            "INVALID_PARAMS" in up
            or ("UNKNOWN PARAMETER" in up)
            or ("FUNCTION CALLING IS NOT ENABLED" in up)
            or ("TOOL" in up and "NOT SUPPORTED" in up)
            or ("INVALIDPARAMETER" in normalized)
            or ("PARAMETERS" in up and "OPENAI-COMPATIBLE JSON SCHEMA" in up)
        )

    def _is_endpoint_missing_text(self, text: str) -> bool:
        up = str(text or "").upper()
        return (
            "404" in up
            or "NOT FOUND" in up
            or "NO ROUTE" in up
            or "UNSUPPORTED ENDPOINT" in up
            or "DOES NOT EXIST" in up
            or "METHOD NOT ALLOWED" in up
            # Some providers (notably Gemini via Bifrost) reject /responses payload
            # shape even when endpoint exists; treat as endpoint-incompatible to
            # trigger auto fallback to /chat/completions.
            or "FAILED TO CONVERT BIFROST REQUEST TO THE EXPECTED PROVIDER REQUEST BODY"
            in up
            or "RESPONSES INPUT IS NOT PROVIDED" in up
            or "COULD NOT BE CONVERTED TO GEMINI FORMAT" in up
        )

    def _is_endpoint_missing_result(self, result: Union[str, dict]) -> bool:
        if isinstance(result, str):
            return self._is_endpoint_missing_text(result)
        if isinstance(result, dict) and isinstance(result.get("error"), dict):
            return self._is_endpoint_missing_text(
                self._as_text(result["error"].get("message"))
            )
        return False

    def _stream_from_nonstream_result(
        self, result: Union[str, dict], model: str
    ) -> Generator[dict, None, None]:
        if isinstance(result, str):
            yield {"choices": [{"delta": {"content": result}}]}
            yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}
            return

        if not isinstance(result, dict):
            text = self._as_text(result)
            if text:
                yield {"choices": [{"delta": {"content": text}}]}
            yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}
            return

        if isinstance(result.get("error"), dict):
            yield result
            return

        choices = result.get("choices")
        if isinstance(choices, list) and choices:
            choice0 = choices[0] if isinstance(choices[0], dict) else {}
            message = (
                choice0.get("message")
                if isinstance(choice0.get("message"), dict)
                else {}
            )
            content = message.get("content")
            if isinstance(content, str) and content:
                yield {"choices": [{"delta": {"content": content}}]}
            reasoning = message.get("reasoning_content")
            if (
                self._show_reasoning_enabled()
                and isinstance(reasoning, str)
                and reasoning
            ):
                yield {"choices": [{"delta": {"reasoning_content": reasoning}}]}
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list) and tool_calls:
                yield {"choices": [{"delta": {"tool_calls": tool_calls}}]}
                yield {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}
            else:
                finish = self._map_finish_reason(choice0.get("finish_reason")) or "stop"
                yield {"choices": [{"delta": {}, "finish_reason": finish}]}
            if isinstance(result.get("usage"), dict):
                yield {"usage": result.get("usage")}
            return

        text = self._as_text(result)
        if text:
            yield {"choices": [{"delta": {"content": text}}]}
        yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}

    # -------------------------------------------------------------------------
    # Transport helpers
    # -------------------------------------------------------------------------

    def _post(
        self, endpoint: str, payload: dict, stream: bool = False
    ) -> Union[requests.Response, str]:
        url = f"{self._api_base_url()}{endpoint}"
        transport_headers: Dict[str, str] = {}
        debug_requested = False
        if isinstance(payload, dict):
            debug_requested = bool(
                self._extract_cache_settings(
                    payload, model=payload.get("model", "")
                ).get("cache_debug")
            ) or bool(payload.get("_owui_cache_debug"))
        payload_for_post = payload
        if isinstance(payload, dict):
            payload_for_post = dict(payload)
            payload_for_post.pop("_owui_cache_debug", None)
            payload_for_post.pop("cache_debug", None)
            payload_for_post.pop("semantic_cache_key", None)
            payload_for_post.pop("enable_semantic_cache", None)
        if debug_requested and isinstance(payload_for_post, dict):
            transport_headers["x-bf-send-back-raw-response"] = "true"
        if isinstance(payload_for_post, dict):
            payload, removed = self._sanitize_upstream_payload(payload_for_post)
            if removed and self.valves.DEBUG_MODE:
                self._debug(
                    f"Removed {removed} banned reasoning token(s) before POST {endpoint}"
                )
        else:
            payload = payload_for_post
        try:
            response = requests.post(
                url,
                headers=self._headers(
                    json_content=True, extra_headers=transport_headers
                ),
                data=self._utf8_json_bytes(payload),
                stream=stream,
                timeout=(10, self.valves.REQUEST_TIMEOUT_SECS),
            )
            return response
        except Exception as e:
            return f"Error ({type(e).__name__}): {e}"

    def _chat_non_stream_with_fallback(
        self, payload: dict, tool_name_prefix: str = ""
    ) -> Union[str, dict]:
        response = self._post("/chat/completions", payload, stream=False)
        if isinstance(response, str):
            return response

        if response.status_code >= 400:
            text = response.text[:2000]
            if (
                self._retry_without_reasoning_enabled()
                and payload.get("reasoning")
                and self._is_reasoning_param_error(text)
            ):
                if self._reasoning_payload_has_optional_fields(payload):
                    self._debug(
                        "chat rejected rich reasoning; retry with minimal reasoning"
                    )
                    retry_payload = self._payload_with_minimal_reasoning(payload)
                    retry_payload, _ = self._sanitize_upstream_payload(retry_payload)
                    retry = self._post("/chat/completions", retry_payload, stream=False)
                    if isinstance(retry, str):
                        return retry
                    if retry.status_code < 400:
                        return self._normalize_chat_non_stream_response(
                            retry,
                            retry_payload,
                            tool_name_prefix=tool_name_prefix,
                        )
                    retry_text = retry.text[:2000]
                    if not self._is_reasoning_param_error(retry_text):
                        return f"Error HTTP {retry.status_code}: {retry_text[:1200]}"

                self._debug("chat rejected reasoning; retry without reasoning")
                retry_payload = self._payload_without_reasoning(payload)
                retry_payload, _ = self._sanitize_upstream_payload(retry_payload)
                retry = self._post("/chat/completions", retry_payload, stream=False)
                if isinstance(retry, str):
                    return retry
                if retry.status_code < 400:
                    return self._normalize_chat_non_stream_response(
                        retry,
                        retry_payload,
                        tool_name_prefix=tool_name_prefix,
                    )
                return f"Error HTTP {retry.status_code}: {retry.text[:1200]}"

            if (
                self._retry_without_tools_enabled()
                and payload.get("tools")
                and self._is_invalid_params_error(text)
            ):
                self._debug("chat rejected tool params; retry without tools")
                retry_payload = self._payload_without_tools(payload)
                retry_payload, _ = self._sanitize_upstream_payload(retry_payload)
                retry = self._post("/chat/completions", retry_payload, stream=False)
                if isinstance(retry, str):
                    return retry
                if retry.status_code < 400:
                    return self._normalize_chat_non_stream_response(
                        retry,
                        retry_payload,
                        tool_name_prefix=tool_name_prefix,
                    )
                return f"Error HTTP {retry.status_code}: {retry.text[:1200]}"

            return f"Error HTTP {response.status_code}: {text[:1200]}"

        return self._normalize_chat_non_stream_response(
            response,
            payload,
            tool_name_prefix=tool_name_prefix,
        )

    def _responses_non_stream_with_fallback(
        self, payload: dict, tool_name_prefix: str = ""
    ) -> Union[str, dict]:
        response = self._post("/responses", payload, stream=False)
        if isinstance(response, str):
            return response

        if response.status_code >= 400:
            text = response.text[:2000]
            if (
                self._retry_without_reasoning_enabled()
                and payload.get("reasoning")
                and self._is_reasoning_param_error(text)
            ):
                if self._reasoning_payload_has_optional_fields(payload):
                    self._debug(
                        "responses rejected rich reasoning; retry with minimal reasoning"
                    )
                    retry_payload = self._payload_with_minimal_reasoning(payload)
                    retry_payload, _ = self._sanitize_upstream_payload(retry_payload)
                    retry = self._post("/responses", retry_payload, stream=False)
                    if isinstance(retry, str):
                        return retry
                    if retry.status_code < 400:
                        return self._normalize_responses_non_stream_response(
                            retry,
                            retry_payload,
                            tool_name_prefix=tool_name_prefix,
                        )
                    retry_text = retry.text[:2000]
                    if not self._is_reasoning_param_error(retry_text):
                        return f"Error HTTP {retry.status_code}: {retry_text[:1200]}"

                self._debug("responses rejected reasoning; retry without reasoning")
                retry_payload = self._payload_without_reasoning(payload)
                retry_payload, _ = self._sanitize_upstream_payload(retry_payload)
                retry = self._post("/responses", retry_payload, stream=False)
                if isinstance(retry, str):
                    return retry
                if retry.status_code < 400:
                    return self._normalize_responses_non_stream_response(
                        retry,
                        retry_payload,
                        tool_name_prefix=tool_name_prefix,
                    )
                return f"Error HTTP {retry.status_code}: {retry.text[:1200]}"

            if (
                self._retry_without_tools_enabled()
                and payload.get("tools")
                and self._is_invalid_params_error(text)
            ):
                self._debug("responses rejected tool params; retry without tools")
                retry_payload = self._payload_without_tools(payload)
                retry_payload, _ = self._sanitize_upstream_payload(retry_payload)
                retry = self._post("/responses", retry_payload, stream=False)
                if isinstance(retry, str):
                    return retry
                if retry.status_code < 400:
                    return self._normalize_responses_non_stream_response(
                        retry,
                        retry_payload,
                        tool_name_prefix=tool_name_prefix,
                    )
                return f"Error HTTP {retry.status_code}: {retry.text[:1200]}"

            return f"Error HTTP {response.status_code}: {text[:1200]}"

        return self._normalize_responses_non_stream_response(
            response,
            payload,
            tool_name_prefix=tool_name_prefix,
        )

    def _auto_stream_with_fallback(
        self,
        responses_payload: dict,
        chat_payload_builder,
        tool_name_prefix: str = "",
        __event_emitter__=None,
    ) -> Generator[dict, None, None]:
        yield from self._stream_with_fallback(
            endpoint="/responses",
            payload=responses_payload,
            mode="responses",
            allow_endpoint_fallback=True,
            endpoint_fallback_builder=chat_payload_builder,
            tool_name_prefix=tool_name_prefix,
            __event_emitter__=__event_emitter__,
        )

    def _chat_stream_with_fallback(
        self, payload: dict, tool_name_prefix: str = "", __event_emitter__=None
    ) -> Generator[dict, None, None]:
        yield from self._stream_with_fallback(
            endpoint="/chat/completions",
            payload=payload,
            mode="chat",
            allow_endpoint_fallback=False,
            tool_name_prefix=tool_name_prefix,
            __event_emitter__=__event_emitter__,
        )

    def _responses_stream_with_fallback(
        self, payload: dict, tool_name_prefix: str = "", __event_emitter__=None
    ) -> Generator[dict, None, None]:
        yield from self._stream_with_fallback(
            endpoint="/responses",
            payload=payload,
            mode="responses",
            allow_endpoint_fallback=False,
            tool_name_prefix=tool_name_prefix,
            __event_emitter__=__event_emitter__,
        )

    def _stream_with_fallback(
        self,
        endpoint: str,
        payload: dict,
        mode: str,
        allow_endpoint_fallback: bool,
        endpoint_fallback_builder=None,
        tool_name_prefix: str = "",
        __event_emitter__=None,
    ) -> Generator[dict, None, None]:
        current_endpoint = endpoint
        current_payload = payload
        current_mode = mode
        retried_minimal_reasoning = False
        retried_without_reasoning = False
        retried_without_tools = False
        switched_endpoint = False

        while True:
            response = self._post(current_endpoint, current_payload, stream=True)
            if isinstance(response, str):
                yield {"error": {"message": response}}
                return

            with response as r:
                if r.status_code >= 400:
                    text = r.text[:2000]

                    if (
                        self._retry_without_reasoning_enabled()
                        and current_payload.get("reasoning")
                        and self._is_reasoning_param_error(text)
                    ):
                        if (
                            not retried_minimal_reasoning
                        ) and self._reasoning_payload_has_optional_fields(
                            current_payload
                        ):
                            self._debug(
                                f"{current_mode} stream rejected rich reasoning; retry with minimal reasoning"
                            )
                            current_payload = self._payload_with_minimal_reasoning(
                                current_payload
                            )
                            current_payload, _ = self._sanitize_upstream_payload(
                                current_payload
                            )
                            retried_minimal_reasoning = True
                            continue

                        if not retried_without_reasoning:
                            self._debug(
                                f"{current_mode} stream rejected reasoning; retry without reasoning"
                            )
                            current_payload = self._payload_without_reasoning(
                                current_payload
                            )
                            current_payload, _ = self._sanitize_upstream_payload(
                                current_payload
                            )
                            retried_without_reasoning = True
                            continue

                    if (
                        self._retry_without_tools_enabled()
                        and (not retried_without_tools)
                        and current_payload.get("tools")
                        and self._is_invalid_params_error(text)
                    ):
                        self._debug(
                            f"{current_mode} stream rejected tool params; retry without tools"
                        )
                        current_payload = self._payload_without_tools(current_payload)
                        current_payload, _ = self._sanitize_upstream_payload(
                            current_payload
                        )
                        retried_without_tools = True
                        continue

                    if (
                        allow_endpoint_fallback
                        and (not switched_endpoint)
                        and current_endpoint == "/responses"
                        and self._is_endpoint_missing_text(text)
                        and callable(endpoint_fallback_builder)
                    ):
                        self._debug("responses endpoint missing; auto fallback to chat")
                        current_endpoint = "/chat/completions"
                        current_payload = endpoint_fallback_builder()
                        current_mode = "chat"
                        switched_endpoint = True
                        retried_minimal_reasoning = False
                        retried_without_reasoning = False
                        retried_without_tools = False
                        continue

                    yield {
                        "error": {
                            "message": f"Error HTTP {r.status_code}: {text[:1200]}"
                        }
                    }
                    return

                finish_sent = False
                state = self._new_stream_state(__event_emitter__=__event_emitter__)
                state["tool_name_alias_map"] = self._build_tool_name_alias_map(
                    current_payload
                )
                state["tool_name_prefix"] = str(tool_name_prefix or "").strip()
                state["model_name"] = current_payload.get("model")
                state["reasoning_requested"] = isinstance(
                    current_payload.get("reasoning"), dict
                )
                retry_stream_without_tools = False

                for raw_line in r.iter_lines():
                    payload_text = self._extract_sse_data(raw_line)
                    if not payload_text:
                        continue
                    if payload_text == "[DONE]":
                        break

                    try:
                        event = json.loads(payload_text)
                    except Exception:
                        continue
                    if not isinstance(event, dict):
                        continue

                    if current_mode == "responses":
                        chunk = self._parse_responses_event(event, state)
                    else:
                        chunk = self._parse_chat_event(event, state)

                    if chunk is None:
                        continue
                    if (
                        current_mode == "responses"
                        and isinstance(chunk, dict)
                        and isinstance(chunk.get("error"), dict)
                    ):
                        stream_error_message = self._as_text(
                            chunk.get("error", {}).get("message")
                        )
                        if (
                            self._retry_without_tools_enabled()
                            and (not retried_without_tools)
                            and current_payload.get("tools")
                            and (not state.get("text_seen"))
                            and (not state.get("tool_call_seen"))
                            and self._is_invalid_params_error(stream_error_message)
                        ):
                            self._debug(
                                f"{current_mode} stream emitted invalid tool params; retry without tools"
                            )
                            current_payload = self._payload_without_tools(
                                current_payload
                            )
                            current_payload, _ = self._sanitize_upstream_payload(
                                current_payload
                            )
                            retried_without_tools = True
                            retry_stream_without_tools = True
                            break
                    if isinstance(chunk, list):
                        for item in chunk:
                            if item is None:
                                continue
                            self._update_stream_usage_state(item, state)
                            if self._chunk_has_finish_reason(item):
                                finish_sent = True
                            yield item
                    else:
                        self._update_stream_usage_state(chunk, state)
                        if self._chunk_has_finish_reason(chunk):
                            finish_sent = True
                        yield chunk

                if retry_stream_without_tools:
                    continue

                if not finish_sent:
                    if (
                        self._show_reasoning_enabled()
                        and (not state.get("reasoning_seen"))
                        and (not state.get("reasoning_placeholder_emitted"))
                        and self._should_emit_gpt_reasoning_placeholder(
                            model=state.get("model_name"),
                            reasoning_requested=bool(state.get("reasoning_requested")),
                            usage={
                                "reasoning_tokens": state.get("reasoning_tokens", 0)
                            },
                        )
                    ):
                        placeholder = self._gpt_reasoning_placeholder(
                            int(state.get("reasoning_tokens") or 0)
                        )
                        if placeholder:
                            yield {
                                "choices": [
                                    {"delta": {"reasoning_content": placeholder}}
                                ]
                            }
                    finish = "tool_calls" if state.get("tool_call_seen") else "stop"
                    yield {"choices": [{"delta": {}, "finish_reason": finish}]}
                return

    # -------------------------------------------------------------------------
    # SSE parsing
    # -------------------------------------------------------------------------

    def _extract_sse_data(self, raw_line: Any) -> Optional[str]:
        if raw_line is None:
            return None
        if isinstance(raw_line, (bytes, bytearray)):
            line = raw_line.decode("utf-8", errors="replace")
        else:
            line = str(raw_line)
        line = line.strip()
        if not line or line.startswith(":"):
            return None
        if line.startswith("data:"):
            return line[5:].strip()
        if line.startswith("{") and line.endswith("}"):
            return line
        return None

    def _new_stream_state(self, __event_emitter__=None) -> dict:
        return {
            "content_so_far": "",
            "content_last_raw": "",
            "content_mode": None,
            "reasoning_so_far": "",
            "reasoning_last_raw": "",
            "reasoning_mode": None,
            "tool_states": {},
            "tool_state_aliases": {},
            "tool_name_alias_map": {},
            "next_tool_index": 0,
            "tool_call_seen": False,
            "web_search_calls": {},
            "text_seen": False,
            "reasoning_seen": False,
            "reasoning_placeholder_emitted": False,
            "reasoning_tokens": 0,
            "model_name": "",
            "reasoning_requested": False,
            "__event_emitter__": __event_emitter__,
        }

    def _build_tool_name_alias_map(self, payload: dict) -> Dict[str, str]:
        if not isinstance(payload, dict):
            return {}

        tools = payload.get("tools")
        if not isinstance(tools, list):
            return {}

        names: List[str] = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            name = tool.get("name")
            if not isinstance(name, str):
                fn = (
                    tool.get("function")
                    if isinstance(tool.get("function"), dict)
                    else {}
                )
                name = fn.get("name")
            if isinstance(name, str):
                normalized = name.strip()
                if normalized:
                    names.append(normalized)

        unique_names = list(dict.fromkeys(names))
        if not unique_names:
            return {}

        candidates: Dict[str, List[str]] = {}
        for full_name in unique_names:
            for key in (full_name, full_name.lower()):
                arr = candidates.setdefault(key, [])
                if full_name not in arr:
                    arr.append(full_name)
            if "_" in full_name:
                short_name = full_name.split("_", 1)[1].strip()
                if short_name:
                    for key in (short_name, short_name.lower()):
                        arr = candidates.setdefault(key, [])
                        if full_name not in arr:
                            arr.append(full_name)

        alias_map: Dict[str, str] = {}
        for key, vals in candidates.items():
            if len(vals) == 1:
                alias_map[key] = vals[0]
        return alias_map

    def _resolve_tool_name_alias(self, name: Any, alias_map: Any) -> str:
        if not isinstance(name, str):
            return ""
        normalized = name.strip()
        if not normalized:
            return ""
        if not isinstance(alias_map, dict):
            return normalized
        mapped = alias_map.get(normalized)
        if not isinstance(mapped, str) or not mapped:
            mapped = alias_map.get(normalized.lower())
        return str(mapped or normalized)

    def _apply_tool_name_prefix(self, name: Any, tool_name_prefix: Any) -> str:
        if not isinstance(name, str):
            return ""
        normalized = name.strip()
        if not normalized:
            return ""
        # Only prefix MCP meta tools and only when caller gave an explicit MCP prefix.
        prefix = str(tool_name_prefix or "").strip()
        if (
            prefix
            and "_" not in normalized
            and self._is_bifrost_meta_tool_name(normalized)
        ):
            return f"{prefix}{normalized}"
        return normalized

    def _resolve_effective_tool_name(
        self, name: Any, alias_map: Any = None, tool_name_prefix: Any = ""
    ) -> str:
        resolved = self._resolve_tool_name_alias(name, alias_map)
        resolved = self._apply_tool_name_prefix(resolved, tool_name_prefix)
        return resolved

    def _remap_tool_calls(
        self, tool_calls: Any, alias_map: Any, tool_name_prefix: str = ""
    ) -> Any:
        if not isinstance(tool_calls, list):
            return tool_calls
        out: List[dict] = []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            item = copy.deepcopy(tc)
            fn = item.get("function") if isinstance(item.get("function"), dict) else {}
            original_name = (
                fn.get("name") if isinstance(fn.get("name"), str) else item.get("name")
            )
            remapped_name = self._resolve_effective_tool_name(
                original_name,
                alias_map=alias_map,
                tool_name_prefix=tool_name_prefix,
            )
            if remapped_name:
                if isinstance(item.get("function"), dict):
                    item["function"]["name"] = remapped_name
                else:
                    item["name"] = remapped_name
            out.append(item)
        return out

    def _chunk_has_finish_reason(self, chunk: dict) -> bool:
        if not isinstance(chunk, dict):
            return False
        choices = chunk.get("choices")
        if not isinstance(choices, list):
            return False
        for choice in choices:
            if isinstance(choice, dict) and choice.get("finish_reason") is not None:
                return True
        return False

    def _update_stream_usage_state(self, chunk: dict, state: dict) -> None:
        if not isinstance(chunk, dict) or not isinstance(state, dict):
            return
        usage = chunk.get("usage")
        if not isinstance(usage, dict):
            return
        reasoning_tokens = self._extract_reasoning_tokens_from_usage(usage)
        if reasoning_tokens > 0:
            state["reasoning_tokens"] = reasoning_tokens

    def _normalize_stream_delta(self, raw_text: Any, state: dict, prefix: str) -> str:
        if not isinstance(raw_text, str) or not raw_text:
            return ""

        text_key = f"{prefix}_so_far"
        last_key = f"{prefix}_last_raw"
        mode_key = f"{prefix}_mode"

        prev = str(state.get(text_key) or "")
        last_raw = str(state.get(last_key) or "")
        mode = state.get(mode_key)
        text = raw_text

        # If identical chunk repeats, drop it.
        if text == last_raw:
            return ""

        # Infer mode on first meaningful chunk.
        if mode is None:
            if prev and text.startswith(prev) and len(text) >= len(prev):
                mode = "cumulative"
            else:
                mode = "delta"
            state[mode_key] = mode

        if mode == "delta":
            # Some gateways send delta first, then a full cumulative snapshot in a
            # later chunk (often *.done). Switch mode on-the-fly to avoid duplication.
            if prev and text == prev:
                state[last_key] = text
                return ""
            if prev and text.startswith(prev) and len(text) >= len(prev):
                state[mode_key] = "cumulative"
                delta = text[len(prev) :]
                state[text_key] = text
                state[last_key] = text
                return delta
            state[text_key] = prev + text
            state[last_key] = text
            return text

        # cumulative mode
        if text.startswith(prev) and len(text) >= len(prev):
            delta = text[len(prev) :]
            state[text_key] = text
            state[last_key] = text
            return delta

        if prev.startswith(text) and len(text) < len(prev):
            state[last_key] = text
            return ""

        # ambiguous: degrade to delta mode
        state[mode_key] = "delta"
        state[text_key] = prev + text
        state[last_key] = text
        return text

    def _coerce_reasoning_text(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            chunks: List[str] = []
            for item in value:
                t = self._coerce_reasoning_text(item)
                if t:
                    chunks.append(t)
            return "".join(chunks)
        if isinstance(value, dict):
            for key in (
                "reasoning_content",
                "reasoning",
                "thinking",
                "thought",
                "text",
                "content",
                "value",
            ):
                if key in value:
                    t = self._coerce_reasoning_text(value.get(key))
                    if t:
                        return t
        return ""

    def _part_to_image_markdown(self, part: dict) -> str:
        if not isinstance(part, dict):
            return ""

        # Gemini style: inlineData { mimeType, data }
        inline = part.get("inlineData") or part.get("inline_data") or {}
        if isinstance(inline, dict):
            mime = inline.get("mimeType") or inline.get("mime_type") or ""
            data = inline.get("data") or ""
            if (
                isinstance(mime, str)
                and mime.startswith("image/")
                and isinstance(data, str)
                and data
            ):
                return f"![generated image](data:{mime};base64,{data})"

        # Gemini style: fileData { mimeType, fileUri }
        file_data = part.get("fileData") or part.get("file_data") or {}
        if isinstance(file_data, dict):
            mime = file_data.get("mimeType") or file_data.get("mime_type") or ""
            uri = (
                file_data.get("fileUri")
                or file_data.get("file_uri")
                or file_data.get("uri")
                or ""
            )
            if isinstance(uri, str) and uri:
                if isinstance(mime, str) and mime.startswith("image/"):
                    return f"![generated image]({uri})"
                return f"[generated file]({uri})"

        # OpenAI-like image payloads
        for key in ("b64_json", "image_base64", "base64", "data"):
            value = part.get(key)
            if isinstance(value, str) and value:
                mime = str(part.get("mime_type") or part.get("mimeType") or "image/png")
                if not mime.startswith("image/"):
                    mime = "image/png"
                return f"![generated image](data:{mime};base64,{value})"

        image_url = part.get("image_url")
        if isinstance(image_url, dict):
            image_url = image_url.get("url")
        if isinstance(image_url, str) and image_url:
            return f"![generated image]({image_url})"

        return ""

    def _extract_text_from_content_field(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if not isinstance(value, list):
            return ""
        chunks: List[str] = []
        for part in value:
            if isinstance(part, str):
                chunks.append(part)
                continue
            if not isinstance(part, dict):
                continue
            image_md = self._part_to_image_markdown(part)
            if image_md:
                chunks.append(image_md)
                continue
            text = part.get("text")
            if isinstance(text, str):
                chunks.append(text)
        return "".join(chunks)

    def _map_finish_reason(self, reason: Any) -> Optional[str]:
        if not isinstance(reason, str):
            return None
        value = reason.strip().lower()
        mapping = {
            "stop": "stop",
            "end_turn": "stop",
            "eos": "stop",
            "length": "length",
            "max_tokens": "length",
            "max_output_tokens": "length",
            "tool_calls": "tool_calls",
            "function_call": "tool_calls",
            "tool_use": "tool_calls",
            "content_filter": "content_filter",
            "safety": "content_filter",
        }
        return mapping.get(value, None)

    def _ensure_json_string(self, value: Any) -> str:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return "{}"
            try:
                parsed = json.loads(stripped)
                return json.dumps(parsed, ensure_ascii=False)
            except Exception:
                return stripped
        try:
            return json.dumps(value if value is not None else {}, ensure_ascii=False)
        except Exception:
            return "{}"

    def _coerce_argument_fragment(self, value: Any) -> str:
        if value is None:
            return ""

        if isinstance(value, str):
            return value

        if isinstance(value, bytes):
            try:
                return value.decode("utf-8")
            except Exception:
                return value.decode("utf-8", errors="ignore")

        if isinstance(value, dict):
            if len(value) == 1:
                key = next(iter(value))
                if key in {
                    "value",
                    "text",
                    "partial_json",
                    "arguments_delta",
                    "arguments",
                    "delta",
                }:
                    return self._coerce_argument_fragment(value.get(key))
                if key == "input_json_delta":
                    nested = value.get(key)
                    if isinstance(nested, dict) and "partial_json" in nested:
                        return self._coerce_argument_fragment(nested.get("partial_json"))
                if key == "function":
                    nested = value.get(key)
                    if isinstance(nested, dict) and "arguments" in nested:
                        return self._coerce_argument_fragment(nested.get("arguments"))

            return self._ensure_json_string(value)

        if isinstance(value, (list, int, float, bool)):
            return self._ensure_json_string(value)

        return str(value)

    def _extract_tool_thought_signature(self, item: Any) -> str:
        if not isinstance(item, dict):
            return ""

        function = (
            item.get("function") if isinstance(item.get("function"), dict) else {}
        )
        extra_content = (
            item.get("extra_content")
            if isinstance(item.get("extra_content"), dict)
            else {}
        )
        extra_google = (
            extra_content.get("google")
            if isinstance(extra_content.get("google"), dict)
            else {}
        )
        function_extra_content = (
            function.get("extra_content")
            if isinstance(function.get("extra_content"), dict)
            else {}
        )
        function_extra_google = (
            function_extra_content.get("google")
            if isinstance(function_extra_content.get("google"), dict)
            else {}
        )

        candidates = (
            item.get("thoughtSignature"),
            item.get("thought_signature"),
            function.get("thoughtSignature"),
            function.get("thought_signature"),
            extra_google.get("thoughtSignature"),
            extra_google.get("thought_signature"),
            function_extra_google.get("thoughtSignature"),
            function_extra_google.get("thought_signature"),
        )
        for candidate in candidates:
            if isinstance(candidate, str):
                normalized = candidate.strip()
                if normalized:
                    return normalized
        return ""

    def _apply_tool_thought_signature(self, item: Any, thought_signature: Any) -> Any:
        if not isinstance(item, dict):
            return item
        if not isinstance(thought_signature, str):
            return item

        signature = thought_signature.strip()
        if not signature:
            return item

        item["thoughtSignature"] = signature
        item["thought_signature"] = signature

        extra_content = item.get("extra_content")
        if not isinstance(extra_content, dict):
            extra_content = {}
            item["extra_content"] = extra_content
        google_extra = extra_content.get("google")
        if not isinstance(google_extra, dict):
            google_extra = {}
            extra_content["google"] = google_extra
        google_extra["thoughtSignature"] = signature
        google_extra["thought_signature"] = signature

        function = item.get("function")
        if isinstance(function, dict):
            function["thoughtSignature"] = signature
            function["thought_signature"] = signature

            function_extra_content = function.get("extra_content")
            if not isinstance(function_extra_content, dict):
                function_extra_content = {}
                function["extra_content"] = function_extra_content
            function_google_extra = function_extra_content.get("google")
            if not isinstance(function_google_extra, dict):
                function_google_extra = {}
                function_extra_content["google"] = function_google_extra
            function_google_extra["thoughtSignature"] = signature
            function_google_extra["thought_signature"] = signature

        return item

    def _coerce_tool_index(self, value: Any) -> Optional[int]:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            idx = int(value)
            return idx if idx >= 0 else None
        if isinstance(value, str):
            s = value.strip()
            if s.isdigit():
                return int(s)
        return None

    def _normalize_tool_field_delta(
        self, raw_text: Any, tool_state: dict, field: str
    ) -> str:
        if raw_text is None:
            return ""

        text = raw_text
        if not isinstance(text, str):
            if field == "args":
                text = self._ensure_json_string(text)
            else:
                text = str(text)
        if not text:
            return ""

        text_key = f"{field}_so_far"
        last_key = f"{field}_last_raw"
        mode_key = f"{field}_mode"

        prev = str(tool_state.get(text_key) or "")
        last_raw = str(tool_state.get(last_key) or "")
        mode = tool_state.get(mode_key)

        if text == last_raw:
            return ""

        if mode is None:
            if prev and text.startswith(prev) and len(text) >= len(prev):
                mode = "cumulative"
            else:
                mode = "delta"
            tool_state[mode_key] = mode

        if mode == "delta":
            if prev and text == prev:
                tool_state[last_key] = text
                return ""
            if prev and text.startswith(prev) and len(text) >= len(prev):
                tool_state[mode_key] = "cumulative"
                delta = text[len(prev) :]
                tool_state[text_key] = text
                tool_state[last_key] = text
                return delta
            tool_state[text_key] = prev + text
            tool_state[last_key] = text
            return text

        if text.startswith(prev) and len(text) >= len(prev):
            delta = text[len(prev) :]
            tool_state[text_key] = text
            tool_state[last_key] = text
            return delta

        if prev.startswith(text) and len(text) < len(prev):
            tool_state[last_key] = text
            return ""

        tool_state[mode_key] = "delta"
        tool_state[text_key] = prev + text
        tool_state[last_key] = text
        return text

    def _resolve_tool_state(
        self,
        state: dict,
        *,
        index_hint: Optional[int] = None,
        call_id: Optional[str] = None,
        aliases: Optional[List[str]] = None,
        name_hint: Optional[str] = None,
    ) -> dict:
        tool_states = state.setdefault("tool_states", {})
        alias_map = state.setdefault("tool_state_aliases", {})

        keys: List[str] = []
        if call_id:
            keys.append(call_id)
        if aliases:
            keys.extend([a for a in aliases if isinstance(a, str) and a])
        if index_hint is not None:
            keys.append(f"idx:{index_hint}")

        ts: Optional[dict] = None
        canonical_key: Optional[str] = None
        for key in keys:
            mapped = alias_map.get(key, key)
            candidate = tool_states.get(mapped)
            if isinstance(candidate, dict):
                ts = candidate
                canonical_key = mapped
                break

        if ts is None:
            if index_hint is None:
                idx = int(state.get("next_tool_index") or 0)
            else:
                idx = index_hint
            state["next_tool_index"] = max(
                int(state.get("next_tool_index") or 0), idx + 1
            )
            call_id_norm = call_id or f"call_{idx}"
            ts = {
                "index": idx,
                "id": call_id_norm,
                "name": "",
                "name_so_far": "",
                "name_last_raw": "",
                "name_mode": None,
                "args_so_far": "",
                "args_last_raw": "",
                "args_mode": None,
                "thought_signature": "",
                "thought_signature_emitted": False,
                "header_sent": False,
            }
            canonical_key = call_id_norm
            tool_states[canonical_key] = ts

        if call_id:
            alias_map[call_id] = canonical_key
            tool_states[call_id] = ts
            if not ts.get("id"):
                ts["id"] = call_id

        if index_hint is not None:
            idx_key = f"idx:{index_hint}"
            alias_map[idx_key] = canonical_key
            tool_states[idx_key] = ts

        if aliases:
            for alias in aliases:
                if not isinstance(alias, str) or not alias:
                    continue
                alias_map[alias] = canonical_key
                tool_states[alias] = ts

        if isinstance(name_hint, str) and name_hint.strip():
            if not ts.get("name"):
                ts["name"] = name_hint.strip()

        return ts

    def _normalize_tool_calls_delta(self, tool_calls: Any, state: dict) -> List[dict]:
        if not isinstance(tool_calls, list):
            return []

        out: List[dict] = []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue

            idx_hint = self._coerce_tool_index(tc.get("index"))
            call_id = str(tc.get("id") or "").strip() or None
            item_id = str(tc.get("item_id") or "").strip()
            aliases = [item_id] if item_id else []

            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            name_raw = fn.get("name")
            name_text = (
                self._resolve_effective_tool_name(
                    name_raw,
                    alias_map=state.get("tool_name_alias_map", {}),
                    tool_name_prefix=state.get("tool_name_prefix", ""),
                )
                if isinstance(name_raw, str)
                else ""
            )
            args_raw = fn.get("arguments")
            if args_raw is None:
                args_text: Any = ""
            elif isinstance(args_raw, str):
                args_text = args_raw
            else:
                args_text = self._ensure_json_string(args_raw)
            thought_signature = self._extract_tool_thought_signature(tc)

            ts = self._resolve_tool_state(
                state,
                index_hint=idx_hint,
                call_id=call_id,
                aliases=aliases,
                name_hint=name_text if name_text else None,
            )
            if thought_signature:
                ts["thought_signature"] = thought_signature

            name_delta = (
                self._normalize_tool_field_delta(name_text, ts, "name")
                if name_text
                else ""
            )
            if ts.get("name_so_far"):
                ts["name"] = ts.get("name_so_far")

            args_delta = (
                self._normalize_tool_field_delta(args_text, ts, "args")
                if isinstance(args_text, str) and args_text
                else ""
            )

            needs_header = not bool(ts.get("header_sent"))
            emit_signature = bool(
                ts.get("thought_signature")
                and (needs_header or not ts.get("thought_signature_emitted"))
            )

            fn_delta: Dict[str, Any] = {}
            if needs_header:
                fn_delta["name"] = ts.get("name") or name_text or "tool"
                fn_delta["arguments"] = ""
            elif name_delta:
                fn_delta["name"] = name_delta

            if args_delta:
                fn_delta["arguments"] = args_delta

            if not fn_delta and not emit_signature:
                continue

            payload_tc: Dict[str, Any] = {"index": int(ts.get("index") or 0)}
            if needs_header:
                payload_tc["id"] = str(ts.get("id") or f"call_{payload_tc['index']}")
                payload_tc["type"] = "function"
            if fn_delta:
                payload_tc["function"] = fn_delta
            if emit_signature:
                payload_tc.setdefault("function", {})
                payload_tc = self._apply_tool_thought_signature(
                    payload_tc, ts.get("thought_signature")
                )
                ts["thought_signature_emitted"] = True

            ts["header_sent"] = True
            state["tool_call_seen"] = True
            out.append(payload_tc)

        return out

    def _parse_chat_event(
        self, event: dict, state: dict
    ) -> Optional[Union[dict, List[dict]]]:
        if "error" in event:
            message = self._as_text(event.get("error"))
            return {"error": {"message": message}}

        if isinstance(event.get("choices"), list):
            normalized = self._normalize_openai_chat_chunk(event, state)
            if normalized is not None:
                return normalized

        anth = self._anthropic_event_to_chunk(event, state)
        if anth is not None:
            return anth

        gem = self._gemini_candidates_to_chunk(event, state)
        if gem is not None:
            return gem

        return None

    def _web_search_call_details(self, item: dict) -> Tuple[str, List[str], str]:
        action = item.get("action") if isinstance(item.get("action"), dict) else {}
        action_type = str(action.get("type") or "search").strip()

        raw_queries = action.get("queries")
        if not isinstance(raw_queries, list):
            raw_queries = item.get("queries")
        queries = [
            str(query).strip()
            for query in (raw_queries if isinstance(raw_queries, list) else [])
            if str(query).strip()
        ]

        query = str(action.get("query") or item.get("query") or "").strip()
        if not query and queries:
            query = queries[0]

        return query, queries, action_type

    def _record_web_search_call(self, item: dict, state: dict) -> dict:
        call_id = str(item.get("id") or item.get("item_id") or "").strip()
        if not call_id:
            return item

        calls = state.setdefault("web_search_calls", {})
        previous = calls.get(call_id) if isinstance(calls.get(call_id), dict) else {}
        merged = dict(previous)
        merged.update(item)
        calls[call_id] = merged
        return merged

    def _web_search_call_item_from_event(self, event: dict, state: dict) -> dict:
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        if item:
            return self._record_web_search_call(item, state)

        item_id = str(event.get("item_id") or "").strip()
        calls = state.setdefault("web_search_calls", {})
        existing = calls.get(item_id) if item_id else None
        if isinstance(existing, dict):
            return existing

        fallback = {"id": item_id, "type": "web_search_call"}
        if item_id:
            calls[item_id] = fallback
        return fallback

    def _emit_web_search_call_status(
        self, item: dict, state: dict, phase: str
    ) -> None:
        query, queries, _ = self._web_search_call_details(item)
        done = phase == "complete"
        description = (
            'Searched "{{searchQuery}}"'
            if done and query
            else "Web search completed"
            if done
            else 'Searching "{{searchQuery}}"'
            if query
            else "Searching the web"
        )

        extra = {
            "status": "complete" if done else "in_progress",
            "item_id": item.get("id"),
        }
        if query:
            extra["query"] = query
        if queries:
            extra["queries"] = queries

        self._emit_status(
            state.get("__event_emitter__"),
            "web_search",
            description,
            done,
            extra=extra,
        )

    def _format_web_search_call_without_results(self, item: dict, state: dict) -> list:
        query, queries, action_type = self._web_search_call_details(item)
        call_id = str(item.get("id") or "web_search_call").strip()
        arguments = {
            "action": action_type or "search",
        }
        if query:
            arguments["query"] = query
        if queries:
            arguments["queries"] = queries

        result_lines = [
            "Web search completed.",
            "The upstream provider did not include raw search result items for this call.",
        ]
        if query:
            result_lines.insert(1, f"Query: {query}")

        details = (
            '\n<details type="tool_calls" done="true" '
            f'id="{html.escape(call_id, quote=True)}" '
            'name="Web Search" '
            f'arguments="{html.escape(json.dumps(arguments, ensure_ascii=False), quote=True)}">\n'
            "<summary>Tool Executed</summary>\n"
            f"{html.escape(chr(10).join(result_lines))}\n"
            "</details>\n"
        )
        state["text_seen"] = True
        return [{"choices": [{"delta": {"content": details}}]}]

    def _parse_responses_event(
        self, event: dict, state: dict
    ) -> Optional[Union[dict, List[dict]]]:
        # Some gateways proxy responses as normal chat chunks.
        if isinstance(event.get("choices"), list):
            normalized = self._normalize_openai_chat_chunk(event, state)
            if normalized is not None:
                return normalized

        event_type = str(event.get("type") or "").strip().lower()
        if event_type == "response.output_text.delta":
            delta = self._normalize_stream_delta(event.get("delta"), state, "content")
            if delta:
                state["text_seen"] = True
                content_chunk = {"choices": [{"delta": {"content": delta}}]}
                placeholder_chunk = self._stream_reasoning_placeholder_chunk(state)
                if placeholder_chunk:
                    return [placeholder_chunk, content_chunk]
                return content_chunk
            return None

        if event_type == "response.output_text.done":
            if state.get("text_seen"):
                return None
            delta = self._normalize_stream_delta(event.get("text"), state, "content")
            if delta:
                state["text_seen"] = True
                content_chunk = {"choices": [{"delta": {"content": delta}}]}
                placeholder_chunk = self._stream_reasoning_placeholder_chunk(state)
                if placeholder_chunk:
                    return [placeholder_chunk, content_chunk]
                return content_chunk
            return None

        if event_type in (
            "response.reasoning_summary_text.delta",
            "response.reasoning_text.delta",
        ):
            if not self._show_reasoning_enabled():
                return None
            delta = self._normalize_stream_delta(event.get("delta"), state, "reasoning")
            if delta:
                state["reasoning_seen"] = True
                return {"choices": [{"delta": {"reasoning_content": delta}}]}
            return None

        if event_type in (
            "response.reasoning_summary_text.done",
            "response.reasoning_text.done",
        ):
            if not self._show_reasoning_enabled():
                return None
            delta = self._normalize_stream_delta(event.get("text"), state, "reasoning")
            if delta:
                state["reasoning_seen"] = True
                return {"choices": [{"delta": {"reasoning_content": delta}}]}
            return None

        if event_type in ("response.output_item.added", "response.output_item.done"):
            item = event.get("item") if isinstance(event.get("item"), dict) else {}
            if str(item.get("type") or "").strip().lower() == "function_call":
                return self._responses_tool_item_to_chunks(
                    item, state, include_args=(event_type.endswith(".done"))
                )
            if str(item.get("type") or "").strip().lower() == "web_search_call":
                item = self._record_web_search_call(item, state)
                if event_type.endswith(".added"):
                    self._emit_web_search_call_status(item, state, "in_progress")
                    return None
                if event_type.endswith(".done"):
                    return self._format_web_search_call_result(item, state)
                return None
            if str(item.get("type") or "").strip().lower() in (
                "output_image",
                "image",
                "image_generation_call",
            ):
                image_md = self._part_to_image_markdown(item)
                if image_md:
                    state["text_seen"] = True
                    content_chunk = {"choices": [{"delta": {"content": image_md}}]}
                    placeholder_chunk = self._stream_reasoning_placeholder_chunk(state)
                    if placeholder_chunk:
                        return [placeholder_chunk, content_chunk]
                    return content_chunk
            return None

        if event_type in (
            "response.web_search_call.in_progress",
            "response.web_search_call.searching",
            "response.web_search_call.completed",
        ):
            item = self._web_search_call_item_from_event(event, state)
            if event_type.endswith(".in_progress"):
                self._emit_web_search_call_status(item, state, "in_progress")
            elif event_type.endswith(".searching"):
                self._emit_web_search_call_status(item, state, "searching")
            return None

        if event_type in ("response.output_image.delta", "response.image.delta"):
            image_md = self._part_to_image_markdown(
                {
                    "b64_json": event.get("b64_json") or event.get("delta"),
                    "image_url": event.get("image_url"),
                    "mime_type": event.get("mime_type") or event.get("mimeType"),
                }
            )
            if image_md:
                state["text_seen"] = True
                content_chunk = {"choices": [{"delta": {"content": image_md}}]}
                placeholder_chunk = self._stream_reasoning_placeholder_chunk(state)
                if placeholder_chunk:
                    return [placeholder_chunk, content_chunk]
                return content_chunk
            return None

        if event_type in ("response.output_image.done", "response.image.done"):
            image_md = self._part_to_image_markdown(
                {
                    "b64_json": event.get("b64_json") or event.get("image_base64"),
                    "image_url": event.get("image_url") or event.get("url"),
                    "mime_type": event.get("mime_type") or event.get("mimeType"),
                }
            )
            if image_md:
                state["text_seen"] = True
                content_chunk = {"choices": [{"delta": {"content": image_md}}]}
                placeholder_chunk = self._stream_reasoning_placeholder_chunk(state)
                if placeholder_chunk:
                    return [placeholder_chunk, content_chunk]
                return content_chunk
            return None

        if event_type in (
            "response.function_call_arguments.delta",
            "response.function_call.arguments.delta",
        ):
            return self._responses_tool_args_delta_chunk(event, state)

        if event_type in (
            "response.function_call_arguments.done",
            "response.function_call.arguments.done",
        ):
            return self._responses_tool_args_done_chunk(event, state)

        if event_type in ("error", "response.failed"):
            err = event.get("error") if isinstance(event.get("error"), dict) else event
            message = (
                err.get("message")
                if isinstance(err, dict) and isinstance(err.get("message"), str)
                else self._as_text(err)
            )
            return {"error": {"message": message}}

        if event_type == "response.completed":
            response_obj = (
                event.get("response") if isinstance(event.get("response"), dict) else {}
            )
            chunks: List[dict] = []
            usage = self._normalize_usage(response_obj.get("usage"))
            if usage:
                chunks.append({"usage": usage})
                usage_reasoning_tokens = self._extract_reasoning_tokens_from_usage(
                    usage
                )
                if usage_reasoning_tokens > 0:
                    state["reasoning_tokens"] = usage_reasoning_tokens

            if not state.get("reasoning_seen") and self._show_reasoning_enabled():
                reasoning = self._extract_responses_reasoning(response_obj)
                if reasoning:
                    state["reasoning_seen"] = True
                    chunks.append(
                        {"choices": [{"delta": {"reasoning_content": reasoning}}]}
                    )
                elif (
                    not state.get("reasoning_placeholder_emitted")
                ) and self._should_emit_gpt_reasoning_placeholder(
                    model=state.get("model_name"),
                    reasoning_requested=bool(state.get("reasoning_requested")),
                    usage=usage,
                ):
                    placeholder = self._gpt_reasoning_placeholder(
                        int(state.get("reasoning_tokens") or 0)
                    )
                    if placeholder:
                        state["reasoning_placeholder_emitted"] = True
                        state["reasoning_seen"] = True
                        chunks.append(
                            {"choices": [{"delta": {"reasoning_content": placeholder}}]}
                        )

            if not state.get("text_seen"):
                text = self._extract_responses_text(response_obj)
                if text:
                    chunks.append({"choices": [{"delta": {"content": text}}]})

            tool_calls = self._extract_responses_tool_calls(
                response_obj,
                alias_map=state.get("tool_name_alias_map", {}),
                tool_name_prefix=state.get("tool_name_prefix", ""),
            )
            if tool_calls and not state.get("tool_call_seen"):
                normalized_tool_calls = self._normalize_tool_calls_delta(
                    tool_calls, state
                )
                if normalized_tool_calls:
                    chunks.append(
                        {"choices": [{"delta": {"tool_calls": normalized_tool_calls}}]}
                    )

            finish = "tool_calls" if state.get("tool_call_seen") else "stop"
            chunks.append({"choices": [{"delta": {}, "finish_reason": finish}]})
            return [
                (
                    self._retain_cache_metadata(chunk, response_obj)
                    if isinstance(chunk, dict)
                    else chunk
                )
                for chunk in chunks
            ]

        anth = self._anthropic_event_to_chunk(event, state)
        if anth is not None:
            return anth

        gem = self._gemini_candidates_to_chunk(event, state)
        if gem is not None:
            return gem

        return None

    def _normalize_openai_chat_chunk(self, event: dict, state: dict) -> Optional[dict]:
        choices = event.get("choices")
        if not isinstance(choices, list):
            return None

        out_choices: List[dict] = []
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            out_choice: Dict[str, Any] = {}
            if "index" in choice:
                out_choice["index"] = choice.get("index")

            out_delta: Dict[str, Any] = {}
            delta = choice.get("delta")
            if isinstance(delta, dict):
                role = delta.get("role")
                if isinstance(role, str):
                    out_delta["role"] = role

                content_raw = delta.get("content")
                content_text = self._extract_text_from_content_field(content_raw)
                if content_text:
                    content_text = self._normalize_stream_delta(
                        content_text, state, "content"
                    )
                    if content_text:
                        state["text_seen"] = True
                        out_delta["content"] = content_text

                if self._show_reasoning_enabled():
                    reasoning_text = self._coerce_reasoning_text(
                        delta.get("reasoning_content")
                        or delta.get("reasoning")
                        or delta.get("thinking")
                        or delta.get("thought")
                    )
                    if reasoning_text:
                        reasoning_text = self._normalize_stream_delta(
                            reasoning_text, state, "reasoning"
                        )
                        if reasoning_text:
                            state["reasoning_seen"] = True
                            out_delta["reasoning_content"] = reasoning_text

                tool_calls = delta.get("tool_calls")
                if isinstance(tool_calls, list) and tool_calls:
                    normalized_tool_calls = self._normalize_tool_calls_delta(
                        tool_calls, state
                    )
                    if normalized_tool_calls:
                        out_delta["tool_calls"] = normalized_tool_calls

                function_call = delta.get("function_call")
                if isinstance(function_call, dict):
                    normalized_tool_calls = self._normalize_tool_calls_delta(
                        [
                            {
                                "index": 0,
                                "id": "legacy_function_call",
                                "type": "function",
                                "function": {
                                    "name": str(function_call.get("name") or "tool"),
                                    "arguments": str(
                                        function_call.get("arguments") or ""
                                    ),
                                },
                            }
                        ],
                        state,
                    )
                    if normalized_tool_calls:
                        out_delta["tool_calls"] = normalized_tool_calls

            if out_delta:
                out_choice["delta"] = out_delta

            finish_reason = self._map_finish_reason(
                choice.get("finish_reason") or choice.get("stop_reason")
            )
            if finish_reason == "stop" and state.get("tool_call_seen"):
                finish_reason = None
            if finish_reason is not None:
                out_choice["finish_reason"] = finish_reason
                if finish_reason == "tool_calls":
                    state["tool_call_seen"] = True
                out_choice.setdefault("delta", {})

            if out_choice:
                out_choices.append(out_choice)

        if not out_choices:
            return None

        out: Dict[str, Any] = {"choices": out_choices}
        for key in ("id", "object", "created", "model", "system_fingerprint"):
            if key in event:
                out[key] = event.get(key)
        usage = self._normalize_usage(event.get("usage"))
        if usage:
            out["usage"] = usage
            usage_reasoning_tokens = self._extract_reasoning_tokens_from_usage(usage)
            if usage_reasoning_tokens > 0:
                state["reasoning_tokens"] = usage_reasoning_tokens

        if (not state.get("reasoning_seen")) and (
            not state.get("reasoning_placeholder_emitted")
        ):
            target_choice = next(
                (
                    choice
                    for choice in out_choices
                    if isinstance(choice, dict)
                    and isinstance(choice.get("delta"), dict)
                    and isinstance(choice.get("delta", {}).get("content"), str)
                    and bool(choice.get("delta", {}).get("content"))
                    and not choice.get("delta", {}).get("reasoning_content")
                ),
                None,
            )
            if isinstance(target_choice, dict):
                placeholder_chunk = self._stream_reasoning_placeholder_chunk(
                    state, usage=usage
                )
                if placeholder_chunk:
                    delta_obj = target_choice.setdefault("delta", {})
                    if isinstance(delta_obj, dict):
                        delta_obj["reasoning_content"] = (
                            placeholder_chunk.get("choices", [{}])[0]
                            .get("delta", {})
                            .get("reasoning_content")
                        )

        has_finish_reason = any(
            isinstance(choice, dict) and choice.get("finish_reason") is not None
            for choice in out_choices
        )
        if (
            has_finish_reason
            and (not state.get("reasoning_seen"))
            and (not state.get("reasoning_placeholder_emitted"))
            and self._should_emit_gpt_reasoning_placeholder(
                model=state.get("model_name"),
                reasoning_requested=bool(state.get("reasoning_requested")),
                usage=usage,
            )
        ):
            placeholder = self._gpt_reasoning_placeholder(
                int(state.get("reasoning_tokens") or 0)
            )
            if placeholder:
                target_choice = next(
                    (choice for choice in out_choices if isinstance(choice, dict)),
                    None,
                )
                if isinstance(target_choice, dict):
                    delta_obj = target_choice.setdefault("delta", {})
                    if isinstance(delta_obj, dict):
                        delta_obj["reasoning_content"] = placeholder
                        state["reasoning_seen"] = True
        return self._retain_cache_metadata(out, event)

    def _anthropic_event_to_chunk(self, event: dict, state: dict) -> Optional[dict]:
        event_type = str(event.get("type") or "").strip().lower()
        if not event_type:
            return None

        if event_type == "content_block_start":
            block = (
                event.get("content_block")
                if isinstance(event.get("content_block"), dict)
                else {}
            )
            btype = str(block.get("type") or "").strip().lower()
            if btype == "tool_use":
                idx = int(event.get("index") or len(state.get("tool_states") or {}))
                call_id = str(block.get("id") or f"call_{idx}")
                name = str(block.get("name") or "tool")
                ts = self._resolve_tool_state(
                    state,
                    index_hint=idx,
                    call_id=call_id,
                    name_hint=name,
                )
                if ts.get("name_so_far"):
                    ts["name"] = ts.get("name_so_far")
                if not ts.get("header_sent"):
                    ts["header_sent"] = True
                    state["tool_call_seen"] = True
                    return {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": int(ts.get("index") or idx),
                                            "id": str(ts.get("id") or call_id),
                                            "type": "function",
                                            "function": {
                                                "name": str(ts.get("name") or name),
                                                "arguments": "",
                                            },
                                        }
                                    ]
                                }
                            }
                        ]
                    }
            return None

        if event_type == "content_block_delta":
            delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
            dtype = str(delta.get("type") or "").strip().lower()
            if dtype == "text_delta":
                text = self._normalize_stream_delta(delta.get("text"), state, "content")
                if text:
                    state["text_seen"] = True
                    return {"choices": [{"delta": {"content": text}}]}
                return None
            if dtype in ("thinking_delta", "thought_delta"):
                if not self._show_reasoning_enabled():
                    return None
                text = self._normalize_stream_delta(
                    delta.get("thinking") or delta.get("text"),
                    state,
                    "reasoning",
                )
                if text:
                    state["reasoning_seen"] = True
                    return {"choices": [{"delta": {"reasoning_content": text}}]}
                return None
            if dtype == "input_json_delta":
                idx = int(event.get("index") or 0)
                tool_state = self._resolve_tool_state(state, index_hint=idx)
                json_delta = str(delta.get("partial_json") or "")
                if not json_delta:
                    return None
                json_delta = self._normalize_tool_field_delta(
                    json_delta, tool_state, "args"
                )
                if not json_delta:
                    return None
                state["tool_call_seen"] = True
                return {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": int(tool_state.get("index") or idx),
                                        "function": {"arguments": json_delta},
                                    }
                                ]
                            }
                        }
                    ]
                }
            return None

        if event_type == "message_delta":
            reason = self._map_finish_reason(
                event.get("delta", {}).get("stop_reason")
                if isinstance(event.get("delta"), dict)
                else None
            )
            if reason:
                return {"choices": [{"delta": {}, "finish_reason": reason}]}
            return None

        if event_type == "message_stop":
            finish = "tool_calls" if state.get("tool_call_seen") else "stop"
            return {"choices": [{"delta": {}, "finish_reason": finish}]}

        if event_type == "error":
            err = event.get("error") if isinstance(event.get("error"), dict) else event
            return {"error": {"message": self._as_text(err)}}

        return None

    def _gemini_candidates_to_chunk(self, event: dict, state: dict) -> Optional[dict]:
        candidates = event.get("candidates")
        if not isinstance(candidates, list):
            return None

        out_choices: List[dict] = []
        for idx, cand in enumerate(candidates):
            if not isinstance(cand, dict):
                continue

            content = (
                cand.get("content") if isinstance(cand.get("content"), dict) else {}
            )
            parts = (
                content.get("parts") if isinstance(content.get("parts"), list) else []
            )

            text_chunks: List[str] = []
            thought_chunks: List[str] = []
            raw_tool_calls: List[dict] = []

            for part_idx, part in enumerate(parts):
                if not isinstance(part, dict):
                    continue
                function_call = part.get("functionCall")
                if isinstance(function_call, dict):
                    name = str(function_call.get("name") or "").strip()
                    if name:
                        arguments = self._ensure_json_string(
                            function_call.get("args", {})
                        )
                        call_id = str(
                            function_call.get("id")
                            or function_call.get("call_id")
                            or f"gem_{idx}_{part_idx}_{name}"
                        ).strip()
                        tool_call = {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": arguments},
                        }
                        thought_signature = self._extract_tool_thought_signature(
                            function_call
                        )
                        if thought_signature:
                            tool_call = self._apply_tool_thought_signature(
                                tool_call, thought_signature
                            )
                        raw_tool_calls.append(tool_call)
                image_md = self._part_to_image_markdown(part)
                if image_md:
                    text_chunks.append(image_md)
                    continue
                text = part.get("text")
                if not isinstance(text, str) or not text:
                    continue
                if bool(part.get("thought")):
                    thought_chunks.append(text)
                else:
                    text_chunks.append(text)

            out_delta: Dict[str, Any] = {}
            if text_chunks:
                merged = self._normalize_stream_delta(
                    "".join(text_chunks), state, "content"
                )
                if merged:
                    state["text_seen"] = True
                    out_delta["content"] = merged
            if thought_chunks and self._show_reasoning_enabled():
                merged = self._normalize_stream_delta(
                    "".join(thought_chunks), state, "reasoning"
                )
                if merged:
                    state["reasoning_seen"] = True
                    out_delta["reasoning_content"] = merged
            if raw_tool_calls:
                normalized_tool_calls = self._normalize_tool_calls_delta(
                    raw_tool_calls, state
                )
                if normalized_tool_calls:
                    out_delta["tool_calls"] = normalized_tool_calls

            out_choice: Dict[str, Any] = {"index": cand.get("index", idx)}
            if out_delta:
                out_choice["delta"] = out_delta

            finish = self._map_finish_reason(
                cand.get("finishReason") or cand.get("finish_reason")
            )
            if finish:
                out_choice["finish_reason"] = finish
                if finish == "tool_calls":
                    state["tool_call_seen"] = True
                out_choice.setdefault("delta", {})

            if "delta" in out_choice or "finish_reason" in out_choice:
                out_choices.append(out_choice)

        if not out_choices:
            return None

        out: Dict[str, Any] = {"choices": out_choices}
        usage = self._normalize_gemini_usage(event.get("usageMetadata"))
        if usage:
            out["usage"] = usage
        return out

    def _responses_tool_item_to_chunks(
        self, item: dict, state: dict, include_args: bool = False
    ) -> List[dict]:
        chunks: List[dict] = []
        item_id = str(item.get("id") or "").strip()
        call_id = str(item.get("call_id") or item_id or "").strip()
        name = str(item.get("name") or "").strip() or "tool"
        name = self._resolve_effective_tool_name(
            name,
            alias_map=state.get("tool_name_alias_map", {}),
            tool_name_prefix=state.get("tool_name_prefix", ""),
        )
        arguments = self._coerce_argument_fragment(item.get("arguments"))
        thought_signature = self._extract_tool_thought_signature(item)
        if not call_id:
            call_id = f"call_{state.get('next_tool_index', 0)}"

        ts = self._resolve_tool_state(
            state,
            index_hint=self._coerce_tool_index(item.get("index")),
            call_id=call_id,
            aliases=[item_id] if item_id else None,
            name_hint=name,
        )

        if name:
            _ = self._normalize_tool_field_delta(name, ts, "name")
            if ts.get("name_so_far"):
                ts["name"] = ts.get("name_so_far")
        if thought_signature:
            ts["thought_signature"] = thought_signature

        state["tool_call_seen"] = True
        if not ts.get("header_sent"):
            tool_call = {
                "index": int(ts.get("index") or 0),
                "id": str(ts.get("id") or call_id),
                "type": "function",
                "function": {
                    "name": str(ts.get("name") or name),
                    "arguments": "",
                },
            }
            if ts.get("thought_signature"):
                tool_call = self._apply_tool_thought_signature(
                    tool_call, ts.get("thought_signature")
                )
                ts["thought_signature_emitted"] = True
            chunks.append({"choices": [{"delta": {"tool_calls": [tool_call]}}]})
            ts["header_sent"] = True

        if include_args and arguments:
            args_delta = self._normalize_tool_field_delta(arguments, ts, "args")
            if args_delta:
                chunks.append(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": int(ts.get("index") or 0),
                                            "function": {"arguments": args_delta},
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                )
        return chunks

    def _responses_tool_args_delta_chunk(
        self, event: dict, state: dict
    ) -> Optional[Union[dict, List[dict]]]:
        call_id = str(event.get("call_id") or "").strip()
        item_id = str(event.get("item_id") or "").strip()
        if not call_id and not item_id:
            return None
        delta_raw = event.get("delta")
        if delta_raw is None:
            delta_raw = event.get("arguments_delta")
        delta = self._coerce_argument_fragment(delta_raw)
        if not delta:
            return None

        name_hint = str(event.get("name") or "").strip() or None
        if isinstance(name_hint, str) and name_hint:
            name_hint = self._resolve_effective_tool_name(
                name_hint,
                alias_map=state.get("tool_name_alias_map", {}),
                tool_name_prefix=state.get("tool_name_prefix", ""),
            )
        thought_signature = self._extract_tool_thought_signature(event)
        ts = self._resolve_tool_state(
            state,
            index_hint=self._coerce_tool_index(event.get("index")),
            call_id=call_id or item_id,
            aliases=[item_id] if item_id else None,
            name_hint=name_hint,
        )
        if name_hint:
            _ = self._normalize_tool_field_delta(name_hint, ts, "name")
            if ts.get("name_so_far"):
                ts["name"] = ts.get("name_so_far")
        if thought_signature:
            ts["thought_signature"] = thought_signature

        args_delta = self._normalize_tool_field_delta(delta, ts, "args")
        emit_signature = bool(
            ts.get("thought_signature") and not ts.get("thought_signature_emitted")
        )
        if not args_delta and ts.get("header_sent") and not emit_signature:
            return None

        state["tool_call_seen"] = True
        chunks: List[dict] = []

        if not ts.get("header_sent"):
            tool_call = {
                "index": int(ts.get("index") or 0),
                "id": str(ts.get("id") or (call_id or item_id)),
                "type": "function",
                "function": {
                    "name": str(ts.get("name") or "tool"),
                    "arguments": "",
                },
            }
            if ts.get("thought_signature"):
                tool_call = self._apply_tool_thought_signature(
                    tool_call, ts.get("thought_signature")
                )
                ts["thought_signature_emitted"] = True
            chunks.append({"choices": [{"delta": {"tool_calls": [tool_call]}}]})
            ts["header_sent"] = True
        elif emit_signature:
            tool_call = self._apply_tool_thought_signature(
                {"index": int(ts.get("index") or 0), "function": {}},
                ts.get("thought_signature"),
            )
            ts["thought_signature_emitted"] = True
            chunks.append({"choices": [{"delta": {"tool_calls": [tool_call]}}]})

        if args_delta:
            chunks.append(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": int(ts.get("index") or 0),
                                        "function": {"arguments": args_delta},
                                    }
                                ]
                            }
                        }
                    ]
                }
            )

        if not chunks:
            return None
        return chunks[0] if len(chunks) == 1 else chunks

    def _responses_tool_args_done_chunk(
        self, event: dict, state: dict
    ) -> Optional[Union[dict, List[dict]]]:
        call_id = str(event.get("call_id") or "").strip()
        item_id = str(event.get("item_id") or "").strip()
        if not call_id and not item_id:
            return None
        arguments = self._coerce_argument_fragment(event.get("arguments"))
        if not arguments:
            return None

        name_hint = str(event.get("name") or "").strip() or None
        if isinstance(name_hint, str) and name_hint:
            name_hint = self._resolve_effective_tool_name(
                name_hint,
                alias_map=state.get("tool_name_alias_map", {}),
                tool_name_prefix=state.get("tool_name_prefix", ""),
            )
        thought_signature = self._extract_tool_thought_signature(event)
        ts = self._resolve_tool_state(
            state,
            index_hint=self._coerce_tool_index(event.get("index")),
            call_id=call_id or item_id,
            aliases=[item_id] if item_id else None,
            name_hint=name_hint,
        )
        if name_hint:
            _ = self._normalize_tool_field_delta(name_hint, ts, "name")
            if ts.get("name_so_far"):
                ts["name"] = ts.get("name_so_far")
        if thought_signature:
            ts["thought_signature"] = thought_signature

        args_delta = self._normalize_tool_field_delta(arguments, ts, "args")
        emit_signature = bool(
            ts.get("thought_signature") and not ts.get("thought_signature_emitted")
        )
        if not args_delta and ts.get("header_sent") and not emit_signature:
            return None

        state["tool_call_seen"] = True
        chunks: List[dict] = []

        if not ts.get("header_sent"):
            tool_call = {
                "index": int(ts.get("index") or 0),
                "id": str(ts.get("id") or (call_id or item_id)),
                "type": "function",
                "function": {
                    "name": str(ts.get("name") or "tool"),
                    "arguments": "",
                },
            }
            if ts.get("thought_signature"):
                tool_call = self._apply_tool_thought_signature(
                    tool_call, ts.get("thought_signature")
                )
                ts["thought_signature_emitted"] = True
            chunks.append({"choices": [{"delta": {"tool_calls": [tool_call]}}]})
            ts["header_sent"] = True
        elif emit_signature:
            tool_call = self._apply_tool_thought_signature(
                {"index": int(ts.get("index") or 0), "function": {}},
                ts.get("thought_signature"),
            )
            ts["thought_signature_emitted"] = True
            chunks.append({"choices": [{"delta": {"tool_calls": [tool_call]}}]})

        if args_delta:
            chunks.append(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": int(ts.get("index") or 0),
                                        "function": {"arguments": args_delta},
                                    }
                                ]
                            }
                        }
                    ]
                }
            )

        if not chunks:
            return None
        return chunks[0] if len(chunks) == 1 else chunks

    # -------------------------------------------------------------------------
    # Non-stream response normalization
    # -------------------------------------------------------------------------

    def _normalize_chat_non_stream_response(
        self,
        response: requests.Response,
        payload: dict,
        tool_name_prefix: str = "",
    ) -> Union[str, dict]:
        try:
            data = response.json()
        except Exception:
            return response.text[:2000]

        if not isinstance(data, dict):
            return self._as_text(data)

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return data

        choice0 = choices[0] if isinstance(choices[0], dict) else {}
        message = (
            choice0.get("message") if isinstance(choice0.get("message"), dict) else {}
        )
        if not message and isinstance(choice0.get("delta"), dict):
            message = dict(choice0.get("delta") or {})

        content_value = message.get("content")
        if isinstance(content_value, list):
            normalized = self._extract_text_from_content_field(content_value)
            if normalized:
                message["content"] = normalized

        # Some providers place generated image payloads outside text content.
        if not message.get("content"):
            image_candidates = []
            if isinstance(choice0.get("images"), list):
                image_candidates.extend(choice0.get("images") or [])
            if isinstance(message.get("images"), list):
                image_candidates.extend(message.get("images") or [])
            for img in image_candidates:
                if isinstance(img, dict):
                    md = self._part_to_image_markdown(img)
                    if md:
                        message["content"] = (message.get("content") or "") + md

        if self._show_reasoning_enabled():
            reasoning = self._coerce_reasoning_text(
                message.get("reasoning_content")
                or message.get("reasoning")
                or message.get("thinking")
                or choice0.get("reasoning_content")
            )
            if reasoning:
                message["reasoning_content"] = reasoning
            else:
                usage = self._normalize_usage(data.get("usage"))
                if self._should_emit_gpt_reasoning_placeholder(
                    model=payload.get("model") or data.get("model") or "",
                    reasoning_requested=isinstance(payload.get("reasoning"), dict),
                    usage=usage,
                ):
                    message["reasoning_content"] = self._gpt_reasoning_placeholder(
                        self._extract_reasoning_tokens_from_usage(usage)
                    )
                if usage:
                    data["usage"] = usage

        alias_map = self._build_tool_name_alias_map(payload)
        if isinstance(message.get("tool_calls"), list) and message.get("tool_calls"):
            message["tool_calls"] = self._remap_tool_calls(
                message.get("tool_calls"),
                alias_map,
                tool_name_prefix=tool_name_prefix,
            )
            choice0["finish_reason"] = "tool_calls"
            choice0["message"] = message
            data["choices"][0] = choice0
            return self._retain_cache_metadata(data, data)

        choice0["message"] = message
        data["choices"][0] = choice0
        return self._retain_cache_metadata(data, data)

    def _normalize_responses_non_stream_response(
        self,
        response: requests.Response,
        payload: dict,
        tool_name_prefix: str = "",
    ) -> Union[str, dict]:
        try:
            data = response.json()
        except Exception:
            return response.text[:2000]

        if not isinstance(data, dict):
            return self._as_text(data)

        text = self._extract_responses_text(data)
        reasoning = (
            self._extract_responses_reasoning(data)
            if self._show_reasoning_enabled()
            else ""
        )
        alias_map = self._build_tool_name_alias_map(payload)
        tool_calls = self._extract_responses_tool_calls(
            data, alias_map=alias_map, tool_name_prefix=tool_name_prefix
        )
        usage = self._normalize_usage(data.get("usage"))

        message: Dict[str, Any] = {"role": "assistant", "content": text or ""}
        finish_reason = "stop"
        if tool_calls:
            finish_reason = "tool_calls"
            message["tool_calls"] = tool_calls
            if not text:
                message["content"] = None
        if reasoning:
            message["reasoning_content"] = reasoning
        elif self._should_emit_gpt_reasoning_placeholder(
            model=payload.get("model") or data.get("model") or "",
            reasoning_requested=isinstance(payload.get("reasoning"), dict),
            usage=usage,
        ):
            message["reasoning_content"] = self._gpt_reasoning_placeholder(
                self._extract_reasoning_tokens_from_usage(usage)
            )

        out: Dict[str, Any] = {
            "id": data.get("id", f"resp_{uuid.uuid4().hex}"),
            "object": "chat.completion",
            "model": payload.get("model") or data.get("model") or "",
            "choices": [
                {"index": 0, "message": message, "finish_reason": finish_reason}
            ],
        }
        if usage:
            out["usage"] = usage
        return self._retain_cache_metadata(out, data)

    def _extract_responses_text(self, response_json: dict) -> str:
        if not isinstance(response_json, dict):
            return self._as_text(response_json)

        output_text = response_json.get("output_text")
        if isinstance(output_text, str) and output_text:
            return output_text

        chunks: List[str] = []
        for item in response_json.get("output", []) or []:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip().lower()
            if item_type == "message":
                for part in item.get("content", []) or []:
                    if not isinstance(part, dict):
                        continue
                    image_md = self._part_to_image_markdown(part)
                    if image_md:
                        chunks.append(image_md)
                        continue
                    ptype = str(part.get("type") or "").strip().lower()
                    if ptype in ("output_text", "text"):
                        text = part.get("text")
                        if isinstance(text, str):
                            chunks.append(text)
                        elif isinstance(text, dict) and isinstance(
                            text.get("value"), str
                        ):
                            chunks.append(text["value"])
            elif item_type in ("output_image", "image", "image_generation_call"):
                image_md = self._part_to_image_markdown(item)
                if image_md:
                    chunks.append(image_md)
            elif item_type in ("output_text", "text"):
                text = item.get("text")
                if isinstance(text, str):
                    chunks.append(text)

        return "".join(chunks).strip("\r\n")

    def _extract_responses_reasoning(self, response_json: dict) -> str:
        if not isinstance(response_json, dict):
            return ""

        chunks: List[str] = []
        for item in response_json.get("output", []) or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "").strip().lower() != "reasoning":
                continue

            summary = item.get("summary")
            if isinstance(summary, list):
                for part in summary:
                    if isinstance(part, str):
                        if part:
                            chunks.append(part)
                        continue
                    if not isinstance(part, dict):
                        continue
                    ptype = str(part.get("type") or "").strip().lower()
                    if ptype in ("summary_text", "reasoning_summary_text", "text"):
                        text = part.get("text")
                        if isinstance(text, str) and text:
                            chunks.append(text)
                    elif isinstance(part.get("text"), str):
                        chunks.append(part.get("text"))

            summary_text = item.get("summary_text")
            if isinstance(summary_text, str) and summary_text:
                chunks.append(summary_text)

        return "".join(chunks).strip("\r\n")

    def _extract_responses_tool_calls(
        self,
        response_json: dict,
        alias_map: Optional[dict] = None,
        tool_name_prefix: str = "",
    ) -> List[dict]:
        out: List[dict] = []

        raw = response_json.get("tool_calls")
        if isinstance(raw, list):
            for tc in raw:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                name = str(fn.get("name") or tc.get("name") or "").strip()
                if not name:
                    continue
                name = self._resolve_effective_tool_name(
                    name,
                    alias_map=alias_map,
                    tool_name_prefix=tool_name_prefix,
                )
                args = fn.get("arguments", tc.get("arguments", "{}"))
                tool_call = {
                    "id": str(tc.get("id") or f"call_{len(out)}"),
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": self._ensure_json_string(args),
                    },
                }
                thought_signature = self._extract_tool_thought_signature(tc)
                if thought_signature:
                    tool_call = self._apply_tool_thought_signature(
                        tool_call, thought_signature
                    )
                out.append(tool_call)

        for item in response_json.get("output", []) or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "").strip().lower() != "function_call":
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            name = self._resolve_effective_tool_name(
                name,
                alias_map=alias_map,
                tool_name_prefix=tool_name_prefix,
            )
            call_id = str(item.get("call_id") or item.get("id") or f"call_{len(out)}")
            args = item.get("arguments")
            if args is None:
                args = item.get("input", {})
            tool_call = {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": self._ensure_json_string(args),
                },
            }
            thought_signature = self._extract_tool_thought_signature(item)
            if thought_signature:
                tool_call = self._apply_tool_thought_signature(
                    tool_call, thought_signature
                )
            out.append(tool_call)

        deduped: List[dict] = []
        seen = set()
        for tc in out:
            key = (tc.get("id"), (tc.get("function") or {}).get("name"))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(tc)
        return deduped

    def _normalize_usage(self, usage: Any) -> Optional[dict]:
        if not isinstance(usage, dict):
            return None
        out: Dict[str, Any] = dict(usage)

        def _as_int(v: Any) -> Optional[int]:
            if isinstance(v, bool):
                return None
            if isinstance(v, (int, float)):
                return int(v)
            if isinstance(v, str):
                s = v.strip()
                if not s:
                    return None
                try:
                    return int(float(s))
                except Exception:
                    return None
            return None

        prompt = _as_int(out.get("prompt_tokens"))
        completion = _as_int(out.get("completion_tokens"))
        input_tokens = _as_int(out.get("input_tokens"))
        output_tokens = _as_int(out.get("output_tokens"))
        total = _as_int(out.get("total_tokens"))
        reasoning = _as_int(out.get("reasoning_tokens"))

        if prompt is None and input_tokens is not None:
            prompt = input_tokens
        if completion is None and output_tokens is not None:
            completion = output_tokens
        if input_tokens is None and prompt is not None:
            input_tokens = prompt
        if output_tokens is None and completion is not None:
            output_tokens = completion
        if total is None:
            total = (input_tokens or 0) + (output_tokens or 0)

        if prompt is not None:
            out["prompt_tokens"] = prompt
        if completion is not None:
            out["completion_tokens"] = completion
        if input_tokens is not None:
            out["input_tokens"] = input_tokens
        if output_tokens is not None:
            out["output_tokens"] = output_tokens
        out["total_tokens"] = total

        if reasoning is not None:
            out["reasoning_tokens"] = reasoning
            details = out.get("completion_tokens_details")
            if not isinstance(details, dict):
                details = {}
                out["completion_tokens_details"] = details
            details.setdefault("reasoning_tokens", reasoning)

        return out

    def _normalize_gemini_usage(self, usage_meta: Any) -> Optional[dict]:
        if not isinstance(usage_meta, dict):
            return None

        prompt = usage_meta.get("promptTokenCount")
        completion = usage_meta.get("candidatesTokenCount")
        total = usage_meta.get("totalTokenCount")
        reasoning = usage_meta.get("thoughtsTokenCount")

        usage: Dict[str, Any] = {}
        if isinstance(prompt, (int, float)):
            usage["prompt_tokens"] = int(prompt)
            usage["input_tokens"] = int(prompt)
        if isinstance(completion, (int, float)):
            usage["completion_tokens"] = int(completion)
            usage["output_tokens"] = int(completion)
        if isinstance(total, (int, float)):
            usage["total_tokens"] = int(total)
        if isinstance(reasoning, (int, float)):
            usage["reasoning_tokens"] = int(reasoning)
            usage["completion_tokens_details"] = {"reasoning_tokens": int(reasoning)}
        if usage:
            usage.setdefault(
                "total_tokens",
                (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0),
            )
            return usage
        return None

    def _format_web_search_call_result(self, item: dict, state: dict) -> Optional[list]:
        """Format web_search_call results as markdown block and emit source events.

        Returns list of content delta chunks, or None if no results/not ready.
        Source events are emitted via __event_emitter__ if available.
        """
        if str(item.get("status") or "").strip().lower() != "completed":
            return None

        self._emit_web_search_call_status(item, state, "complete")

        query, _, _ = self._web_search_call_details(item)
        results = item.get("results")
        if not isinstance(results, list) or not results:
            return self._format_web_search_call_without_results(item, state)

        chunks = []
        state["text_seen"] = True

        # Emit source events FIRST (before text) so OpenWebUI builds the citation index
        emitter = state.get("__event_emitter__")
        if emitter:
            import asyncio
            for i, r in enumerate(results, 1):
                title = str(r.get("title") or "").strip() or "Untitled"
                url = str(r.get("url") or "").strip()
                text = str(r.get("text") or r.get("summary") or "").strip()
                source_id = url if url else f"web_search_result_{i}"
                source_event = {
                    "type": "source",
                    "data": {
                        "source": {
                            "name": title,
                            "url": url if url else None,
                        },
                        "document": [text] if text else [],
                        "metadata": [{
                            "source": source_id,
                            "name": f"[{i}] {title}",
                        }],
                    },
                }
                try:
                    result = emitter(source_event)
                    if asyncio.iscoroutine(result):
                        pass
                except Exception:
                    pass

        # Build a markdown formatted search result block
        lines = []
        if query:
            lines.append(f"\n\n🌐 **Web Search**: {query}\n")
        else:
            lines.append("\n\n🌐 **Web Search Results**\n")

        for i, r in enumerate(results, 1):
            title = str(r.get("title") or "").strip() or "Untitled"
            url = str(r.get("url") or "").strip()
            text = str(r.get("text") or r.get("summary") or "").strip()

            if url:
                lines.append(f"{i}. **[{title}]({url})**")
            else:
                lines.append(f"{i}. **{title}**")
            if text:
                # Truncate long snippets
                if len(text) > 300:
                    text = text[:297] + "..."
                lines.append(f"   > {text}")
            lines.append("")

        result_text = "\n".join(lines) + "\n"

        # Emit as content delta chunk
        placeholder_chunk = self._stream_reasoning_placeholder_chunk(state)
        if placeholder_chunk:
            chunks.append(placeholder_chunk)
        chunks.append({"choices": [{"delta": {"content": result_text}}]})

        return chunks if chunks else None
