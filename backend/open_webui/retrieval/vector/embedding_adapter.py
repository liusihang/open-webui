from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

import aiohttp

from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL, AIOHTTP_CLIENT_TIMEOUT
from open_webui.utils.headers import include_user_info_headers

_DEFAULT_IMAGE_PROMPT = "Represent the given image for retrieval."
_UNSAFE_EXTERNAL_IMAGE_FIELDS = {
    "base64",
    "bytes",
    "data_url",
    "image_url",
    "path",
    "source_url",
    "storage_uri",
    "url",
}


class OpenAICompatibleMultimodalEvidenceEmbeddingAdapter:
    """Evidence-only embedding adapter for OpenAI-compatible multimodal models."""

    def __init__(
        self,
        *,
        text_embedding_function: Callable[..., Awaitable[Any]],
        model: str,
        url: str,
        key: str = "",
        dimensions: int | None = None,
        image_prompt: str = _DEFAULT_IMAGE_PROMPT,
        post_json: Callable[..., Awaitable[Mapping[str, Any]]] | None = None,
    ) -> None:
        self._text_embedding_function = text_embedding_function
        self._model = model
        self._url = url.rstrip("/")
        self._key = key
        self._dimensions = int(dimensions) if dimensions is not None else None
        self._image_prompt = image_prompt.strip() or _DEFAULT_IMAGE_PROMPT
        self._post_json = post_json or self._post_embeddings_json

    async def __call__(self, query: Any, prefix: str | None = None, user: Any = None) -> Any:
        if not self._has_image_payload(query):
            return await self._text_embedding_function(query, prefix=prefix, user=user)

        payload = self._build_image_embedding_payload(query)
        response = await self._post_json(
            url=f"{self._url}/embeddings",
            headers=self._build_headers(user),
            payload=payload,
        )
        vectors = self._extract_vectors(response)
        if len(vectors) != 1:
            raise ValueError(f"expected exactly one multimodal embedding vector, got {len(vectors)}")
        return vectors[0]

    @staticmethod
    def _has_image_payload(query: Any) -> bool:
        if not isinstance(query, Mapping):
            return False
        if "image_bytes" in query:
            return True
        images = query.get("query_images")
        return isinstance(images, Sequence) and not isinstance(images, (str, bytes)) and bool(images)

    def _build_headers(self, user: Any = None) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        if user:
            headers = include_user_info_headers(headers, user)
        return headers

    async def _post_embeddings_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        async with aiohttp.ClientSession(
            trust_env=True,
            timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT),
        ) as session:
            async with session.post(
                url,
                headers=dict(headers),
                json=dict(payload),
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as response:
                response.raise_for_status()
                data = await response.json()
        if not isinstance(data, Mapping):
            raise ValueError("Unexpected embeddings response: expected JSON object")
        return data

    def _build_image_embedding_payload(self, query: Mapping[str, Any]) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        for image in self._extract_images(query):
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": self._image_to_data_url(image)},
                }
            )

        content.append({"type": "text", "text": self._extract_prompt(query)})
        payload: dict[str, Any] = {
            "model": self._model,
            "encoding_format": "float",
            "messages": [{"role": "user", "content": content}],
        }
        if self._dimensions is not None and self._dimensions > 0:
            payload["dimensions"] = self._dimensions
        return payload

    def _extract_images(self, query: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        if query.get("image_bytes"):
            return [query]

        images = query.get("query_images")
        if not isinstance(images, Sequence) or isinstance(images, (str, bytes)) or not images:
            raise ValueError("multimodal embedding input is missing query_images")
        normalized = []
        for image in images:
            if not isinstance(image, Mapping):
                raise ValueError("query_images entries must be objects")
            normalized.append(image)
        return normalized

    def _image_to_data_url(self, image: Mapping[str, Any]) -> str:
        unsafe = sorted(
            field
            for field in _UNSAFE_EXTERNAL_IMAGE_FIELDS
            if field in image and image.get(field)
        )
        if unsafe:
            raise ValueError(
                "multimodal evidence embedding only accepts resolved stored image bytes; "
                f"unsafe image fields: {', '.join(unsafe)}"
            )

        image_bytes = image.get("image_bytes")
        if not isinstance(image_bytes, (bytes, bytearray)):
            raise ValueError("multimodal evidence embedding image is missing image_bytes")

        mime_type = str(image.get("mime_type") or "").split(";")[0].strip().lower()
        if not mime_type.startswith("image/"):
            raise ValueError("multimodal evidence embedding image is missing a safe image MIME type")

        encoded = base64.b64encode(bytes(image_bytes)).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    def _extract_prompt(self, query: Mapping[str, Any]) -> str:
        for key in ("query_text", "content_text", "preview_text", "title", "source_name"):
            value = query.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return self._image_prompt

    @staticmethod
    def _extract_vectors(response: Mapping[str, Any]) -> list[list[float]]:
        data = response.get("data")
        if not isinstance(data, list):
            raise ValueError("Unexpected embeddings response: missing 'data' list")

        vectors: list[list[float]] = []
        for item in data:
            if not isinstance(item, Mapping) or "embedding" not in item:
                raise ValueError("Unexpected embeddings response: missing embedding item")
            embedding = item["embedding"]
            if not isinstance(embedding, list):
                raise ValueError("Unexpected embeddings response: embedding is not a list")
            vectors.append(embedding)
        return vectors


def get_evidence_retrieval_embedding_function(
    *,
    embedding_engine: str,
    embedding_model: str,
    text_embedding_function: Callable[..., Awaitable[Any]],
    url: str,
    key: str = "",
) -> Callable[..., Awaitable[Any]]:
    if embedding_engine != "openai":
        return text_embedding_function

    return OpenAICompatibleMultimodalEvidenceEmbeddingAdapter(
        text_embedding_function=text_embedding_function,
        model=embedding_model,
        url=url,
        key=key,
    )
