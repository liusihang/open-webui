"""
title: Bifrost Unified Manifold Pipe
authors: codex
version: 0.9.2.1
required_open_webui_version: 0.9.2
license: MIT
"""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib
import json
import mimetypes
import os
import re
from typing import Any, Dict, Generator, List, Optional, Tuple, Union
from urllib.parse import urlparse

import requests
from pydantic import BaseModel, Field


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
            description="Routing mode: chat | responses | auto",
        )
        REQUEST_TIMEOUT_SECS: int = Field(
            default=300,
            description="HTTP timeout in seconds",
        )
        INLINE_FILE_MAX_BYTES: int = Field(
            default=20 * 1024 * 1024,
            description="Max bytes per uploaded inline file",
        )
        ATTACHMENT_TEXT_FALLBACK_MAX_CHARS: int = Field(
            default=60000,
            description="Max chars for text attachment fallback",
        )
        ENABLE_PROMPT_CACHE_MARKERS: bool = Field(
            default=True,
            description="Inject cache_control markers for Anthropic prompt caching",
        )
        ENABLE_AUTO_PROMPT_CACHE_KEY: bool = Field(
            default=False,
            description="Auto-generate prompt_cache_key for GPT models when absent",
        )
        PROMPT_CACHE_MIN_TEXT_CHARS: int = Field(
            default=1024,
            description="Minimum text length before cache markers are added",
        )
        PROMPT_CACHE_MAX_MARKERS: int = Field(
            default=4,
            description="Maximum prompt cache markers per request",
        )
        DEBUG_MODE: bool = Field(
            default=False,
            description="Enable debug logs",
        )

    def __init__(self):
        self.type = "manifold"
        self.id = "bifrostapi"
        self.name = "bifrostapi."
        self.valves = self.Valves(
            BIFROST_API_KEY=os.getenv("BIFROST_API_KEY", os.getenv("OPENAI_API_KEY", "")),
            BIFROST_BASE_URL=os.getenv(
                "BIFROST_BASE_URL", os.getenv("OPENAI_BASE_URL", "https://api.maximhq.com/v1")
            ),
        )

    def pipes(self) -> List[dict]:
        return [
            {"id": "openai/gpt-5", "name": "openai/gpt-5"},
            {"id": "openai/gpt-5-mini", "name": "openai/gpt-5-mini"},
            {"id": "anthropic/claude-3.7-sonnet", "name": "anthropic/claude-3.7-sonnet"},
            {"id": "ZenMuxOAI/google/gemini-2.5-flash", "name": "ZenMuxOAI/google/gemini-2.5-flash"},
        ]

    def pipe(
        self,
        body: dict,
        __files__=None,
        __metadata__=None,
        __tools__=None,
        __request__=None,
        __event_emitter__=None,
        __user__=None,
    ) -> Union[str, dict, Generator]:
        if not self.valves.BIFROST_API_KEY:
            return "Error: BIFROST_API_KEY is required"

        raw_messages = body.get("messages", []) or []
        system_message, messages = pop_system_message(raw_messages)
        model = self._normalize_model(body.get("model", ""))
        route_mode = self._resolve_route_mode(body)
        function_specs = self._collect_function_specs(body.get("tools"), __tools__, __metadata__)
        attachments = self._prepare_attachments(
            self._collect_file_candidates(body, __files__, __metadata__),
            __user__,
            model,
        )
        cache_settings = self._resolve_effective_cache_settings(
            body=body,
            model=model,
            route_mode=route_mode,
            system_message=system_message,
            messages=messages,
            attachments=attachments,
            function_specs=function_specs,
        )

        chat_payload = self._build_chat_payload(
            body=body,
            model=model,
            system_message=system_message,
            messages=messages,
            attachments=attachments,
            function_specs=function_specs,
            effective_cache_settings=cache_settings,
        )
        responses_payload = self._build_responses_payload(
            body=body,
            model=model,
            system_message=system_message,
            messages=messages,
            attachments=attachments,
            function_specs=function_specs,
            effective_cache_settings=cache_settings,
        )

        stream = bool(body.get("stream", False))
        if route_mode == "chat":
            return self._dispatch_request("chat", chat_payload, stream=stream)
        if route_mode == "responses":
            return self._dispatch_request("responses", responses_payload, stream=stream)

        result = self._dispatch_request("responses", responses_payload, stream=stream, allow_missing=True)
        if self._is_missing_route_result(result):
            return self._dispatch_request("chat", chat_payload, stream=stream)
        return result

    def _debug(self, message: str) -> None:
        if self.valves.DEBUG_MODE:
            print(f"[bifrostapi] {message}")

    def _valve_bool(self, value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("1", "true", "yes", "on"):
                return True
            if lowered in ("0", "false", "no", "off"):
                return False
        if value is None:
            return default
        return bool(value)

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

    def _api_base_url(self) -> str:
        base = str(self.valves.BIFROST_BASE_URL or "").strip().rstrip("/")
        if not base:
            return "https://api.maximhq.com/v1"
        if base.endswith("/v1"):
            return base
        return f"{base}/v1"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.valves.BIFROST_API_KEY}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def _normalize_model(self, raw_model_id: Any) -> str:
        model_id = str(raw_model_id or "").strip()
        if model_id.startswith(f"{self.id}."):
            model_id = model_id.split(".", 1)[1]
        if model_id.startswith("bifrostapi."):
            model_id = model_id.split(".", 1)[1]
        for prefix in ("bifrost/", "openai/"):
            if model_id.startswith(prefix):
                model_id = model_id[len(prefix) :]
        return model_id or "openai/gpt-5-mini"

    def _is_gpt_model_name(self, model: Any) -> bool:
        if not isinstance(model, str):
            return False
        name = model.strip().lower()
        if not name:
            return False
        parts = [part for part in name.split("/") if part]
        tail = parts[-1] if parts else name
        return tail.startswith("gpt") or "openai/gpt" in name or "/gpt-" in name

    def _is_gemini_model_name(self, model: Any) -> bool:
        if not isinstance(model, str):
            return False
        name = model.strip().lower()
        return name.startswith("gemini/") or "/gemini" in name

    def _is_anthropic_model_name(self, model: Any) -> bool:
        if not isinstance(model, str):
            return False
        name = model.strip().lower()
        return name.startswith("anthropic/") or "/claude" in name or "claude" in name

    def _resolve_route_mode(self, body: dict) -> str:
        request_mode = str(self._body_param(body, "route_mode", self._body_param(body, "api_mode", ""))).strip().lower()
        valve_mode = str(self.valves.ROUTE_MODE or "").strip().lower()
        mode = request_mode or valve_mode or "auto"
        if mode not in ("chat", "responses", "auto"):
            mode = "auto"
        if mode == "auto" and not request_mode:
            model = self._normalize_model(body.get("model", "")).lower()
            if model.startswith("zenmuxoai/google/gemini"):
                return "chat"
            if model.startswith("zenmuxoai/z-ai/glm") or model.startswith("z-ai/glm"):
                return "responses"
        return mode

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
            return value.strip() if isinstance(value, str) else ""

        def _as_bool(value: Any, default: bool = False) -> bool:
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in ("1", "true", "yes", "on"):
                    return True
                if lowered in ("0", "false", "no", "off"):
                    return False
            return default

        prompt_cache_key = _as_text(self._body_param(body, "prompt_cache_key"))
        prompt_cache_retention = _as_text(self._body_param(body, "prompt_cache_retention"))
        cached_content = _as_text(self._body_param(body, "cached_content"))
        return {
            "provider": self._detect_cache_provider(model),
            "prompt_cache_key": prompt_cache_key,
            "prompt_cache_retention": prompt_cache_retention,
            "cached_content": cached_content,
            "enable_prompt_caching": _as_bool(
                self._body_param(body, "enable_prompt_caching"),
                default=bool(prompt_cache_key),
            ),
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
        raw_spec = spec.get("function") if isinstance(spec.get("function"), dict) else spec
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
            "description": str(raw_spec.get("description") or spec.get("description") or ""),
            "parameters": self._normalize_tool_parameters(params),
        }

    def _ordered_function_specs(self, function_specs: Any) -> List[dict]:
        if not isinstance(function_specs, list):
            return []
        coerced = []
        for spec in function_specs:
            normalized = self._coerce_function_spec(spec)
            if normalized:
                coerced.append(normalized)
        return sorted(
            coerced,
            key=lambda spec: (
                self._stable_sort_text(spec.get("name")),
                self._stable_sort_text(spec.get("description")),
                json.dumps(spec.get("parameters"), ensure_ascii=False, sort_keys=True),
            ),
        )

    def _ordered_attachments(self, attachments: Any) -> List[dict]:
        if not isinstance(attachments, list):
            return []
        out = []
        for item in attachments:
            if isinstance(item, dict):
                out.append(item)
        return sorted(
            out,
            key=lambda item: (
                self._stable_sort_text(item.get("openwebui_file_id")),
                self._stable_sort_text(item.get("filename")),
                self._stable_sort_text(item.get("name")),
                self._stable_sort_text(item.get("kind")),
                self._stable_sort_text(item.get("type")),
                self._stable_sort_text((item.get("chat_part") or {}).get("type") if isinstance(item.get("chat_part"), dict) else ""),
                self._stable_sort_text((item.get("responses_part") or {}).get("type") if isinstance(item.get("responses_part"), dict) else ""),
            ),
        )

    def _stable_cache_tool_summary(self, function_specs: Any) -> List[dict]:
        return [
            {
                "name": str(spec.get("name") or "").strip(),
                "description": str(spec.get("description") or "").strip(),
                "parameters": self._normalize_tool_parameters(spec.get("parameters")),
            }
            for spec in self._ordered_function_specs(function_specs)
        ]

    def _stable_cache_attachment_summary(self, attachments: Any) -> List[dict]:
        out = []
        for item in self._ordered_attachments(attachments):
            summary: Dict[str, str] = {}
            for key in ("openwebui_file_id", "type", "kind", "mime_type", "filename", "name"):
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
                value = content.get(key)
                if isinstance(value, str):
                    return value
            return json.dumps(content, ensure_ascii=False)
        return str(content)

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
            "route_mode": str(route_mode or "").strip().lower(),
            "system": self._content_to_text(system_message.get("content") if isinstance(system_message, dict) else system_message).strip(),
            "tools": self._stable_cache_tool_summary(function_specs),
            "attachments": self._stable_cache_attachment_summary(attachments),
        }
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return f"owg:{digest[:60]}"

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
        prompt_cache_key = str(settings.get("prompt_cache_key") or "").strip()
        if prompt_cache_key:
            settings["prompt_cache_key"] = prompt_cache_key
            return settings
        if not self._is_gpt_model_name(model):
            return settings
        auto_generate = self._valve_bool(
            self._body_param(body, "enable_auto_prompt_cache_key"),
            default=bool(self.valves.ENABLE_AUTO_PROMPT_CACHE_KEY),
        )
        if not auto_generate:
            return settings
        settings["prompt_cache_key"] = self._generate_prompt_cache_key(
            model=model,
            route_mode=route_mode,
            system_message=system_message,
            attachments=attachments,
            function_specs=function_specs,
        )
        return settings

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

    def _should_apply_prompt_cache_markers(self, body: dict, model: str) -> bool:
        if not self._prompt_cache_markers_enabled():
            return False
        if not self._is_anthropic_model_name(model):
            return False
        explicit = self._body_param(body, "enable_prompt_caching")
        return explicit is True

    def _cache_control_marker(self) -> dict:
        return {"type": "ephemeral"}

    def _mark_chat_content_for_cache(self, content: Any, min_chars: int) -> Tuple[Any, bool]:
        if isinstance(content, str):
            if len(content.strip()) >= min_chars:
                return ([{"type": "text", "text": content, "cache_control": self._cache_control_marker()}], True)
            return content, False
        if isinstance(content, list):
            out = []
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

    def _mark_responses_content_for_cache(self, content: Any, min_chars: int) -> Tuple[Any, bool]:
        if not isinstance(content, list):
            return content, False
        out = []
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

    def _apply_prompt_cache_markers(self, payload: dict, route_mode: str, body: dict, model: str) -> None:
        if not isinstance(payload, dict) or not self._should_apply_prompt_cache_markers(body, model):
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
                    new_content, marked = self._mark_chat_content_for_cache(message.get("content"), min_chars)
                    if marked:
                        message["content"] = new_content
                        remaining -= 1
            if remaining > 0:
                self._mark_tools_for_cache(payload.get("tools"), remaining)
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
                    new_content, marked = self._mark_responses_content_for_cache(item.get("content"), min_chars)
                    if marked:
                        item["content"] = new_content
                        remaining -= 1
            if remaining > 0:
                self._mark_tools_for_cache(payload.get("tools"), remaining)

    def _clean_tool_schema_node(self, schema: Any) -> None:
        if not isinstance(schema, dict):
            return
        any_of = schema.get("anyOf")
        if isinstance(any_of, list):
            non_null_types = [item for item in any_of if isinstance(item, dict) and item.get("type") != "null"]
            if len(non_null_types) == 1:
                schema.clear()
                schema.update(copy.deepcopy(non_null_types[0]))
            else:
                schema["anyOf"] = non_null_types
        if "default" in schema and schema.get("default") is None:
            del schema["default"]
        if "type" not in schema and "anyOf" not in schema and "properties" not in schema:
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
        if schema_type == "object" and not isinstance(schema.get("properties"), dict):
            schema["properties"] = {}
        if isinstance(schema.get("required"), list):
            schema["required"] = [item for item in schema["required"] if isinstance(item, str)]
        self._clean_tool_schema_node(schema)
        return schema

    def _collect_function_specs(self, body_tools: Any, runtime_tools: Any, metadata: Any = None) -> List[dict]:
        candidates: List[dict] = []
        if isinstance(body_tools, list):
            for tool in body_tools:
                normalized = self._coerce_function_spec(tool)
                if normalized:
                    candidates.append(normalized)
        elif isinstance(runtime_tools, dict):
            for item in runtime_tools.values():
                if not isinstance(item, dict):
                    continue
                spec = item.get("spec")
                normalized = self._coerce_function_spec(spec)
                if normalized:
                    candidates.append(normalized)
        deduped = []
        seen = set()
        for spec in candidates:
            name = str(spec.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            deduped.append(spec)
        return self._ordered_function_specs(deduped)

    def _tools_for_chat(self, function_specs: List[dict]) -> List[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.get("name"),
                    "description": spec.get("description", ""),
                    "parameters": self._normalize_tool_parameters(spec.get("parameters")),
                },
            }
            for spec in self._ordered_function_specs(function_specs)
        ]

    def _tools_for_responses(self, function_specs: List[dict]) -> List[dict]:
        return [
            {
                "type": "function",
                "name": spec.get("name"),
                "description": spec.get("description", ""),
                "parameters": self._normalize_tool_parameters(spec.get("parameters")),
            }
            for spec in self._ordered_function_specs(function_specs)
        ]

    def _tool_choice_for_chat(self, tool_choice: Any) -> Any:
        if tool_choice is None:
            return None
        if isinstance(tool_choice, str):
            value = tool_choice.strip().lower()
            return value if value in ("auto", "none", "required") else "auto"
        if isinstance(tool_choice, dict):
            kind = str(tool_choice.get("type") or "").strip().lower()
            if kind in ("auto", "none", "required"):
                return kind
            if kind in ("function", "tool"):
                function = tool_choice.get("function") if isinstance(tool_choice.get("function"), dict) else {}
                name = str(function.get("name") or tool_choice.get("name") or "").strip()
                if name:
                    return {"type": "function", "function": {"name": name}}
        return None

    def _tool_choice_for_responses(self, tool_choice: Any) -> Any:
        if tool_choice is None:
            return None
        if isinstance(tool_choice, str):
            value = tool_choice.strip().lower()
            return value if value in ("auto", "none", "required") else "auto"
        if isinstance(tool_choice, dict):
            kind = str(tool_choice.get("type") or "").strip().lower()
            if kind in ("auto", "none", "required"):
                return kind
            if kind in ("function", "tool"):
                function = tool_choice.get("function") if isinstance(tool_choice.get("function"), dict) else {}
                name = str(function.get("name") or tool_choice.get("name") or "").strip()
                if name:
                    return {"type": "function", "name": name}
        return None

    def _collect_file_candidates(self, body: dict, files_arg: Any, metadata_arg: Any) -> List[dict]:
        candidates: List[dict] = []
        if isinstance(files_arg, list):
            candidates.extend(item for item in files_arg if isinstance(item, dict))
        if isinstance(metadata_arg, dict) and isinstance(metadata_arg.get("files"), list):
            candidates.extend(item for item in metadata_arg.get("files") if isinstance(item, dict))
        if isinstance(body.get("metadata"), dict) and isinstance(body["metadata"].get("files"), list):
            candidates.extend(item for item in body["metadata"].get("files") if isinstance(item, dict))
        if isinstance(body.get("files"), list):
            candidates.extend(item for item in body.get("files") if isinstance(item, dict))
        return [item for item in candidates if item.get("_adaptive_excluded") is not True]

    def _extract_openwebui_file_id(self, item: dict) -> str:
        if not isinstance(item, dict):
            return ""
        value = str(item.get("id") or "").strip()
        if value:
            return value
        nested = item.get("file")
        if isinstance(nested, dict):
            value = str(nested.get("id") or "").strip()
            if value:
                return value
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

    def _can_read_openwebui_file(self, file_record: Any, file_id: str, user: Any) -> bool:
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
            return None
        try:
            local_path = Storage.get_file(record.path)
        except Exception:
            return None
        if not local_path or not os.path.isfile(local_path):
            return None
        filename = getattr(record, "filename", None) or os.path.basename(local_path)
        content_type = ((getattr(record, "meta", {}) or {}).get("content_type")) or mimetypes.guess_type(filename)[0] or "application/octet-stream"
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

    def _max_inline_bytes(self) -> int:
        try:
            value = int(self.valves.INLINE_FILE_MAX_BYTES or 0)
        except Exception:
            value = 20 * 1024 * 1024
        return max(1, value)

    def _max_text_fallback_chars(self) -> int:
        try:
            value = int(self.valves.ATTACHMENT_TEXT_FALLBACK_MAX_CHARS or 0)
        except Exception:
            value = 60000
        return max(200, value)

    def _safe_filename(self, filename: str) -> str:
        raw = os.path.basename(str(filename or "")).strip() or "upload"
        cleaned = "".join(ch if (ch.isalnum() or ch in ("-", "_", ".")) else "_" for ch in raw).strip("._")
        if not cleaned:
            cleaned = "upload"
        return cleaned[:160]

    def _is_image_file(self, resolved: dict) -> bool:
        content_type = str(resolved.get("content_type") or "").strip().lower()
        if content_type.startswith("image/"):
            return True
        filename = str(resolved.get("filename") or "").lower()
        return filename.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic"))

    def _is_text_like_file(self, resolved: dict) -> bool:
        content_type = str(resolved.get("content_type") or "").strip().lower()
        if content_type.startswith("text/"):
            return True
        filename = str(resolved.get("filename") or "").lower()
        return filename.endswith((".txt", ".md", ".markdown", ".json", ".csv", ".tsv", ".yaml", ".yml", ".xml", ".html", ".htm", ".log", ".py", ".js", ".ts", ".sql"))

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
        content_type = str(resolved.get("content_type") or "").strip() or "application/octet-stream"
        data = self._read_binary(resolved)
        return f"data:{content_type};base64,{base64.b64encode(data).decode(ascii)}"

    def _extract_text_fallback(self, resolved: dict) -> str:
        path = str(resolved.get("local_path") or "")
        if not path or not os.path.isfile(path):
            return ""
        max_chars = self._max_text_fallback_chars()
        max_bytes = min(self._max_inline_bytes(), 3 * 1024 * 1024)
        try:
            with open(path, "rb") as file:
                data = file.read(max_bytes)
            text = data.decode("utf-8", errors="replace").strip()
        except Exception:
            return ""
        return text[:max_chars] if text else ""

    def _prepare_attachments(self, file_items: List[dict], user: Any, model: Any = None) -> List[dict]:
        attachments: List[dict] = []
        seen_ids = set()
        for item in file_items:
            if not isinstance(item, dict):
                continue
            if any(key in item for key in ("chat_part", "responses_part", "fallback_text")):
                normalized = {
                    "openwebui_file_id": str(item.get("openwebui_file_id") or "").strip(),
                    "filename": str(item.get("filename") or item.get("name") or "").strip(),
                    "name": str(item.get("name") or item.get("filename") or "attachment").strip() or "attachment",
                    "type": str(item.get("type") or "").strip(),
                    "kind": str(item.get("kind") or "").strip(),
                    "mime_type": str(item.get("mime_type") or "").strip(),
                    "chat_part": copy.deepcopy(item.get("chat_part")),
                    "responses_part": copy.deepcopy(item.get("responses_part")),
                    "fallback_text": item.get("fallback_text"),
                }
                attachments.append(normalized)
                continue
            resolved = self._resolve_file_item(item, user)
            if not resolved:
                text = self._content_to_text(item.get("content"))
                if text:
                    attachments.append(
                        {
                            "kind": "text",
                            "name": str(item.get("name") or item.get("id") or "context"),
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
            name = str(resolved.get("filename") or "attachment")
            if self._is_image_file(resolved):
                data_url = self._build_data_url(resolved)
                attachments.append(
                    {
                        "openwebui_file_id": file_id,
                        "filename": name,
                        "name": name,
                        "mime_type": str(resolved.get("content_type") or "").strip(),
                        "kind": "image",
                        "chat_part": {"type": "image_url", "image_url": {"url": data_url}},
                        "responses_part": {"type": "input_image", "image_url": data_url},
                        "fallback_text": None,
                    }
                )
                continue
            if self._is_text_like_file(resolved):
                text = self._extract_text_fallback(resolved)
                if text:
                    attachments.append(
                        {
                            "openwebui_file_id": file_id,
                            "filename": name,
                            "name": name,
                            "mime_type": str(resolved.get("content_type") or "").strip(),
                            "kind": "text",
                            "chat_part": None,
                            "responses_part": None,
                            "fallback_text": text,
                        }
                    )
                    continue
            data_url = self._build_data_url(resolved)
            safe_name = self._safe_filename(name)
            attachments.append(
                {
                    "openwebui_file_id": file_id,
                    "filename": name,
                    "name": name,
                    "mime_type": str(resolved.get("content_type") or "").strip(),
                    "kind": "document",
                    "chat_part": {"type": "file", "file": {"filename": safe_name, "file_data": data_url}},
                    "responses_part": {"type": "input_file", "filename": safe_name, "file_data": data_url},
                    "fallback_text": None,
                }
            )
        return self._ordered_attachments(attachments)

    def _normalize_chat_content(self, content: Any, role: str) -> Any:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[dict] = []
            for part in content:
                if isinstance(part, str):
                    if part:
                        parts.append({"type": "text", "text": part})
                    continue
                if not isinstance(part, dict):
                    continue
                ptype = str(part.get("type") or "").strip().lower()
                if ptype in ("text", "image_url", "input_audio", "file"):
                    parts.append(copy.deepcopy(part))
                    continue
                if ptype in ("input_text", "output_text"):
                    parts.append({"type": "text", "text": str(part.get("text") or "")})
                    continue
                if isinstance(part.get("text"), str):
                    parts.append({"type": "text", "text": str(part.get("text") or "")})
            if parts:
                return parts
        text = self._content_to_text(content)
        return text if text else ("" if role != "assistant" else None)

    def _content_to_responses_parts(self, content: Any, role: str) -> List[dict]:
        out: List[dict] = []
        text_part_type = "output_text" if role == "assistant" else "input_text"
        if isinstance(content, str):
            if content:
                out.append({"type": text_part_type, "text": content})
            return out
        if isinstance(content, list):
            for part in content:
                if isinstance(part, str):
                    if part:
                        out.append({"type": text_part_type, "text": part})
                    continue
                if not isinstance(part, dict):
                    continue
                ptype = str(part.get("type") or "").strip().lower()
                if ptype in ("text", "input_text", "output_text"):
                    text = part.get("text")
                    if isinstance(text, str) and text:
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
                    file_obj = part.get("file") if isinstance(part.get("file"), dict) else {}
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
                if isinstance(part.get("text"), str) and part.get("text"):
                    out.append({"type": text_part_type, "text": part.get("text")})
            if out:
                return out
        text = self._content_to_text(content)
        if text:
            out.append({"type": text_part_type, "text": text})
        return out

    def _messages_to_chat_messages(self, messages: List[dict], system_message: Any) -> List[dict]:
        out: List[dict] = []
        if system_message is not None:
            system_text = self._content_to_text(system_message.get("content") if isinstance(system_message, dict) else system_message)
            if system_text:
                out.append({"role": "system", "content": system_text})
        for msg in messages or []:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or "user").strip().lower()
            if role not in ("system", "user", "assistant", "tool"):
                role = "user"
            item: Dict[str, Any] = {
                "role": role,
                "content": self._normalize_chat_content(msg.get("content"), role=role),
            }
            if role == "assistant" and isinstance(msg.get("tool_calls"), list) and msg.get("tool_calls"):
                item["tool_calls"] = copy.deepcopy(msg.get("tool_calls"))
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
            instructions = self._content_to_text(system_message.get("content") if isinstance(system_message, dict) else system_message).strip()
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
                call_id = str(msg.get("tool_call_id") or msg.get("id") or "").strip()
                if call_id and not degrade_tool_history_to_messages:
                    item = {"type": "function_call_output", "output": output_text}
                    if not omit_call_ids:
                        item["call_id"] = call_id
                    input_items.append(item)
                else:
                    input_items.append({"type": "message", "role": "tool", "content": [{"type": "input_text", "text": output_text}]})
                continue
            if role == "assistant" and isinstance(msg.get("tool_calls"), list) and msg.get("tool_calls"):
                if degrade_tool_history_to_messages:
                    history_lines = []
                    for tc in msg.get("tool_calls"):
                        function = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                        name = str(function.get("name") or tc.get("name") or "").strip()
                        if not name:
                            continue
                        arguments = function.get("arguments", tc.get("arguments", {}))
                        history_lines.append(f"Called tool {name} with arguments {self._ensure_json_string(arguments)}.")
                    if history_lines:
                        input_items.append({"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "\n".join(history_lines)}]})
                    continue
                for tool_index, tc in enumerate(msg.get("tool_calls")):
                    if not isinstance(tc, dict):
                        continue
                    function = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                    name = str(function.get("name") or "").strip()
                    if not name:
                        continue
                    item = {
                        "type": "function_call",
                        "name": name,
                        "arguments": self._ensure_json_string(function.get("arguments", tc.get("arguments", {}))),
                    }
                    if not omit_call_ids:
                        item["call_id"] = str(tc.get("id") or f"call_m{msg_index}_t{tool_index}")
                    input_items.append(item)
                continue
            role_name = "assistant" if role == "assistant" else "user"
            parts = self._content_to_responses_parts(content, role=role_name)
            if parts:
                input_items.append({"type": "message", "role": role_name, "content": parts})
        if not input_items:
            input_items.append({"type": "message", "role": "user", "content": [{"type": "input_text", "text": ""}]})
        return input_items, instructions

    def _attach_attachments_to_last_user_chat(self, chat_messages: List[dict], attachments: List[dict]) -> List[dict]:
        if not attachments:
            return chat_messages
        out = [dict(message) if isinstance(message, dict) else message for message in chat_messages or []]
        idx = None
        for i in range(len(out) - 1, -1, -1):
            msg = out[i]
            if isinstance(msg, dict) and str(msg.get("role") or "").strip().lower() == "user":
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
                    content_parts.append(copy.deepcopy(part))
                elif isinstance(part, str) and part:
                    content_parts.append({"type": "text", "text": part})
        elif isinstance(content, str) and content:
            content_parts.append({"type": "text", "text": content})
        for attachment in attachments or []:
            chat_part = attachment.get("chat_part")
            if isinstance(chat_part, dict):
                content_parts.append(copy.deepcopy(chat_part))
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

    def _attach_attachments_to_last_user_responses(self, input_items: List[dict], attachments: List[dict]) -> List[dict]:
        if not attachments:
            return input_items
        out = [dict(item) if isinstance(item, dict) else item for item in input_items or []]
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
                    content_parts.append(copy.deepcopy(part))
        for attachment in attachments or []:
            responses_part = attachment.get("responses_part")
            if isinstance(responses_part, dict):
                content_parts.append(copy.deepcopy(responses_part))
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

    def _merge_payload_sections(self, stable_prefix: Dict[str, Any], volatile_tail: Dict[str, Any]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        payload.update(stable_prefix or {})
        payload.update(volatile_tail or {})
        return payload

    def _build_chat_payload(
        self,
        body: dict,
        model: str,
        system_message: Any,
        messages: List[dict],
        attachments: List[dict],
        function_specs: List[dict],
        effective_cache_settings: Optional[dict] = None,
    ) -> dict:
        chat_messages = self._attach_attachments_to_last_user_chat(
            self._messages_to_chat_messages(messages, system_message),
            attachments,
        )
        stable_payload: Dict[str, Any] = {"model": model, "messages": chat_messages}
        volatile_payload: Dict[str, Any] = {"stream": bool(body.get("stream", False))}
        cache_settings = dict(effective_cache_settings or self._resolve_effective_cache_settings(body, model, "chat", system_message, messages, attachments, function_specs))
        max_tokens = self._body_param(body, "max_completion_tokens")
        if not (isinstance(max_tokens, (int, float)) and int(max_tokens) > 0):
            max_tokens = self._body_param(body, "max_tokens")
        if isinstance(max_tokens, (int, float)) and int(max_tokens) > 0:
            stable_payload["max_completion_tokens"] = int(max_tokens)
            if self._is_anthropic_model_name(model):
                stable_payload["max_tokens"] = int(max_tokens)
        for key in ("temperature", "top_p", "response_format", "metadata", "stream_options"):
            value = self._body_param(body, key)
            if value is not None:
                target = stable_payload if key in ("temperature", "top_p", "response_format") else volatile_payload
                target[key] = copy.deepcopy(value)
        if isinstance(cache_settings.get("prompt_cache_key"), str) and cache_settings["prompt_cache_key"].strip():
            stable_payload["prompt_cache_key"] = cache_settings["prompt_cache_key"].strip()
        if self._is_gpt_model_name(model) and isinstance(cache_settings.get("prompt_cache_retention"), str) and cache_settings["prompt_cache_retention"].strip():
            stable_payload["prompt_cache_retention"] = cache_settings["prompt_cache_retention"].strip()
        if isinstance(cache_settings.get("cached_content"), str) and cache_settings["cached_content"].strip():
            stable_payload["cached_content"] = cache_settings["cached_content"].strip()
        chat_tools = self._tools_for_chat(function_specs)
        if chat_tools:
            stable_payload["tools"] = chat_tools
            choice = self._tool_choice_for_chat(body.get("tool_choice"))
            if choice is not None:
                stable_payload["tool_choice"] = choice
        parallel_tool_calls = self._body_param(body, "parallel_tool_calls")
        if isinstance(parallel_tool_calls, bool):
            volatile_payload["parallel_tool_calls"] = parallel_tool_calls
        payload = self._merge_payload_sections(stable_payload, volatile_payload)
        self._apply_prompt_cache_markers(payload, route_mode="chat", body=body, model=model)
        return payload

    def _build_responses_payload(
        self,
        body: dict,
        model: str,
        system_message: Any,
        messages: List[dict],
        attachments: List[dict],
        function_specs: List[dict],
        effective_cache_settings: Optional[dict] = None,
    ) -> dict:
        input_items, instructions = self._messages_to_responses_input(
            messages,
            system_message,
            omit_call_ids=self._is_gemini_model_name(model),
            degrade_tool_history_to_messages=self._is_gemini_model_name(model),
        )
        input_items = self._attach_attachments_to_last_user_responses(input_items, attachments)
        stable_payload: Dict[str, Any] = {"model": model, "input": input_items}
        volatile_payload: Dict[str, Any] = {"stream": bool(body.get("stream", False))}
        cache_settings = dict(effective_cache_settings or self._resolve_effective_cache_settings(body, model, "responses", system_message, messages, attachments, function_specs))
        if instructions:
            stable_payload["instructions"] = instructions
        max_tokens = self._body_param(body, "max_completion_tokens")
        if not (isinstance(max_tokens, (int, float)) and int(max_tokens) > 0):
            max_tokens = self._body_param(body, "max_tokens")
        if isinstance(max_tokens, (int, float)) and int(max_tokens) > 0:
            stable_payload["max_output_tokens"] = int(max_tokens)
        for key in ("temperature", "top_p", "metadata", "previous_response_id"):
            value = self._body_param(body, key)
            if value is not None:
                target = stable_payload if key in ("temperature", "top_p") else volatile_payload
                target[key] = copy.deepcopy(value)
        if isinstance(cache_settings.get("prompt_cache_key"), str) and cache_settings["prompt_cache_key"].strip():
            stable_payload["prompt_cache_key"] = cache_settings["prompt_cache_key"].strip()
        if self._is_gpt_model_name(model) and isinstance(cache_settings.get("prompt_cache_retention"), str) and cache_settings["prompt_cache_retention"].strip():
            stable_payload["prompt_cache_retention"] = cache_settings["prompt_cache_retention"].strip()
        if isinstance(cache_settings.get("cached_content"), str) and cache_settings["cached_content"].strip():
            stable_payload["cached_content"] = cache_settings["cached_content"].strip()
        responses_tools = self._tools_for_responses(function_specs)
        if responses_tools:
            stable_payload["tools"] = responses_tools
            choice = self._tool_choice_for_responses(body.get("tool_choice"))
            if choice is not None:
                stable_payload["tool_choice"] = choice
        parallel_tool_calls = self._body_param(body, "parallel_tool_calls")
        if isinstance(parallel_tool_calls, bool):
            volatile_payload["parallel_tool_calls"] = parallel_tool_calls
        payload = self._merge_payload_sections(stable_payload, volatile_payload)
        self._apply_prompt_cache_markers(payload, route_mode="responses", body=body, model=model)
        return payload

    def _dispatch_request(
        self,
        route_mode: str,
        payload: dict,
        stream: bool,
        allow_missing: bool = False,
    ) -> Union[str, dict, Generator]:
        endpoint = "/responses" if route_mode == "responses" else "/chat/completions"
        response = requests.post(
            f"{self._api_base_url()}{endpoint}",
            headers=self._headers(),
            json=payload,
            timeout=self.valves.REQUEST_TIMEOUT_SECS,
            stream=stream,
        )
        if response.status_code >= 400:
            detail = response.text[:1200]
            if allow_missing and self._is_missing_route_error(response.status_code, detail):
                return {"_missing_route": True, "status_code": response.status_code, "detail": detail}
            return f"Error: Bifrost request failed (HTTP {response.status_code}): {detail}"
        if stream:
            return self._stream_to_openwebui_chunks(response, route_mode=route_mode)
        try:
            data = response.json()
        except Exception:
            text = response.text.strip()
            return text or "Error: Bifrost returned empty response"
        text = self._extract_nonstream_text(data)
        return text if text else data

    def _is_missing_route_error(self, status_code: int, detail: str) -> bool:
        message = str(detail or "").upper()
        return status_code in (400, 404, 405, 501) and (
            "RESPONSES" in message
            or "CHAT/COMPLETIONS" in message
            or "NOT FOUND" in message
            or "UNSUPPORTED" in message
            or "UNKNOWN ENDPOINT" in message
        )

    def _is_missing_route_result(self, result: Any) -> bool:
        return isinstance(result, dict) and result.get("_missing_route") is True

    def _ensure_json_string(self, value: Any) -> str:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
            return "{}"
        try:
            return json.dumps(value if value is not None else {}, ensure_ascii=False)
        except Exception:
            return "{}"

    def _parse_sse_data(self, raw_line: Any) -> Optional[dict]:
        if not raw_line:
            return None
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else str(raw_line)
        line = line.strip()
        if not line or not line.startswith("data:"):
            return None
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            return None
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def _event_has_incremental_text(self, data: dict) -> bool:
        if not isinstance(data, dict):
            return False
        event_type = str(data.get("type") or "").strip().lower()
        if event_type.endswith(".delta"):
            return True
        choices = data.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                delta = choice.get("delta") if isinstance(choice, dict) else None
                if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                    return True
        return False

    def _is_terminal_response_event(self, data: dict) -> bool:
        if not isinstance(data, dict):
            return False
        event_type = str(data.get("type") or "").strip().lower()
        return event_type in {"response.completed", "response.output_text.done", "done"}

    def _extract_text_fragments(self, payload: Any) -> List[str]:
        out: List[str] = []
        if not isinstance(payload, dict):
            return out
        event_type = str(payload.get("type") or "").strip().lower()
        if event_type.endswith(".delta"):
            delta = payload.get("delta")
            if isinstance(delta, str) and delta:
                out.append(delta)
            elif isinstance(delta, dict):
                text = delta.get("text") or delta.get("value")
                if isinstance(text, str) and text:
                    out.append(text)
        response = payload.get("response") if isinstance(payload.get("response"), dict) else payload
        output_text = response.get("output_text")
        if isinstance(output_text, str) and output_text:
            out.append(output_text)
        output = response.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if isinstance(content, list):
                    for part in content:
                        if not isinstance(part, dict):
                            continue
                        if str(part.get("type") or "").strip().lower() in ("output_text", "text", "input_text"):
                            text = part.get("text")
                            if isinstance(text, dict):
                                text = text.get("value")
                            if isinstance(text, str) and text:
                                out.append(text)
        choices = payload.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta")
                if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                    out.append(delta["content"])
                message = choice.get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str) and content:
                        out.append(content)
                    elif isinstance(content, list):
                        out.extend(self._extract_text_fragments({"response": {"output": [{"content": content}]}}))
        return out

    def _stream_to_openwebui_chunks(self, response, route_mode: str) -> Generator[dict, None, None]:
        saw_incremental_text = False
        for raw_line in response.iter_lines(decode_unicode=True):
            data = self._parse_sse_data(raw_line)
            if not isinstance(data, dict):
                continue
            if route_mode == "responses" and saw_incremental_text and self._is_terminal_response_event(data):
                continue
            for text in self._extract_text_fragments(data):
                if text:
                    yield {
                        "choices": [
                            {
                                "delta": {"content": text},
                                "index": 0,
                                "finish_reason": None,
                            }
                        ]
                    }
            if self._event_has_incremental_text(data):
                saw_incremental_text = True

    def _collect_text_from_stream(self, response) -> str:
        texts: List[str] = []
        saw_incremental_text = False
        for raw_line in response.iter_lines(decode_unicode=True):
            data = self._parse_sse_data(raw_line)
            if not isinstance(data, dict):
                continue
            if saw_incremental_text and self._is_terminal_response_event(data):
                continue
            texts.extend(self._extract_text_fragments(data))
            if self._event_has_incremental_text(data):
                saw_incremental_text = True
        return "".join(texts).strip()

    def _extract_nonstream_text(self, payload: Any) -> str:
        fragments = self._extract_text_fragments(payload)
        if fragments:
            return "".join(fragments).strip()
        return ""
