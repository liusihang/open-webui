"""Evidence-only multimodal encoding; text uses the existing embedding helper unchanged."""

import base64
import io
import math
import re
from numbers import Real

import aiohttp
from PIL import Image

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
IMAGE_MIME_TYPES = {'image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/bmp', 'image/tiff'}


def validate_vector(vector, dimensions: int):
    """Reject before official pgvector can silently pad/truncate the query."""
    if not isinstance(vector, (list, tuple)) or len(vector) != dimensions:
        raise ValueError(f'expected exactly {dimensions} embedding dimensions')
    if any(isinstance(v, bool) or not isinstance(v, Real) or not math.isfinite(v) for v in vector):
        raise ValueError('embedding must contain finite real numbers')
    if not any(vector):
        raise ValueError('zero embedding cannot be used with cosine distance')
    return [float(v) for v in vector]


def validate_image(image_bytes, mime_type):
    if not isinstance(image_bytes, bytes) or not 0 < len(image_bytes) <= MAX_IMAGE_BYTES:
        raise ValueError('image must contain 1 byte to 20 MiB')
    if mime_type not in IMAGE_MIME_TYPES:
        raise ValueError('unsupported image MIME type')
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            actual_mime = Image.MIME.get(image.format)
            if actual_mime != mime_type or image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValueError('image MIME mismatch or excessive pixel count')
            image.verify()
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError('invalid image') from exc
    return image_bytes


class EvidenceEmbeddingAdapter:
    def __init__(
        self, *, text_embedding_function, model, url, key='', config_fingerprint, dimensions=2048, post_json=None
    ):
        self.text_embedding_function = text_embedding_function
        self.model = model
        self.url = url.rstrip('/')
        self.key = key
        self.config_fingerprint = config_fingerprint
        self.dimensions = dimensions
        self.post_json = post_json or self._post

    def validate_space(self, space, *, image=False, document=False):
        if (
            space.verification_status != 'verified'
            or not space.active
            or space.config_fingerprint != self.config_fingerprint
            or space.embedding_model != self.model
            or space.embedding_dim != self.dimensions
            or space.distance_metric != 'cosine'
        ):
            raise ValueError('embedding space is unverified or incompatible with this encoder')
        capability = ('image' if image else 'text') + ('_evidence' if document else '_query')
        if not getattr(space, 'supports_' + capability):
            raise ValueError(f'encoding space does not support {capability}')

    async def encode(self, *, space, query_text='', image_bytes=None, mime_type=None, user=None, document=False):
        self.validate_space(space, image=image_bytes is not None, document=document)
        if image_bytes is None:
            if not isinstance(query_text, str) or not query_text.strip():
                raise ValueError('text query is empty')
            # Preserve existing query/document prefix settings recorded in the fingerprint.
            prefix = (space.encoding_config or {}).get('document_prefix' if document else 'query_prefix')
            vector = await self.text_embedding_function(query_text, prefix=prefix, user=user)
        else:
            validate_image(image_bytes, mime_type)
            content = [
                {
                    'type': 'image_url',
                    'image_url': {'url': f'data:{mime_type};base64,' + base64.b64encode(image_bytes).decode('ascii')},
                }
            ]
            prompt = query_text.strip() or (space.encoding_config or {}).get(
                'image_prompt', 'Represent the given image for retrieval.'
            )
            content.append({'type': 'text', 'text': prompt})
            # Same messages protocol used by the Live Qwen adapter; no URL fetching or OCR substitute.
            payload = {
                'model': self.model,
                'encoding_format': 'float',
                'messages': [{'role': 'user', 'content': content}],
            }
            headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
            if self.key:
                headers['Authorization'] = 'Bearer ' + self.key
            response = await self.post_json(url=self.url + '/embeddings', headers=headers, payload=payload)
            rows = response.get('data') if isinstance(response, dict) else None
            if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
                raise ValueError('expected exactly one image embedding')
            vector = rows[0].get('embedding')
        return validate_vector(vector, self.dimensions)

    async def _post(self, *, url, headers, payload):
        from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL, AIOHTTP_CLIENT_TIMEOUT

        async with aiohttp.ClientSession(
            trust_env=True, timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT)
        ) as session:
            async with session.post(url, headers=headers, json=payload, ssl=AIOHTTP_CLIENT_SESSION_SSL) as response:
                response.raise_for_status()
                return await response.json()


class LegacyReadEmbeddingAdapter(EvidenceEmbeddingAdapter):
    """Explicit raw-v1 query compatibility, never an index-writing encoder.

    These vectors have observed compatibility, not a verified weights revision.
    A separately fingerprinted, server-registered profile binds source metadata
    and bounded comparison evidence without relabelling old vectors as verified.
    """

    def validate_space(self, space, *, image=False, document=False):
        config = space.encoding_config or {}
        if (
            document
            or space.verification_status != 'legacy_read_only'
            or config.get('protocol') != 'legacy_raw_v1'
            or config.get('engine') != 'openai'
            or config.get('model') != self.model
            or space.model_revision is not None
            or config.get('text_api_base_url') != self.url
            or config.get('query_prefix') is not None
            or not isinstance(config.get('source_metadata_hashes'), list)
            or not config.get('source_metadata_hashes')
            or any(not isinstance(h, str) or not re.fullmatch('[0-9a-f]{64}', h)
                   for h in config.get('source_metadata_hashes', []))
            or not re.fullmatch('[0-9a-f]{64}', str(config.get('compatibility_evidence_sha256') or ''))
            or not space.active
            or space.config_fingerprint != self.config_fingerprint
            or space.embedding_model != self.model
            or space.embedding_dim != self.dimensions
            or space.distance_metric != 'cosine'
        ):
            raise ValueError('legacy profile is unavailable, incompatible or read-only')
        if not getattr(space, 'supports_' + ('image_query' if image else 'text_query')):
            raise ValueError('legacy profile does not support this query modality')


class QueryEmbeddingRouter:
    """Select a query encoder by exact space identity; no global encoder fallback."""

    def __init__(self, adapters):
        self.adapters = dict(adapters)

    def _adapter(self, space):
        adapter = self.adapters.get(space.id)
        if adapter is None:
            raise ValueError('no compatible query encoder for this space')
        return adapter

    def validate_space(self, space, *, image=False, document=False):
        if document:
            raise ValueError('query router cannot index documents')
        return self._adapter(space).validate_space(space, image=image)

    async def encode(self, *, space, document=False, **kwargs):
        self.validate_space(space, image=kwargs.get('image_bytes') is not None, document=document)
        return await self._adapter(space).encode(space=space, **kwargs)
