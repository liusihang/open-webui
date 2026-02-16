import asyncio
import json
import logging
import time
from typing import Any

import aiohttp
from starlette.responses import StreamingResponse

from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL

log = logging.getLogger(__name__)


_THREAD_MAP: dict[str, str] = {}
_THREAD_MAP_LOCK = asyncio.Lock()


def _sse_data(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _normalize_model_id(model_id: str | None, fallback: str) -> str:
    model = str(model_id or "").strip()
    if "." in model:
        model = model.split(".", 1)[1]
    return model or fallback


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, str):
                chunks.append(item)
                continue

            if not isinstance(item, dict):
                continue

            item_type = str(item.get("type") or "").strip().lower()
            if item_type in {"text", "input_text", "output_text"}:
                text = item.get("text")
                if isinstance(text, str):
                    chunks.append(text)
            elif isinstance(item.get("text"), str):
                chunks.append(str(item.get("text")))
            elif "content" in item:
                nested = _content_to_text(item.get("content"))
                if nested:
                    chunks.append(nested)

        return "\n".join([chunk for chunk in chunks if chunk])

    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return str(content.get("text"))
        if "content" in content:
            return _content_to_text(content.get("content"))
        return json.dumps(content, ensure_ascii=False)

    return str(content)


def _messages_to_deerflow(messages: list[dict]) -> list[dict]:
    output: list[dict] = []

    for message in messages:
        if not isinstance(message, dict):
            continue

        role = str(message.get("role") or "").strip().lower()
        if role not in {"system", "user", "assistant"}:
            continue

        text = _content_to_text(message.get("content")).strip()
        if not text:
            continue

        output.append({"role": role, "content": text})

    return output


