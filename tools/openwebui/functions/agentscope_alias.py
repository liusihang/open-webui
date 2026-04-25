"""
title: AgentScope Alias Manifold Pipe
authors: codex
version: 0.1.0
required_open_webui_version: 0.9.2
license: MIT
"""

from __future__ import annotations

import importlib
import json
import os
from typing import Any, Dict, Generator, List, Optional, Union

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
        ALIAS_RUNTIME_BASE_URL: str = Field(
            default="http://127.0.0.1:8091",
            description="AgentScope Alias runtime base URL",
        )
        ALIAS_RUNTIME_API_KEY: str = Field(
            default="",
            description="Optional bearer token if Alias runtime is protected",
        )
        DEFAULT_MODEL: str = Field(
            default="ZenMuxOAI/openai/gpt-5.4",
            description="Default model passed to Alias runtime",
        )
        REQUEST_TIMEOUT_SECS: int = Field(
            default=300,
            description="HTTP timeout in seconds",
        )
        DEBUG_MODE: bool = Field(
            default=False,
            description="Enable debug mode",
        )

    def __init__(self):
        self.type = "manifold"
        self.id = "agentscope_alias"
        self.name = "alias/"
        self.valves = self.Valves(
            ALIAS_RUNTIME_BASE_URL=os.getenv(
                "ALIAS_RUNTIME_BASE_URL", "http://127.0.0.1:8091"
            ),
            ALIAS_RUNTIME_API_KEY=os.getenv("ALIAS_RUNTIME_API_KEY", ""),
            DEFAULT_MODEL=os.getenv("ALIAS_RUNTIME_DEFAULT_MODEL", "ZenMuxOAI/openai/gpt-5.4"),
        )

    def pipes(self) -> List[dict]:
        return [
            {
                "id": self.valves.DEFAULT_MODEL,
                "name": self.valves.DEFAULT_MODEL,
            }
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
    ) -> Union[str, Dict[str, Any], Generator]:
        model = self._normalize_model(body.get("model"))
        stream = bool(body.get("stream", False))

        raw_messages = body.get("messages", []) or []
        system_message, messages = pop_system_message(raw_messages)
        payload_input = self._messages_to_responses_input(system_message, messages)

        payload: Dict[str, Any] = {
            "model": model,
            "input": payload_input,
            "stream": True,
        }

        for key in (
            "temperature",
            "top_p",
            "max_output_tokens",
            "presence_penalty",
            "frequency_penalty",
            "metadata",
            "previous_response_id",
            "tool_choice",
            "parallel_tool_calls",
        ):
            if key in body and body.get(key) is not None:
                payload[key] = body.get(key)

        if body.get("tools"):
            payload["tools"] = body.get("tools")

        response = self._post("/compatible-mode/v1/responses", payload, stream=True)
        if response.status_code != 200:
            detail = response.text[:1200]
            return (
                f"Error: Alias runtime request failed "
                f"(HTTP {response.status_code}): {detail}"
            )

        if stream:
            return self._stream_to_openwebui_chunks(response)

        text = self._collect_text_from_stream(response)
        if not text:
            return "Error: Alias runtime returned empty response output"

        return text

    def _api_base_url(self) -> str:
        return str(self.valves.ALIAS_RUNTIME_BASE_URL or "").rstrip("/")

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        token = str(self.valves.ALIAS_RUNTIME_API_KEY or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _post(self, endpoint: str, payload: dict, stream: bool = False):
        return requests.post(
            f"{self._api_base_url()}{endpoint}",
            headers=self._headers(),
            json=payload,
            timeout=self.valves.REQUEST_TIMEOUT_SECS,
            stream=stream,
        )

    def _normalize_model(self, raw_model_id: Any) -> str:
        model_id = str(raw_model_id or "").strip()
        if model_id.startswith(f"{self.id}."):
            model_id = model_id.split(".", 1)[1]
        if model_id.startswith(self.name):
            model_id = model_id[len(self.name) :]
        return model_id or self.valves.DEFAULT_MODEL

    def _messages_to_responses_input(
        self, system_message: Optional[str], messages: List[dict]
    ) -> List[dict]:
        out: List[dict] = []
        if isinstance(system_message, str) and system_message.strip():
            out.append(
                {
                    "type": "message",
                    "role": "developer",
                    "content": [
                        {"type": "input_text", "text": system_message.strip()}
                    ],
                }
            )

        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or "user").strip().lower()
            if role not in ("user", "assistant", "system", "developer"):
                role = "user"
            content = self._normalize_message_content(msg.get("content"))
            if not content:
                continue
            out.append(
                {
                    "type": "message",
                    "role": role,
                    "content": [{"type": "input_text", "text": content}],
                }
            )
        return out

    def _normalize_message_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
                    continue
                if item.get("type") == "text":
                    t = item.get("text") or item.get("content")
                    if isinstance(t, str):
                        parts.append(t)
            return "\n".join(p.strip() for p in parts if p and p.strip())
        return str(content or "").strip()

    def _collect_text_from_stream(self, response) -> str:
        texts: List[str] = []
        saw_incremental_text = False
        for raw_line in response.iter_lines(decode_unicode=True):
            data = self._parse_sse_data(raw_line)
            if not isinstance(data, dict):
                continue
            if saw_incremental_text and self._is_terminal_response_event(data):
                continue
            for text in self._extract_text_fragments(data):
                texts.append(text)
            if self._event_has_incremental_text(data):
                saw_incremental_text = True
        return "".join(texts).strip()

    def _stream_to_openwebui_chunks(self, response) -> Generator[dict, None, None]:
        saw_incremental_text = False
        for raw_line in response.iter_lines(decode_unicode=True):
            data = self._parse_sse_data(raw_line)
            if not isinstance(data, dict):
                continue
            if saw_incremental_text and self._is_terminal_response_event(data):
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
            return json.loads(payload)
        except Exception:
            return None

    def _extract_text_fragments(self, data: dict) -> List[str]:
        fragments: List[str] = []
        if not isinstance(data, dict):
            return fragments

        for choice in data.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            fragments.extend(self._extract_chat_content_fragments(delta.get("content")))
            message = (
                choice.get("message") if isinstance(choice.get("message"), dict) else {}
            )
            fragments.extend(self._extract_chat_content_fragments(message.get("content")))

        event_type = str(data.get("type") or "").strip().lower()
        if event_type in ("response.output_text.delta", "response.output_text.done"):
            text = self._coerce_text(data.get("delta"))
            if text is None:
                text = self._coerce_text(data.get("text"))
            if text:
                fragments.append(text)

        if event_type == "response.completed":
            response_obj = data.get("response")
            if isinstance(response_obj, dict):
                fragments.extend(self._extract_response_payload_text(response_obj))
            return fragments

        response_obj = data.get("response")
        if isinstance(response_obj, dict):
            fragments.extend(self._extract_response_payload_text(response_obj))
        else:
            fragments.extend(self._extract_response_payload_text(data))

        return fragments

    def _extract_response_payload_text(self, payload: Any) -> List[str]:
        if not isinstance(payload, dict):
            return []

        fragments: List[str] = []
        output_text = self._coerce_text(payload.get("output_text"))
        if output_text:
            fragments.append(output_text)

        output = payload.get("output")
        if not isinstance(output, list):
            return fragments

        for item in output:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip().lower()
            if item_type == "message":
                for block in item.get("content") or []:
                    if not isinstance(block, dict):
                        continue
                    text = self._coerce_text(block.get("text"))
                    if text:
                        fragments.append(text)
                        continue
                    fragments.extend(self._extract_chat_content_fragments(block))
            elif item_type in ("output_text", "text"):
                text = self._coerce_text(item.get("text"))
                if text:
                    fragments.append(text)

        return fragments

    def _extract_chat_content_fragments(self, content: Any) -> List[str]:
        if isinstance(content, str):
            return [content] if content else []
        if not isinstance(content, list):
            return []

        fragments: List[str] = []
        for part in content:
            if isinstance(part, str):
                if part:
                    fragments.append(part)
                continue
            if not isinstance(part, dict):
                continue
            text = self._coerce_text(part.get("text"))
            if text:
                fragments.append(text)
                continue
            if str(part.get("type") or "").strip().lower() in ("text", "output_text"):
                text = self._coerce_text(part.get("content"))
                if text:
                    fragments.append(text)
        return fragments

    def _coerce_text(self, value: Any) -> Optional[str]:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in ("value", "text", "content"):
                nested = value.get(key)
                if isinstance(nested, str):
                    return nested
        return None

    def _event_has_incremental_text(self, data: dict) -> bool:
        event_type = str(data.get("type") or "").strip().lower()
        if event_type in ("response.output_text.delta", "response.output_text.done"):
            return bool(self._extract_text_fragments(data))

        for choice in data.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if isinstance(delta, dict) and self._extract_chat_content_fragments(
                delta.get("content")
            ):
                return True
        return False

    def _is_terminal_response_event(self, data: dict) -> bool:
        return str(data.get("type") or "").strip().lower() == "response.completed"