def _content_value_to_chunks(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []

    if isinstance(value, list):
        output: list[str] = []
        for item in value:
            if isinstance(item, str):
                if item:
                    output.append(item)
                continue

            if not isinstance(item, dict):
                continue

            if isinstance(item.get("text"), str):
                output.append(str(item.get("text")))
                continue

            if "content" in item:
                output.extend(_content_value_to_chunks(item.get("content")))

        return output

    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return [str(value.get("text"))]
        if "content" in value:
            return _content_value_to_chunks(value.get("content"))

    return []


def _extract_stream_chunks(payload: Any) -> list[str]:
    output: list[str] = []

    if isinstance(payload, str):
        return [payload] if payload else []

    if isinstance(payload, list):
        for item in payload:
            output.extend(_extract_stream_chunks(item))
        return output

    if not isinstance(payload, dict):
        return output

    if isinstance(payload.get("content"), (str, list, dict)):
        output.extend(_content_value_to_chunks(payload.get("content")))

    if isinstance(payload.get("text"), str):
        output.append(str(payload.get("text")))

    for key in ("delta", "message", "messages", "chunk", "data", "output"):
        if key in payload:
            output.extend(_extract_stream_chunks(payload.get(key)))

    return output


def _extract_final_assistant_text(payload: Any) -> str:
    if isinstance(payload, dict):
        role = str(payload.get("role") or "").strip().lower()
        if role in {"assistant", "ai"} and "content" in payload:
            return _content_to_text(payload.get("content")).strip()

        messages = payload.get("messages")
        if isinstance(messages, list):
            for message in reversed(messages):
                if not isinstance(message, dict):
                    continue

                message_role = str(message.get("role") or "").strip().lower()
                if message_role in {"assistant", "ai"}:
                    text = _content_to_text(message.get("content")).strip()
                    if text:
                        return text

        for key in ("data", "output", "result", "values", "state"):
            if key in payload:
                text = _extract_final_assistant_text(payload.get(key))
                if text:
                    return text

        return ""

    if isinstance(payload, list):
        for item in reversed(payload):
            text = _extract_final_assistant_text(item)
            if text:
                return text

    return ""


def _infer_status_description(event_name: str, payload: Any) -> str | None:
    lowered = (event_name or "").strip().lower()
    if lowered == "updates":
        if isinstance(payload, dict):
            for key in payload.keys():
                if key not in {"messages", "output", "result", "values", "state"}:
                    return f"Running step: {key}"
        return "Executing research steps"

    if lowered == "values":
        return "Aggregating intermediate results"

    return None


async def _iter_sse_events(response: aiohttp.ClientResponse):
    event_name = "message"
    data_lines: list[str] = []

    while not response.content.at_eof():
        raw_line = await response.content.readline()
        if raw_line == b"":
            break

        line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            if data_lines:
                yield event_name, "\n".join(data_lines)
            event_name = "message"
            data_lines = []
            continue

        if line.startswith(":"):
            continue

        if line.startswith("event:"):
            event_name = line[6:].strip() or "message"
            continue

        if line.startswith("data:"):
            data_lines.append(line[5:].strip())

    if data_lines:
        yield event_name, "\n".join(data_lines)


async def _create_thread(
    session: aiohttp.ClientSession,
    base_url: str,
    headers: dict,
    metadata: dict,
) -> str:
    payload = {
        "metadata": {
            "source": "openwebui",
            "user_id": metadata.get("user_id"),
            "chat_id": metadata.get("chat_id"),
            "message_id": metadata.get("message_id"),
            "created_at": int(time.time()),
        }
    }

    async with session.post(
        f"{base_url}/api/langgraph/threads",
        headers=headers,
        json=payload,
        ssl=AIOHTTP_CLIENT_SESSION_SSL,
    ) as response:
        body_text = await response.text()
        if response.status >= 400:
            raise RuntimeError(
                f"create thread failed HTTP {response.status}: {body_text[:800]}"
            )

        try:
            payload = json.loads(body_text)
        except Exception as exc:
            raise RuntimeError("invalid thread response from DeerFlow") from exc

    thread_id = str(payload.get("thread_id") or payload.get("id") or "").strip()
    if not thread_id:
        raise RuntimeError("create thread failed: missing thread_id")

    return thread_id


async def _resolve_thread_id(
    session: aiohttp.ClientSession,
    base_url: str,
    headers: dict,
    metadata: dict,
    reuse_threads: bool,
) -> tuple[str, bool]:
    chat_id = str(metadata.get("chat_id") or "").strip()
    user_id = str(metadata.get("user_id") or "anon").strip() or "anon"
    if not chat_id or not reuse_threads:
        return await _create_thread(session, base_url, headers, metadata), False

    cache_key = f"{user_id}:{chat_id}"

    async with _THREAD_MAP_LOCK:
        cached = _THREAD_MAP.get(cache_key)
        if cached:
            return cached, True

    thread_id = await _create_thread(session, base_url, headers, metadata)

    async with _THREAD_MAP_LOCK:
        _THREAD_MAP[cache_key] = thread_id

    return thread_id, False


async def create_deerflow_research_stream_response(
    request,
    form_data: dict,
    metadata: dict,
    model: dict,
) -> StreamingResponse:
    base_url = str(request.app.state.config.DEERFLOW_BASE_URL or "").strip().rstrip("/")
    api_key = str(request.app.state.config.DEERFLOW_API_KEY or "").strip()
    model_name = (
        str(request.app.state.config.DEERFLOW_MODEL or "").strip()
        or _normalize_model_id(form_data.get("model"), model.get("id", ""))
    )
    reuse_threads = bool(request.app.state.config.DEERFLOW_REUSE_THREADS)
    connect_timeout = max(
        1, int(request.app.state.config.DEERFLOW_CONNECT_TIMEOUT_SECS or 10)
    )
    request_timeout = max(
        5, int(request.app.state.config.DEERFLOW_REQUEST_TIMEOUT_SECS or 900)
    )

    if not base_url:
        raise RuntimeError(
            "Deep Research is enabled but DEERFLOW_BASE_URL is not configured."
        )

    deerflow_messages = _messages_to_deerflow(form_data.get("messages", []))
    if not deerflow_messages:
        raise RuntimeError("No valid message content was found for deep research.")

    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    timeout = aiohttp.ClientTimeout(
        total=None,
        connect=connect_timeout,
        sock_connect=connect_timeout,
        sock_read=request_timeout,
    )

    async def stream():
        emitted_content = False
        last_values_payload = None
        emitted_statuses: set[str] = set()

        try:
            yield _sse_data(
                {
                    "event": {
                        "type": "status",
                        "data": {
                            "action": "deep_research",
                            "description": "Starting DeerFlow deep research",
                            "done": False,
                        },
                    }
                }
            )

            async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
                thread_id, reused = await _resolve_thread_id(
                    session=session,
                    base_url=base_url,
                    headers=headers,
                    metadata=metadata,
                    reuse_threads=reuse_threads,
                )

                yield _sse_data(
                    {
                        "event": {
                            "type": "status",
                            "data": {
                                "action": "deep_research",
                                "description": (
                                    "Reusing DeerFlow research thread"
                                    if reused
                                    else "Created DeerFlow research thread"
                                ),
                                "done": False,
                            },
                        }
                    }
                )

                run_payload: dict[str, Any] = {
                    "input": {"messages": deerflow_messages},
                    "stream_mode": ["messages", "values"],
                }

                if model_name:
                    run_payload["config"] = {
                        "configurable": {
                            "model_name": model_name,
                        }
                    }

                stream_url = f"{base_url}/api/langgraph/threads/{thread_id}/runs/stream"
                async with session.post(
                    stream_url,
                    headers=headers,
                    json=run_payload,
                    ssl=AIOHTTP_CLIENT_SESSION_SSL,
                ) as response:
                    if response.status >= 400:
                        error_body = (await response.text()).strip()
                        raise RuntimeError(
                            f"DeerFlow stream failed HTTP {response.status}: {error_body[:800]}"
                        )

                    async for event_name, data_str in _iter_sse_events(response):
                        if not data_str:
                            continue
                        if data_str == "[DONE]":
                            break
                        if (event_name or "").strip().lower() == "end":
                            break

                        parsed: Any = data_str
                        is_json = False
                        try:
                            parsed = json.loads(data_str)
                            is_json = True
                        except Exception:
                            is_json = False

                        if is_json and (event_name or "").strip().lower() == "values":
                            last_values_payload = parsed

                        status_description = _infer_status_description(event_name, parsed)
                        if status_description and status_description not in emitted_statuses:
                            emitted_statuses.add(status_description)
                            yield _sse_data(
                                {
                                    "event": {
                                        "type": "status",
                                        "data": {
                                            "action": "deep_research",
                                            "description": status_description,
                                            "done": False,
                                        },
                                    }
                                }
                            )

                        chunks = _extract_stream_chunks(parsed if is_json else data_str)
                        for chunk in chunks:
                            text = str(chunk)
                            if not text:
                                continue

                            emitted_content = True
                            yield _sse_data(
                                {
                                    "choices": [
                                        {
                                            "delta": {
                                                "content": text,
                                            }
                                        }
                                    ]
                                }
                            )

            if not emitted_content and last_values_payload is not None:
                fallback_text = _extract_final_assistant_text(last_values_payload)
                if fallback_text:
                    emitted_content = True
                    yield _sse_data(
                        {
                            "choices": [
                                {
                                    "delta": {
                                        "content": fallback_text,
                                    }
                                }
                            ]
                        }
                    )

            if not emitted_content:
                yield _sse_data(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "content": "DeerFlow finished with no textual output.",
                                }
                            }
                        ]
                    }
                )

            yield _sse_data(
                {
                    "event": {
                        "type": "status",
                        "data": {
                            "action": "deep_research",
                            "description": "Deep research completed",
                            "done": True,
                        },
                    }
                }
            )
        except asyncio.CancelledError:
            yield _sse_data(
                {
                    "event": {
                        "type": "status",
                        "data": {
                            "action": "deep_research",
                            "description": "Deep research cancelled",
                            "done": True,
                            "error": True,
                        },
                    }
                }
            )
            raise
        except Exception as exc:
            log.exception(f"DeerFlow deep research failed: {exc}")
            yield _sse_data(
                {
                    "event": {
                        "type": "status",
                        "data": {
                            "action": "deep_research",
                            "description": "Deep research failed",
                            "done": True,
                            "error": True,
                        },
                    }
                }
            )
            yield _sse_data({"error": {"message": f"DeerFlow error: {exc}"}})

    return StreamingResponse(stream(), media_type="text/event-stream")
