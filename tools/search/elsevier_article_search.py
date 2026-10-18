"""
title: Elsevier Article Search
author: shuofang
version: 0.9.8
license: MIT
required_open_webui_version: 0.7.0
description: Use this tool to search and retrieve academic literature from Elsevier (Scopus & ScienceDirect). Trigger this tool when the user asks to find research papers, authors, abstracts, or Elsevier full-text articles. Core capabilities: 1. Article Search (official API recall, optional bounded query expansion, optional rerank, canonical DOI output, and article-level RAG where one article is one chunk); 2. Author Search; 3. Abstract Retrieval; 4. Full-Text Retrieval through the official Elsevier API, followed by article-level RAG with embedding and reranking. Recommended flow: send 2-3 intent-level queries to search_articles, let this tool merge + dedupe + article-RAG + rerank, then pass a selected Elsevier DOI or ScienceDirect PII into get_article_fulltext.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
import uuid
import xml.etree.ElementTree as ET
from collections import Counter, OrderedDict
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple
from urllib import error, parse, request

from pydantic import BaseModel, Field


class _ElsevierRedirectHandler(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = parse.urlsplit(newurl)
        if (
            target.scheme != 'https'
            or target.hostname != 'api.elsevier.com'
            or target.port not in (None, 443)
            or target.username is not None
            or target.password is not None
        ):
            raise RuntimeError('Refusing Elsevier API redirect outside api.elsevier.com.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Tools:
    class Valves(BaseModel):
        ELSEVIER_API_KEY: str = Field(
            default='',
            description='Elsevier API Key. Register at https://dev.elsevier.com to obtain one.',
        )
        ELSEVIER_INST_TOKEN: str = Field(
            default='',
            description='(Optional) Institutional token for subscriber / full-text access.',
        )
        ELSEVIER_AUTH_TOKEN: str = Field(
            default='',
            description="(Optional) End-user session token. If it starts with 'Bearer ', it will be sent as an Authorization header.",
        )
        DEFAULT_DATABASE: str = Field(
            default='both',
            description="Default search database: 'scopus', 'sciencedirect', or 'both'.",
        )
        DEFAULT_ABSTRACT_VIEW: str = Field(
            default='META_ABS',
            description='Default Abstract Retrieval view. Common values: META, META_ABS, REF, ENTITLED.',
        )
        MAX_RESULTS: int = Field(
            default=10,
            description='Maximum number of results to return per search.',
        )
        MAX_ENRICH_RESULTS: int = Field(
            default=5,
            description='Default number of top search results to enrich with abstract/access metadata.',
        )
        REQUEST_CACHE_SIZE: int = Field(
            default=256,
            description='Number of successful API responses to keep in the in-memory cache.',
        )
        TIMEOUT_SECONDS: float = Field(
            default=20.0,
            description='HTTP timeout for Elsevier API requests.',
        )
        HTTP_PROXY: str = Field(
            default='',
            description='Optional HTTP(S) proxy for api.elsevier.com only. Empty means direct (no environment proxy). Internal embedding and rerank calls remain direct.',
        )
        FULLTEXT_DEFAULT_MODE: str = Field(
            default='rag',
            description="Default full-text mode: 'rag' or 'preview'.",
        )
        FULLTEXT_PREVIEW_CHAR_LIMIT: int = Field(
            default=50000,
            description='Character limit applied to preview mode full text.',
        )
        SEARCH_QUERY_EXPANSION_ENABLED: bool = Field(
            default=False,
            description='If true, search_articles may issue a small number of query variants for better recall. Keep false by default; prefer the calling AI to craft alternate queries explicitly.',
        )
        SEARCH_QUERY_EXPANSION_VARIANTS: int = Field(
            default=3,
            description='Max query variants per source in search_articles (including the original query).',
        )
        SEARCH_RERANK_ENABLED: bool = Field(
            default=False,
            description='If true, rerank merged search candidates with the configured rerank model.',
        )
        SEARCH_RERANK_CANDIDATES: int = Field(
            default=20,
            description='How many top merged search candidates to rerank when SEARCH_RERANK_ENABLED is true.',
        )
        RAG_TOP_K: int = Field(
            default=8,
            description='Default number of evidence chunks returned in RAG mode.',
        )
        RAG_CHUNK_SIZE: int = Field(
            default=1200,
            description='Target chunk size (characters) for full-text RAG indexing.',
        )
        RAG_CHUNK_OVERLAP: int = Field(
            default=200,
            description='Chunk overlap (characters) for full-text RAG indexing.',
        )
        RAG_BM25_CANDIDATES: int = Field(
            default=24,
            description='How many BM25 candidates to include before fusion.',
        )
        RAG_DENSE_CANDIDATES: int = Field(
            default=24,
            description='How many dense-vector candidates to include before fusion.',
        )
        RAG_RRF_K: int = Field(
            default=60,
            description='RRF constant k used to fuse BM25 and dense rankings.',
        )
        RAG_INDEX_CACHE_SIZE: int = Field(
            default=16,
            description='Max number of in-memory full-text RAG indexes to keep.',
        )
        RAG_KEEP_METHODS_GRANULAR: bool = Field(
            default=True,
            description='If true, keep Methods subsections for better RAG recall; if false, collapse methods into summary.',
        )
        EMBEDDING_API_BASE: str = Field(
            default='',
            description="OpenAI-compatible embeddings base URL, e.g. 'http://127.0.0.1:11434/v1'.",
        )
        EMBEDDING_API_KEY: str = Field(
            default='',
            description='(Optional) API key for embedding endpoint.',
        )
        EMBEDDING_MODEL: str = Field(
            default='',
            description='Embedding model name used in RAG mode.',
        )
        EMBEDDING_BATCH_SIZE: int = Field(
            default=16,
            description='Batch size for embedding API calls when indexing article chunks.',
        )
        RERANK_API_URL: str = Field(
            default='',
            description='OpenAI-compatible or common rerank API URL. Request body uses model, query, documents.',
        )
        RERANK_API_KEY: str = Field(
            default='',
            description='(Optional) API key for rerank endpoint.',
        )
        RERANK_MODEL: str = Field(
            default='',
            description='Rerank model name used for final chunk reordering.',
        )
        RERANK_ENABLED: bool = Field(
            default=False,
            description='If true, rerank the fused RRF candidates before returning final evidence chunks.',
        )
        RERANK_CANDIDATES: int = Field(
            default=12,
            description='How many top RRF candidates to send to the rerank model.',
        )

    def __init__(self) -> None:
        self.valves = self.Valves()
        self.citation = True
        self._response_cache: 'OrderedDict[str, bytes]' = OrderedDict()
        self._cache_lock = Lock()
        self._fulltext_rag_cache: 'OrderedDict[str, Dict[str, Any]]' = OrderedDict()
        self._fulltext_rag_cache_lock = Lock()

    # ------------------------------------------------------------------
    # HTTP / transport helpers
    # ------------------------------------------------------------------

    def _headers(self, accept: str = 'application/json', content_type: Optional[str] = None) -> Dict[str, str]:
        headers = {
            'Accept': accept,
            'X-ELS-APIKey': self.valves.ELSEVIER_API_KEY.strip(),
            'User-Agent': 'OpenWebUI-Elsevier-Tool/0.9.7',
            'X-ELS-ReqId': uuid.uuid4().hex,
        }

        inst_token = self.valves.ELSEVIER_INST_TOKEN.strip()
        if inst_token:
            headers['X-ELS-Insttoken'] = inst_token

        auth_token = self.valves.ELSEVIER_AUTH_TOKEN.strip()
        if auth_token:
            if auth_token.lower().startswith('bearer '):
                headers['Authorization'] = auth_token
            else:
                headers['X-ELS-Authtoken'] = auth_token

        if content_type:
            headers['Content-Type'] = content_type
        return headers

    def _build_opener(self, *, use_proxy: bool = False) -> request.OpenerDirector:
        proxy_url = self.valves.HTTP_PROXY.strip()
        handlers = []
        if use_proxy and proxy_url:
            handlers.append(
                request.ProxyHandler(
                    {
                        'http': proxy_url,
                        'https': proxy_url,
                    }
                )
            )
        else:
            handlers.append(request.ProxyHandler({}))
        # Do not inherit host/container proxy variables for internal services,
        # or silently use another egress when the configured proxy is empty.
        if use_proxy:
            handlers.append(_ElsevierRedirectHandler())
        return request.build_opener(*handlers)

    def _cache_key(
        self,
        url: str,
        method: str,
        accept: str,
        content_type: Optional[str],
        payload_bytes: Optional[bytes],
    ) -> str:
        payload_hash = ''
        if payload_bytes is not None:
            payload_hash = hashlib.sha256(payload_bytes).hexdigest()
        basis = (
            f'{method.upper()}::{accept}::{content_type or ""}::{self._cache_auth_fingerprint()}::{url}::{payload_hash}'
        )
        return hashlib.sha256(basis.encode('utf-8', errors='ignore')).hexdigest()

    def _cache_auth_fingerprint(self) -> str:
        # Keep response cache scoped to the credentials/proxy that determine entitlement.
        auth_scope = {
            'api_key': self.valves.ELSEVIER_API_KEY.strip(),
            'inst_token': self.valves.ELSEVIER_INST_TOKEN.strip(),
            'auth_token': self.valves.ELSEVIER_AUTH_TOKEN.strip(),
            'http_proxy': self.valves.HTTP_PROXY.strip(),
        }
        encoded = json.dumps(auth_scope, sort_keys=True).encode('utf-8', errors='ignore')
        return hashlib.sha256(encoded).hexdigest()

    def _cache_get(self, key: str) -> Optional[bytes]:
        with self._cache_lock:
            cached = self._response_cache.get(key)
            if cached is None:
                return None
            self._response_cache.move_to_end(key)
            return cached

    def _cache_set(self, key: str, value: bytes) -> None:
        max_size = max(0, int(self.valves.REQUEST_CACHE_SIZE))
        if max_size <= 0:
            return
        with self._cache_lock:
            self._response_cache[key] = value
            self._response_cache.move_to_end(key)
            while len(self._response_cache) > max_size:
                self._response_cache.popitem(last=False)

    def _prepare_payload_bytes(self, data: Any) -> Tuple[Optional[bytes], Optional[str]]:
        if data is None:
            return None, None
        if isinstance(data, bytes):
            return data, None
        if isinstance(data, str):
            return data.encode('utf-8'), None
        return json.dumps(data, ensure_ascii=False).encode('utf-8'), 'application/json'

    def _request_bytes(
        self,
        url: str,
        *,
        method: str = 'GET',
        accept: str = 'application/json',
        data: Any = None,
        content_type: Optional[str] = None,
        use_cache: bool = True,
    ) -> bytes:
        parsed_url = parse.urlsplit(url)
        if (
            parsed_url.scheme != 'https'
            or parsed_url.hostname != 'api.elsevier.com'
            or parsed_url.port not in (None, 443)
            or parsed_url.username is not None
            or parsed_url.password is not None
        ):
            raise ValueError('Elsevier API requests require https://api.elsevier.com.')
        payload_bytes, inferred_content_type = self._prepare_payload_bytes(data)
        actual_content_type = content_type or inferred_content_type
        cache_key = self._cache_key(url, method, accept, actual_content_type, payload_bytes)
        if use_cache:
            cached = self._cache_get(cache_key)
            if cached is not None:
                return cached

        # Only Elsevier API traffic should traverse the campus proxy.
        opener = self._build_opener(use_proxy=True)
        http_request = request.Request(
            url,
            data=payload_bytes,
            headers=self._headers(accept=accept, content_type=actual_content_type),
            method=method.upper(),
        )

        try:
            with opener.open(http_request, timeout=self.valves.TIMEOUT_SECONDS) as resp:
                raw = resp.read()
        except error.HTTPError as exc:
            detail = exc.read(4096).decode('utf-8', errors='ignore').strip()[:1200]
            raise RuntimeError(f'Elsevier API HTTP {exc.code}: {detail or exc.reason}') from exc
        except error.URLError as exc:
            raise RuntimeError(f'Unable to reach Elsevier API: {getattr(exc, "reason", exc)}') from exc

        if use_cache:
            self._cache_set(cache_key, raw)
        return raw

    def _fetch_json(
        self,
        url: str,
        *,
        method: str = 'GET',
        data: Any = None,
        accept: str = 'application/json',
        content_type: Optional[str] = None,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        raw = self._request_bytes(
            url,
            method=method,
            accept=accept,
            data=data,
            content_type=content_type,
            use_cache=use_cache,
        )
        try:
            return json.loads(raw.decode('utf-8', errors='replace'))
        except json.JSONDecodeError as exc:
            raise RuntimeError('Elsevier API returned invalid JSON.') from exc

    def _fetch_text(
        self,
        url: str,
        *,
        method: str = 'GET',
        data: Any = None,
        accept: str = 'text/plain',
        content_type: Optional[str] = None,
        use_cache: bool = True,
    ) -> str:
        raw = self._request_bytes(
            url,
            method=method,
            accept=accept,
            data=data,
            content_type=content_type,
            use_cache=use_cache,
        )
        return raw.decode('utf-8', errors='replace')

    def _build_url(self, base_url: str, params: Dict[str, Any]) -> str:
        clean_params: Dict[str, Any] = {}
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            clean_params[key] = value
        if not clean_params:
            return base_url
        return f'{base_url}?{parse.urlencode(clean_params, doseq=True)}'

    def _embedding_headers(self) -> Dict[str, str]:
        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'User-Agent': 'OpenWebUI-Elsevier-Tool/0.6.0',
        }
        api_key = self.valves.EMBEDDING_API_KEY.strip()
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'
        return headers

    def _embedding_endpoint(self) -> str:
        base = self.valves.EMBEDDING_API_BASE.strip().rstrip('/')
        if not base:
            raise RuntimeError('EMBEDDING_API_BASE is not configured.')
        if base.endswith('/embeddings'):
            return base
        return f'{base}/embeddings'

    def _rerank_headers(self) -> Dict[str, str]:
        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'User-Agent': 'OpenWebUI-Elsevier-Tool/0.6.0',
        }
        api_key = self.valves.RERANK_API_KEY.strip()
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'
        return headers

    def _rerank_endpoint(self) -> str:
        endpoint = self.valves.RERANK_API_URL.strip()
        if not endpoint:
            raise RuntimeError('RERANK_API_URL is not configured.')
        return endpoint

    def _rerank_documents(self, query: str, documents: List[str]) -> List[Dict[str, Any]]:
        if not documents:
            return []

        model = self.valves.RERANK_MODEL.strip()
        if not model:
            raise RuntimeError('RERANK_MODEL is not configured.')

        endpoint = self._rerank_endpoint()
        payload = {
            'model': model,
            'query': query,
            'documents': documents,
        }

        opener = self._build_opener()
        req = request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers=self._rerank_headers(),
            method='POST',
        )
        try:
            with opener.open(req, timeout=self.valves.TIMEOUT_SECONDS) as resp:
                raw = resp.read()
        except error.HTTPError as exc:
            detail = exc.read().decode('utf-8', errors='ignore').strip()
            raise RuntimeError(f'Rerank API HTTP {exc.code}: {detail or exc.reason}') from exc
        except error.URLError as exc:
            raise RuntimeError(f'Unable to reach rerank API: {getattr(exc, "reason", exc)}') from exc

        try:
            parsed = json.loads(raw.decode('utf-8', errors='replace'))
        except json.JSONDecodeError as exc:
            raise RuntimeError('Rerank API returned invalid JSON.') from exc

        results = parsed.get('data')
        if not isinstance(results, list):
            results = parsed.get('results')
        if not isinstance(results, list):
            raise RuntimeError("Rerank API response schema is unsupported. Expected 'data[]' or 'results[]'.")

        normalized: List[Dict[str, Any]] = []
        for item in results:
            if not isinstance(item, dict):
                continue

            raw_index = item.get('index')
            if raw_index is None and isinstance(item.get('document'), dict):
                raw_index = item['document'].get('index')
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                raise RuntimeError("Rerank API response is missing a valid 'index' for at least one item.")

            raw_score = item.get('relevance_score')
            if raw_score is None:
                raw_score = item.get('score')
            if raw_score is None:
                raise RuntimeError("Rerank API response is missing 'relevance_score' for at least one item.")
            try:
                relevance_score = float(raw_score)
            except (TypeError, ValueError):
                raise RuntimeError('Rerank API returned a non-numeric relevance score.')

            if index < 0 or index >= len(documents):
                raise RuntimeError(f'Rerank API returned out-of-range index {index} for {len(documents)} documents.')

            normalized.append(
                {
                    'index': index,
                    'relevance_score': relevance_score,
                }
            )

        if not normalized:
            raise RuntimeError('Rerank API returned no usable results.')

        normalized.sort(key=lambda item: item['relevance_score'], reverse=True)
        return normalized

    def _embed_texts(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        model = self.valves.EMBEDDING_MODEL.strip()
        if not model:
            raise RuntimeError('EMBEDDING_MODEL is not configured.')

        endpoint = self._embedding_endpoint()
        opener = self._build_opener()
        batch_size = max(1, int(self.valves.EMBEDDING_BATCH_SIZE))
        all_vectors: List[List[float]] = []

        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            payload = {
                'model': model,
                'input': batch,
            }
            req = request.Request(
                endpoint,
                data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                headers=self._embedding_headers(),
                method='POST',
            )
            try:
                with opener.open(req, timeout=self.valves.TIMEOUT_SECONDS) as resp:
                    raw = resp.read()
            except error.HTTPError as exc:
                detail = exc.read().decode('utf-8', errors='ignore').strip()
                raise RuntimeError(f'Embedding API HTTP {exc.code}: {detail or exc.reason}') from exc
            except error.URLError as exc:
                raise RuntimeError(f'Unable to reach embedding API: {getattr(exc, "reason", exc)}') from exc

            try:
                parsed = json.loads(raw.decode('utf-8', errors='replace'))
            except json.JSONDecodeError as exc:
                raise RuntimeError('Embedding API returned invalid JSON.') from exc

            data = parsed.get('data')
            if isinstance(data, list) and data:
                sorted_data = sorted(
                    data,
                    key=lambda item: int(item.get('index', 0)) if isinstance(item, dict) else 0,
                )
                for item in sorted_data:
                    if not isinstance(item, dict) or 'embedding' not in item:
                        raise RuntimeError("Embedding API response is missing 'embedding' vectors.")
                    vector = item.get('embedding') or []
                    if not isinstance(vector, list) or not vector:
                        raise RuntimeError('Embedding API returned empty vector.')
                    all_vectors.append([float(v) for v in vector])
                continue

            # Compatibility fallback: some providers return {"embedding": [...]}
            single_vector = parsed.get('embedding')
            if isinstance(single_vector, list) and single_vector:
                if len(batch) != 1:
                    raise RuntimeError('Embedding API returned single vector for batched input.')
                all_vectors.append([float(v) for v in single_vector])
                continue

            raise RuntimeError(
                "Embedding API response schema is unsupported. Expected OpenAI-compatible 'data[].embedding'."
            )

        if len(all_vectors) != len(texts):
            raise RuntimeError('Embedding API vector count mismatch.')
        return all_vectors

    # ------------------------------------------------------------------
    # Event / display helpers
    # ------------------------------------------------------------------

    async def _emit_status(self, emitter, description: str, done: bool) -> None:
        if emitter is None:
            return
        await emitter({'type': 'status', 'data': {'description': description, 'done': done}})

    async def _emit_citations(self, emitter, results: List[Dict[str, Any]]) -> None:
        if emitter is None:
            return
        for result in results:
            url = result.get('url', '')
            title = result.get('title', 'Article')
            snippet = result.get('abstract') or result.get('snippet') or title
            if not url:
                continue
            await emitter(
                {
                    'type': 'citation',
                    'data': {
                        'source': {'name': title, 'id': url, 'url': url},
                        'document': [self._truncate_text(str(snippet), 500)],
                        'metadata': [{'source': url, 'name': title}],
                    },
                }
            )

    # ------------------------------------------------------------------
    # Generic parsing helpers
    # ------------------------------------------------------------------

    def _normalize_text(self, text: str) -> str:
        text = text.replace('\xa0', ' ')
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    def _normalize_block_text(self, text: str) -> str:
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        text = text.replace('\xa0', ' ')
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def _normalize_doi(self, value: Any) -> str:
        text = self._normalize_text(str(value or ''))
        if not text:
            return ''
        text = re.sub(r'^(?:doi\s*:\s*)', '', text, flags=re.IGNORECASE)
        text = re.sub(r'^https?://(?:dx\.)?doi\.org/', '', text, flags=re.IGNORECASE)
        text = parse.unquote(text)
        return text.strip().strip('/')

    def _normalize_pii(self, value: Any) -> str:
        text = self._normalize_text(str(value or ''))
        text = re.sub(
            r'^https?://(?:www\.)?sciencedirect\.com/science/article/(?:abs/)?pii/',
            '',
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r'^pii\s*:\s*', '', text, flags=re.IGNORECASE)
        return parse.unquote(text.split('?', 1)[0].split('#', 1)[0]).strip().strip('/')

    def _truncate_text(self, text: str, limit: int = 320) -> str:
        clean = self._normalize_text(text)
        if len(clean) <= limit:
            return clean
        return clean[: max(0, limit - 1)].rstrip() + '…'

    def _strip_ns(self, tag: str) -> str:
        if not isinstance(tag, str):
            return ''
        if '}' in tag:
            tag = tag.split('}', 1)[1]
        if ':' in tag:
            tag = tag.split(':', 1)[1]
        return tag.lower()

    def _local_name(self, tag: str) -> str:
        return self._strip_ns(tag)

    def _as_list(self, value: Any) -> List[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]

    def _coerce_bool(self, value: Any) -> Optional[bool]:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, dict):
            for candidate_key in ('$', 'value', '@value', 'text'):
                if candidate_key in value:
                    return self._coerce_bool(value[candidate_key])
            return None
        if value is None:
            return None
        text = str(value).strip().lower()
        if text in {
            '1',
            'true',
            'yes',
            'y',
            'open',
            'allowed',
            'authorized',
            'entitled',
            'subscribed',
            'available',
            'permitted',
        }:
            return True
        if text in {
            '0',
            'false',
            'no',
            'n',
            'closed',
            'denied',
            'unauthorized',
            'restricted',
            'unavailable',
            'notavailable',
            'forbidden',
        }:
            return False
        return None

    def _coerce_int(self, value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    def _extract_link(self, links: Any, preferred_refs: List[str]) -> str:
        ref_order = [ref.lower() for ref in preferred_refs]
        candidates = self._as_list(links)
        for preferred in ref_order:
            for link in candidates:
                if not isinstance(link, dict):
                    continue
                ref = str(link.get('@ref', '')).lower()
                href = link.get('@href', '')
                if ref == preferred and href:
                    return href
        for link in candidates:
            if isinstance(link, dict) and link.get('@href'):
                return link['@href']
        return ''

    def _search_result_key(self, result: Dict[str, Any]) -> str:
        doi = self._normalize_doi(result.get('doi', '')).lower()
        if doi:
            return f'doi::{doi}'
        pii = str(result.get('pii', '')).strip().lower()
        if pii:
            return f'pii::{pii}'
        scopus_id = str(result.get('scopus_id', '')).strip().lower()
        if scopus_id:
            return f'scopus::{scopus_id}'
        title = self._normalize_text(str(result.get('title', ''))).lower()
        year = str(result.get('year', '') or '').strip()
        return f'title::{title}::{year}'

    def _merge_result_records(self, base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(base)

        source_databases = list(merged.get('source_databases', []))
        for db_name in incoming.get('source_databases', []):
            if db_name not in source_databases:
                source_databases.append(db_name)
        merged['source_databases'] = source_databases

        for field in [
            'title',
            'authors',
            'journal',
            'date',
            'year',
            'doi',
            'pii',
            'scopus_id',
            'eid',
            'abstract',
            'url',
            'document_type',
            'publication_type',
        ]:
            if not merged.get(field) and incoming.get(field):
                merged[field] = incoming[field]

        if not merged.get('snippet') and incoming.get('snippet'):
            merged['snippet'] = incoming['snippet']

        if merged.get('cited_by') is None:
            merged['cited_by'] = incoming.get('cited_by')
        elif incoming.get('cited_by') is not None:
            merged['cited_by'] = max(int(merged.get('cited_by') or 0), int(incoming.get('cited_by') or 0))

        open_access_values = [
            value for value in [merged.get('open_access'), incoming.get('open_access')] if value is not None
        ]
        merged['open_access'] = True if True in open_access_values else (False if open_access_values else None)

        return merged

    def _dedupe_results(self, results: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
        seen: Dict[str, Dict[str, Any]] = OrderedDict()
        for result in results:
            key = self._search_result_key(result)
            if key in seen:
                seen[key] = self._merge_result_records(seen[key], result)
            else:
                seen[key] = result
        deduped = list(seen.values())
        return deduped[:limit]

    def _search_query_variant_limit(self) -> int:
        return min(5, max(1, int(self.valves.SEARCH_QUERY_EXPANSION_VARIANTS)))

    def _normalize_explicit_search_queries(self, queries: Any, *, max_queries: int = 3) -> Tuple[List[str], bool]:
        if queries is None:
            return [], False

        raw_items: List[Any]
        if isinstance(queries, (list, tuple, set)):
            raw_items = list(queries)
        else:
            raw_items = [queries]

        normalized: List[str] = []
        seen = set()
        for raw in raw_items:
            query = self._normalize_text(str(raw or ''))
            if not query:
                continue
            key = query.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(query)

        truncated = len(normalized) > max(1, int(max_queries))
        return normalized[: max(1, int(max_queries))], truncated

    def _should_expand_search_queries(
        self,
        *,
        query: Optional[str],
        doi: Optional[str] = None,
        pii: Optional[str] = None,
        scopus_id: Optional[str] = None,
        pubmed_id: Optional[str] = None,
        title: Optional[str] = None,
        author_id: Optional[str] = None,
        issn: Optional[str] = None,
    ) -> bool:
        if not bool(self.valves.SEARCH_QUERY_EXPANSION_ENABLED):
            return False
        if not self._normalize_text(str(query or '')):
            return False
        # Keep exact identifier lookups narrow and predictable.
        exact_identifiers = [
            self._normalize_doi(doi),
            self._normalize_text(str(pii or '')),
            self._normalize_text(str(scopus_id or '')),
            self._normalize_text(str(pubmed_id or '')),
            self._normalize_text(str(title or '')),
            self._normalize_text(str(author_id or '')),
            self._normalize_text(str(issn or '')),
        ]
        return not any(exact_identifiers)

    def _expand_search_query_variants(self, query: Optional[str]) -> List[str]:
        clean_query = self._normalize_text(str(query or ''))
        if not clean_query:
            return []

        variants: List[str] = [clean_query]
        lower_query = clean_query.lower()

        if ' and ' in lower_query:
            variants.append(re.sub(r'\band\b', 'OR', clean_query, flags=re.IGNORECASE))
        if ' or ' in lower_query:
            variants.append(re.sub(r'\bor\b', 'AND', clean_query, flags=re.IGNORECASE))
        if len(clean_query.split()) >= 4:
            variants.append(f'"{clean_query}"')

        token_fallback = self._normalize_text(re.sub(r"[()\\[\\]{}\"']", ' ', clean_query))
        if token_fallback:
            variants.append(token_fallback)

        deduped: List[str] = []
        seen = set()
        for variant in variants:
            normalized_variant = self._normalize_text(variant)
            if not normalized_variant:
                continue
            key = normalized_variant.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(normalized_variant)

        return deduped[: self._search_query_variant_limit()]

    def _build_search_rerank_document(self, result: Dict[str, Any]) -> str:
        parts: List[str] = []
        title = self._normalize_text(str(result.get('title', '')))
        if title:
            parts.append(f'Title: {title}')
        abstract = self._normalize_block_text(str(result.get('abstract', '') or result.get('snippet', '')))
        if abstract:
            parts.append(f'Abstract: {abstract}')
        journal = self._normalize_text(str(result.get('journal', '')))
        if journal:
            parts.append(f'Journal: {journal}')
        authors = self._normalize_text(str(result.get('authors', '')))
        if authors:
            parts.append(f'Authors: {authors}')
        year = self._normalize_text(str(result.get('year', '')))
        if year:
            parts.append(f'Year: {year}')
        doi = self._normalize_doi(result.get('doi', ''))
        if doi:
            parts.append(f'DOI: {doi}')
        return '\n'.join(parts)

    def _apply_search_rerank(
        self,
        *,
        query: str,
        results: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any], str]:
        clean_query = self._normalize_text(query)
        rerank_meta = {
            'enabled': bool(self.valves.SEARCH_RERANK_ENABLED),
            'applied': False,
            'query': clean_query,
            'candidates': 0,
        }
        if not rerank_meta['enabled'] or not results:
            return results, rerank_meta, ''
        if not clean_query:
            return results, rerank_meta, 'search rerank skipped because query is empty.'
        if not self.valves.RERANK_MODEL.strip():
            return (
                results,
                rerank_meta,
                'SEARCH_RERANK_ENABLED is true but RERANK_MODEL is not configured.',
            )

        candidate_limit = min(len(results), max(1, int(self.valves.SEARCH_RERANK_CANDIDATES)))
        rerank_meta['candidates'] = candidate_limit
        head = list(results[:candidate_limit])
        tail = list(results[candidate_limit:])
        documents = [self._build_search_rerank_document(item) for item in head]

        try:
            rerank_results = self._rerank_documents(clean_query, documents)
        except Exception as exc:
            return results, rerank_meta, str(exc)

        ranked_head: List[Dict[str, Any]] = []
        seen_indices = set()
        for item in rerank_results:
            try:
                idx = int(item.get('index'))
            except (TypeError, ValueError):
                continue
            if idx < 0 or idx >= len(head) or idx in seen_indices:
                continue
            ranked_head.append(head[idx])
            seen_indices.add(idx)
        for idx, row in enumerate(head):
            if idx in seen_indices:
                continue
            ranked_head.append(row)

        rerank_meta['applied'] = True
        return ranked_head + tail, rerank_meta, ''

    def _build_search_article_rag_document(self, result: Dict[str, Any]) -> str:
        return self._build_search_rerank_document(result)

    def _build_search_article_rag_chunks(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        chunks: List[Dict[str, Any]] = []
        chunk_counter = 0
        for row in results:
            if not isinstance(row, dict):
                continue
            text = self._normalize_block_text(self._build_search_article_rag_document(row))
            if not text:
                continue

            chunk_counter += 1
            title = self._normalize_text(str(row.get('title', '') or f'Result {chunk_counter}'))
            doi = self._normalize_doi(row.get('doi', ''))
            url = self._normalize_text(str(row.get('url', '') or (f'https://doi.org/{doi}' if doi else '')))
            chunks.append(
                {
                    'chunk_id': f'a{chunk_counter}',
                    'section_title': title or f'Result {chunk_counter}',
                    'section_type': 'article',
                    'part_index': 1,
                    'char_start': 0,
                    'char_end': len(text),
                    'text': text,
                    'title': title,
                    'doi': doi,
                    'url': url,
                    'database': self._normalize_text(str(row.get('database', ''))),
                    'authors': self._normalize_text(str(row.get('authors', ''))),
                    'journal': self._normalize_text(str(row.get('journal', ''))),
                    'year': self._normalize_text(str(row.get('year', ''))),
                    'matched_query': self._normalize_text(str(row.get('matched_query', ''))),
                }
            )
        return chunks

    def _build_search_article_rag_index(self, *, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        chunks = self._build_search_article_rag_chunks(results)
        if not chunks:
            raise RuntimeError('No article-level chunks were built from search results.')

        chunk_texts = [chunk['text'] for chunk in chunks]
        bm25_index = self._build_bm25_index([self._tokenize_for_bm25(text) for text in chunk_texts])

        dense_vectors: List[List[float]] = []
        dense_norms: List[float] = []
        embedding_warning = ''
        try:
            dense_vectors = self._embed_texts(chunk_texts)
            dense_norms = [math.sqrt(sum(value * value for value in vec)) for vec in dense_vectors]
        except Exception as exc:
            embedding_warning = str(exc)
            dense_vectors = []
            dense_norms = []

        return {
            'chunks': chunks,
            'bm25_index': bm25_index,
            'dense_vectors': dense_vectors,
            'dense_norms': dense_norms,
            'embedding_warning': embedding_warning,
            'built_at': time.time(),
        }

    def _query_search_article_rag(
        self,
        *,
        rag_index: Dict[str, Any],
        query: str,
        top_k: int,
        bm25_candidates: int,
        dense_candidates: int,
        rrf_k: int,
    ) -> Dict[str, Any]:
        result = self._query_fulltext_rag(
            rag_index=rag_index,
            query=query,
            top_k=top_k,
            bm25_candidates=bm25_candidates,
            dense_candidates=dense_candidates,
            rrf_k=rrf_k,
            rerank_enabled=bool(self.valves.RERANK_ENABLED),
            rerank_candidates=min(max(top_k, int(self.valves.RERANK_CANDIDATES)), 200),
        )

        chunk_map = {
            str(chunk.get('chunk_id', '')): chunk
            for chunk in self._as_list(rag_index.get('chunks', []))
            if isinstance(chunk, dict) and chunk.get('chunk_id')
        }
        for evidence in self._as_list(result.get('evidence_chunks', [])):
            if not isinstance(evidence, dict):
                continue
            chunk = chunk_map.get(str(evidence.get('chunk_id', '')), {})
            evidence['title'] = chunk.get('title', '')
            evidence['doi'] = chunk.get('doi', '')
            evidence['url'] = chunk.get('url', '')
            evidence['database'] = chunk.get('database', '')
            evidence['authors'] = chunk.get('authors', '')
            evidence['journal'] = chunk.get('journal', '')
            evidence['year'] = chunk.get('year', '')

        result.setdefault('index_stats', {})
        result['index_stats']['article_count'] = len(chunk_map)
        return result

    # ------------------------------------------------------------------
    # Query-building helpers
    # ------------------------------------------------------------------

    def _escape_query_literal(self, value: Any) -> str:
        text = self._normalize_text(str(value or ''))
        return text.replace('"', r'\"')

    def _quote_query_literal(self, value: Any) -> str:
        return f'"{self._escape_query_literal(value)}"'

    def _format_exact_identifier(self, value: Any) -> str:
        text = self._normalize_text(str(value or ''))
        if not text:
            return ''
        if re.fullmatch(r'[A-Za-z0-9._:/+-]+', text):
            return text
        return self._quote_query_literal(text)

    def _parse_year_values(self, year: Any) -> List[int]:
        if year is None:
            return []
        if isinstance(year, (list, tuple, set)):
            values: List[int] = []
            for item in year:
                values.extend(self._parse_year_values(item))
            return values

        text = self._normalize_text(str(year))
        if not text:
            return []

        range_match = re.fullmatch(r'(19|20)\d{2}\s*[-/]\s*(19|20)\d{2}', text)
        if range_match:
            start_str, end_str = re.split(r'\s*[-/]\s*', text)
            start_year = int(start_str)
            end_year = int(end_str)
            if start_year > end_year:
                start_year, end_year = end_year, start_year
            return list(range(start_year, end_year + 1))

        values = []
        for candidate in re.findall(r'(?:19|20)\d{2}', text):
            try:
                values.append(int(candidate))
            except ValueError:
                continue
        deduped: List[int] = []
        for value in values:
            if value not in deduped:
                deduped.append(value)
        return deduped

    def _join_query_clauses(self, clauses: List[str]) -> str:
        clean_clauses = [self._normalize_text(clause) for clause in clauses if self._normalize_text(clause)]
        if not clean_clauses:
            return ''
        if len(clean_clauses) == 1:
            return clean_clauses[0]
        return ' AND '.join(
            f'({clause})' if ' ' in clause and not clause.startswith('(') else clause for clause in clean_clauses
        )

    def _build_scopus_query_from_metadata(
        self,
        *,
        query: Optional[str] = None,
        title: Optional[str] = None,
        author: Optional[str] = None,
        author_id: Optional[str] = None,
        journal: Optional[str] = None,
        doi: Optional[str] = None,
        year: Any = None,
        affiliation: Optional[str] = None,
        keywords: Optional[str] = None,
        issn: Optional[str] = None,
        pii: Optional[str] = None,
        pubmed_id: Optional[str] = None,
        scopus_id: Optional[str] = None,
    ) -> str:
        clauses: List[str] = []

        raw_query = self._normalize_text(str(query or ''))
        if raw_query:
            clauses.append(raw_query)

        if title:
            clauses.append(f'TITLE({self._quote_query_literal(title)})')
        if author:
            clauses.append(f'AUTH({self._quote_query_literal(author)})')
        if author_id:
            clauses.append(f'AU-ID({self._format_exact_identifier(author_id)})')
        if journal:
            clauses.append(f'SRCTITLE({self._quote_query_literal(journal)})')
        if doi:
            normalized_doi = self._normalize_doi(doi)
            if normalized_doi:
                clauses.append(f'DOI({self._format_exact_identifier(normalized_doi)})')
        if affiliation:
            clauses.append(f'AFFIL({self._quote_query_literal(affiliation)})')
        if keywords:
            clauses.append(f'TITLE-ABS-KEY({self._quote_query_literal(keywords)})')
        if issn:
            clauses.append(f'ISSN({self._format_exact_identifier(issn)})')
        if pii:
            clauses.append(f'PII({self._format_exact_identifier(pii)})')
        if pubmed_id:
            clauses.append(f'PMID({self._format_exact_identifier(pubmed_id)})')
        if scopus_id:
            clean_scopus_id = self._normalize_text(str(scopus_id))
            if clean_scopus_id.lower().startswith('2-s2.0-'):
                clauses.append(f'EID({self._format_exact_identifier(clean_scopus_id)})')

        years = self._parse_year_values(year)
        if years:
            if len(years) == 1:
                clauses.append(f'PUBYEAR = {years[0]}')
            else:
                year_clauses = [f'PUBYEAR = {year_value}' for year_value in years]
                clauses.append(
                    ' OR '.join(year_clauses) if len(year_clauses) == 1 else f'({" OR ".join(year_clauses)})'
                )

        return self._join_query_clauses(clauses)

    def _build_sciencedirect_query_from_metadata(
        self,
        *,
        query: Optional[str] = None,
        title: Optional[str] = None,
        author: Optional[str] = None,
        journal: Optional[str] = None,
        doi: Optional[str] = None,
        year: Any = None,
        affiliation: Optional[str] = None,
        keywords: Optional[str] = None,
        issn: Optional[str] = None,
        pii: Optional[str] = None,
    ) -> str:
        clauses: List[str] = []

        raw_query = self._normalize_text(str(query or ''))
        if raw_query:
            clauses.append(raw_query)

        normalized_doi = self._normalize_doi(doi)
        for value in [
            title,
            keywords,
            normalized_doi,
            author,
            journal,
            affiliation,
            issn,
            pii,
        ]:
            clean_value = self._normalize_text(str(value or ''))
            if clean_value:
                clauses.append(self._quote_query_literal(clean_value))

        years = self._parse_year_values(year)
        for year_value in years:
            clauses.append(f'YEAR({year_value})')

        return self._join_query_clauses(clauses)

    def _build_search_queries(
        self,
        *,
        query: Optional[str] = None,
        database: Optional[str] = None,
        title: Optional[str] = None,
        author: Optional[str] = None,
        author_id: Optional[str] = None,
        journal: Optional[str] = None,
        doi: Optional[str] = None,
        year: Any = None,
        affiliation: Optional[str] = None,
        keywords: Optional[str] = None,
        issn: Optional[str] = None,
        pii: Optional[str] = None,
        pubmed_id: Optional[str] = None,
        scopus_id: Optional[str] = None,
    ) -> Tuple[str, Dict[str, str], List[str]]:
        db = database.strip().lower() if database else self.valves.DEFAULT_DATABASE.strip().lower()
        target_sources = ['scopus', 'sciencedirect'] if db == 'both' else [db]

        source_queries: Dict[str, str] = {}
        warnings: List[str] = []

        if 'scopus' in target_sources:
            source_queries['scopus'] = self._build_scopus_query_from_metadata(
                query=query,
                title=title,
                author=author,
                author_id=author_id,
                journal=journal,
                doi=doi,
                year=year,
                affiliation=affiliation,
                keywords=keywords,
                issn=issn,
                pii=pii,
                pubmed_id=pubmed_id,
                scopus_id=scopus_id,
            )

        clean_scopus_id = self._normalize_text(str(scopus_id or ''))
        if clean_scopus_id and not clean_scopus_id.lower().startswith('2-s2.0-'):
            warnings.append(
                'Direct document lookup by numeric scopus_id is not compiled into Scopus Search; for an exact record lookup, prefer get_article_abstract(scopus_id=...).'
            )

        if 'sciencedirect' in target_sources:
            source_queries['sciencedirect'] = self._build_sciencedirect_query_from_metadata(
                query=query,
                title=title,
                author=author,
                journal=journal,
                doi=doi,
                year=year,
                affiliation=affiliation,
                keywords=keywords,
                issn=issn,
                pii=pii,
            )
            precision_limited = [
                name
                for name, value in {
                    'author': author,
                    'journal': journal,
                    'affiliation': affiliation,
                    'issn': issn,
                }.items()
                if self._normalize_text(str(value or ''))
            ]
            if precision_limited:
                warnings.append(
                    'ScienceDirect metadata filtering for '
                    + ', '.join(precision_limited)
                    + ' is best-effort in this tool; Scopus receives the precise fielded query.'
                )

        primary_query = source_queries.get(target_sources[0], '') if target_sources else ''
        return primary_query, source_queries, warnings

    # ------------------------------------------------------------------
    # Search payload parsers
    # ------------------------------------------------------------------

    def _build_scopus_author_query(
        self,
        *,
        query: Optional[str] = None,
        name: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        affiliation: Optional[str] = None,
        orcid: Optional[str] = None,
    ) -> str:
        clauses: List[str] = []

        raw_query = self._normalize_text(str(query or ''))
        if raw_query:
            clauses.append(raw_query)

        resolved_first = self._normalize_text(str(first_name or ''))
        resolved_last = self._normalize_text(str(last_name or ''))
        resolved_name = self._normalize_text(str(name or ''))

        if resolved_name and (not resolved_first and not resolved_last):
            name_parts = [part for part in re.split(r'\s+', resolved_name) if part]
            if len(name_parts) >= 2:
                resolved_first = ' '.join(name_parts[:-1])
                resolved_last = name_parts[-1]
            else:
                resolved_last = resolved_name

        if resolved_last:
            clauses.append(f'authlast({self._escape_query_literal(resolved_last)})')
        if resolved_first:
            clauses.append(f'authfirst({self._escape_query_literal(resolved_first)})')
        if affiliation:
            clauses.append(f'affil({self._escape_query_literal(affiliation)})')
        if orcid:
            clauses.append(self._quote_query_literal(orcid))

        return self._join_query_clauses(clauses)

    def _parse_scopus_author_search_results(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        entries = payload.get('search-results', {}).get('entry', [])
        for entry in entries:
            if entry.get('@_fa') == 'false' or 'error' in entry:
                continue

            preferred_name = entry.get('preferred-name', {}) if isinstance(entry.get('preferred-name'), dict) else {}
            current_affiliation = (
                entry.get('affiliation-current', {}) if isinstance(entry.get('affiliation-current'), dict) else {}
            )
            subject_areas = self._as_list(entry.get('subject-area', []))
            normalized_areas: List[Dict[str, Any]] = []
            for area in subject_areas:
                if not isinstance(area, dict):
                    continue
                normalized_areas.append(
                    {
                        'name': self._normalize_text(str(area.get('$', ''))),
                        'abbrev': self._normalize_text(str(area.get('@abbrev', ''))),
                        'frequency': self._coerce_int(area.get('@frequency')),
                    }
                )

            author_id = str(entry.get('dc:identifier', '')).replace('AUTHOR_ID:', '').strip()
            eid = str(entry.get('eid', '')).strip()
            orcid = self._normalize_text(str(entry.get('orcid', '')))
            surname = self._normalize_text(str(preferred_name.get('surname', '')))
            given_name = self._normalize_text(str(preferred_name.get('given-name', '')))
            initials = self._normalize_text(str(preferred_name.get('initials', '')))
            indexed_name = self._normalize_text(str(preferred_name.get('indexed-name', '')))
            full_name = self._normalize_text(' '.join(part for part in [given_name, surname] if part)) or indexed_name

            result = {
                'author_id': author_id,
                'eid': eid,
                'orcid': orcid,
                'full_name': full_name,
                'surname': surname,
                'given_name': given_name,
                'initials': initials,
                'indexed_name': indexed_name,
                'document_count': self._coerce_int(entry.get('document-count')),
                'current_affiliation': {
                    'name': self._normalize_text(str(current_affiliation.get('affiliation-name', ''))),
                    'id': self._normalize_text(str(current_affiliation.get('affiliation-id', ''))),
                    'city': self._normalize_text(str(current_affiliation.get('affiliation-city', ''))),
                    'country': self._normalize_text(str(current_affiliation.get('affiliation-country', ''))),
                },
                'subject_areas': normalized_areas,
                'url': self._extract_link(entry.get('link', []), ['scopus-author', 'self', 'search']),
            }
            results.append(result)
        return results

    def _parse_scopus_results(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        entries = payload.get('search-results', {}).get('entry', [])
        for entry in entries:
            if entry.get('@_fa') == 'false' or 'error' in entry:
                continue

            doi = self._normalize_doi(entry.get('prism:doi', ''))
            scopus_id = str(entry.get('dc:identifier', '')).replace('SCOPUS_ID:', '').strip()
            title = self._normalize_text(str(entry.get('dc:title', '')))
            journal = self._normalize_text(str(entry.get('prism:publicationName', '')))
            date = str(entry.get('prism:coverDate', '')).strip()
            cited_by = self._coerce_int(entry.get('citedby-count'))
            abstract = self._normalize_block_text(str(entry.get('dc:description', '')))
            authors = self._normalize_text(str(entry.get('dc:creator', '')))
            url = self._extract_link(entry.get('link', []), ['scopus', 'self', 'scopus-citedby'])
            if not url and doi:
                url = f'https://doi.org/{doi}'

            open_access = self._coerce_bool(
                entry.get('openaccess') or entry.get('openaccessFlag') or entry.get('openaccessArticle')
            )

            result = {
                'database': 'scopus',
                'source_databases': ['scopus'],
                'title': title,
                'authors': authors,
                'journal': journal,
                'date': date,
                'year': date[:4] if date else '',
                'doi': doi,
                'pii': str(entry.get('pii', '')).strip(),
                'scopus_id': scopus_id,
                'eid': str(entry.get('eid', '')).strip(),
                'cited_by': cited_by,
                'abstract': abstract,
                'snippet': self._truncate_text(abstract or title, 320),
                'url': url,
                'document_type': self._normalize_text(
                    str(entry.get('subtypeDescription', '') or entry.get('subtype', ''))
                ),
                'publication_type': self._normalize_text(str(entry.get('prism:aggregationType', ''))),
                'open_access': open_access,
            }
            results.append(result)
        return results

    def _parse_scidir_author_names(self, authors_raw: Any) -> str:
        if isinstance(authors_raw, str):
            return self._normalize_text(authors_raw)
        authors: List[str] = []
        if isinstance(authors_raw, dict):
            author_list = authors_raw.get('author', [])
            for author in self._as_list(author_list):
                if not isinstance(author, dict):
                    continue
                given = self._normalize_text(str(author.get('given-name', '')))
                surname = self._normalize_text(str(author.get('surname', '')))
                indexed = self._normalize_text(str(author.get('$', '')))
                name = self._normalize_text(' '.join(part for part in [given, surname] if part)) or indexed
                if name:
                    authors.append(name)
        elif isinstance(authors_raw, list):
            for author in authors_raw:
                if isinstance(author, dict):
                    name = self._normalize_text(
                        ' '.join(
                            part
                            for part in [
                                str(author.get('given-name', '')).strip(),
                                str(author.get('surname', '')).strip(),
                            ]
                            if part
                        )
                    )
                    if name:
                        authors.append(name)
        return ', '.join(authors)

    def _parse_scidir_results(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        entries = payload.get('search-results', {}).get('entry', [])
        for entry in entries:
            if 'error' in entry:
                continue

            doi = self._normalize_doi(entry.get('prism:doi', ''))
            pii = str(entry.get('pii', '')).strip()
            title = self._normalize_text(str(entry.get('dc:title', '')))
            journal = self._normalize_text(str(entry.get('prism:publicationName', '')))
            date = str(entry.get('prism:coverDate', '')).strip()
            abstract = self._normalize_block_text(str(entry.get('dc:description', '')))
            authors = self._parse_scidir_author_names(entry.get('authors', {}))
            url = self._extract_link(entry.get('link', []), ['scidir', 'self'])
            if not url and doi:
                url = f'https://doi.org/{doi}'

            open_access = self._coerce_bool(
                entry.get('openaccess') or entry.get('openaccessArticle') or entry.get('openArchiveArticle')
            )

            result = {
                'database': 'sciencedirect',
                'source_databases': ['sciencedirect'],
                'title': title,
                'authors': authors,
                'journal': journal,
                'date': date,
                'year': date[:4] if date else '',
                'doi': doi,
                'pii': pii,
                'scopus_id': str(entry.get('scopus-id', '')).strip(),
                'eid': str(entry.get('eid', '')).strip(),
                'cited_by': self._coerce_int(entry.get('citedby-count')),
                'abstract': abstract,
                'snippet': self._truncate_text(abstract or title, 320),
                'url': url,
                'document_type': self._normalize_text(
                    str(entry.get('subtypeDescription', '') or entry.get('articleType', ''))
                ),
                'publication_type': self._normalize_text(str(entry.get('prism:aggregationType', ''))),
                'open_access': open_access,
            }
            results.append(result)
        return results

    # ------------------------------------------------------------------
    # Search clients
    # ------------------------------------------------------------------

    def _search_scopus_structured(
        self,
        *,
        query: str,
        limit: int,
        sort: Optional[str],
        offset: int,
        open_access_only: bool,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            'query': query,
            'count': limit,
            'start': max(0, offset),
        }
        if sort:
            params['sort'] = sort
        url = self._build_url('https://api.elsevier.com/content/search/scopus', params)
        payload = self._fetch_json(url)
        total_results = self._coerce_int(payload.get('search-results', {}).get('opensearch:totalResults'))
        warnings: List[str] = []
        results = self._parse_scopus_results(payload)
        if open_access_only:
            filtered = [result for result in results if result.get('open_access') is True]
            if filtered:
                results = filtered
            else:
                warnings.append(
                    'Scopus open_access_only is best-effort because open-access flags are not present on every result.'
                )
        return {
            'database': 'scopus',
            'backend': 'get',
            'total_results': total_results,
            'results': results,
            'warnings': warnings,
        }

    def _search_sciencedirect_put_payload(
        self,
        *,
        query: str,
        limit: int,
        sort: Optional[str],
        offset: int,
        open_access_only: bool,
        article_types: Optional[str],
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            'qs': query,
            'display': {
                'offset': max(0, offset),
                'show': limit,
            },
        }
        if sort:
            payload['sortBy'] = sort
        filters: Dict[str, Any] = {}
        if open_access_only:
            filters['openAccess'] = True
        if article_types:
            filters['articleTypes'] = article_types
        if filters:
            payload['filters'] = filters
        return payload

    def _search_sciencedirect_structured(
        self,
        *,
        query: str,
        limit: int,
        sort: Optional[str],
        date_range: Optional[str],
        offset: int,
        open_access_only: bool,
        article_types: Optional[str],
        field: Optional[str],
    ) -> Dict[str, Any]:
        warnings: List[str] = []
        base_url = 'https://api.elsevier.com/content/search/sciencedirect'

        should_force_get = bool(date_range or field)
        if not should_force_get:
            try:
                payload = self._fetch_json(
                    base_url,
                    method='PUT',
                    data=self._search_sciencedirect_put_payload(
                        query=query,
                        limit=limit,
                        sort=sort,
                        offset=offset,
                        open_access_only=open_access_only,
                        article_types=article_types,
                    ),
                    accept='application/json',
                    content_type='application/json',
                )
                total_results = self._coerce_int(payload.get('search-results', {}).get('opensearch:totalResults'))
                return {
                    'database': 'sciencedirect',
                    'backend': 'put_v2',
                    'total_results': total_results,
                    'results': self._parse_scidir_results(payload),
                    'warnings': warnings,
                }
            except Exception as exc:
                warnings.append(f'ScienceDirect PUT search failed, falling back to GET: {exc}')

        params: Dict[str, Any] = {
            'query': query,
            'count': limit,
            'start': max(0, offset),
        }
        if sort:
            params['sort'] = sort
        if date_range:
            params['date'] = date_range
        if field:
            params['field'] = field
        url = self._build_url(base_url, params)
        payload = self._fetch_json(url)
        total_results = self._coerce_int(payload.get('search-results', {}).get('opensearch:totalResults'))
        results = self._parse_scidir_results(payload)

        if open_access_only:
            filtered = [result for result in results if result.get('open_access') is True]
            if filtered:
                results = filtered
            else:
                warnings.append(
                    'ScienceDirect search fell back to GET, so open_access_only could not be enforced reliably.'
                )

        return {
            'database': 'sciencedirect',
            'backend': 'get_legacy',
            'total_results': total_results,
            'results': results,
            'warnings': warnings,
        }

    async def _search_articles_structured(
        self,
        *,
        query: Optional[str],
        database: str,
        max_results: int,
        sort: Optional[str],
        date_range: Optional[str],
        offset: int,
        open_access_only: bool,
        article_types: Optional[str],
        field: Optional[str],
        enrich_top_n: int,
        include_access: bool,
        abstract_view: Optional[str],
        abstract_field: Optional[str],
        emitter=None,
        source_queries: Optional[Dict[str, str]] = None,
        source_query_variants: Optional[Dict[str, List[str]]] = None,
        extra_warnings: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        clean_query = self._normalize_text(str(query or ''))

        db = database.strip().lower() if database else self.valves.DEFAULT_DATABASE.strip().lower()
        if db not in {'scopus', 'sciencedirect', 'both'}:
            return {
                'error': 'database must be one of: scopus, sciencedirect, both',
                'results': [],
            }

        limit = min(max(1, max_results), 25)
        target_sources = ['scopus', 'sciencedirect'] if db == 'both' else [db]

        resolved_query_variants: Dict[str, List[str]] = {}
        for source_name in target_sources:
            raw_variants = (source_query_variants or {}).get(source_name, [])
            normalized_variants: List[str] = []
            seen_variants = set()
            for raw_variant in self._as_list(raw_variants):
                variant = self._normalize_text(str(raw_variant or ''))
                if not variant:
                    continue
                key = variant.lower()
                if key in seen_variants:
                    continue
                seen_variants.add(key)
                normalized_variants.append(variant)

            if not normalized_variants:
                candidate = self._normalize_text(str((source_queries or {}).get(source_name, clean_query) or ''))
                if candidate:
                    normalized_variants = [candidate]

            if normalized_variants:
                resolved_query_variants[source_name] = normalized_variants[: self._search_query_variant_limit()]

        if not resolved_query_variants:
            return {
                'error': 'Search query must not be empty. Provide query text or metadata fields.',
                'results': [],
            }

        resolved_queries = {
            source_name: variants[0] for source_name, variants in resolved_query_variants.items() if variants
        }

        status_parts = [
            f'{name}[{len(resolved_query_variants[name])}]: {resolved_query_variants[name][0]}'
            for name in target_sources
            if name in resolved_query_variants
        ]
        await self._emit_status(emitter, f'Searching {"; ".join(status_parts)}', False)

        tasks: List[Tuple[str, int, str, asyncio.Future]] = []
        for source_name, variants in resolved_query_variants.items():
            for variant_idx, variant_query in enumerate(variants):
                if source_name == 'scopus':
                    tasks.append(
                        (
                            source_name,
                            variant_idx,
                            variant_query,
                            asyncio.to_thread(
                                self._search_scopus_structured,
                                query=variant_query,
                                limit=limit,
                                sort=sort,
                                offset=offset,
                                open_access_only=open_access_only,
                            ),
                        )
                    )
                elif source_name == 'sciencedirect':
                    tasks.append(
                        (
                            source_name,
                            variant_idx,
                            variant_query,
                            asyncio.to_thread(
                                self._search_sciencedirect_structured,
                                query=variant_query,
                                limit=limit,
                                sort=sort,
                                date_range=date_range,
                                offset=offset,
                                open_access_only=open_access_only,
                                article_types=article_types,
                                field=field,
                            ),
                        )
                    )

        raw_results: List[Dict[str, Any]] = []
        backends: Dict[str, str] = {}
        totals_by_database: Dict[str, Optional[int]] = {}
        warnings: List[str] = list(extra_warnings or [])
        partial_errors: List[str] = []

        responses = await asyncio.gather(*(task for _, _, _, task in tasks), return_exceptions=True)
        for (source_name, variant_idx, variant_query, _), response in zip(tasks, responses):
            if isinstance(response, Exception):
                partial_errors.append(f'{source_name}[{variant_idx + 1}]: {response}')
                continue
            backends[source_name] = response.get('backend', 'unknown')
            total_value = response.get('total_results')
            prior_total = totals_by_database.get(source_name)
            if isinstance(total_value, int):
                if not isinstance(prior_total, int):
                    totals_by_database[source_name] = total_value
                else:
                    totals_by_database[source_name] = max(prior_total, total_value)
            elif source_name not in totals_by_database:
                totals_by_database[source_name] = total_value
            warnings.extend(response.get('warnings', []))
            for row in self._as_list(response.get('results', [])):
                if not isinstance(row, dict):
                    continue
                item = dict(row)
                item.setdefault('matched_query', variant_query)
                raw_results.append(item)

        results = self._dedupe_results(raw_results, limit=limit)
        rerank_query = clean_query or next(
            (variants[0] for variants in resolved_query_variants.values() if variants),
            '',
        )
        results, search_rerank, rerank_warning = self._apply_search_rerank(
            query=rerank_query,
            results=results,
        )
        if rerank_warning:
            warnings.append(f'search rerank unavailable: {rerank_warning}')

        if enrich_top_n > 0 or include_access:
            enrich_count = min(
                len(results),
                enrich_top_n if enrich_top_n > 0 else self.valves.MAX_ENRICH_RESULTS,
            )
            if enrich_count > 0:
                await self._emit_status(
                    emitter,
                    f'Enriching top {enrich_count} results with abstracts/access signals',
                    False,
                )
                results = await self._enrich_results(
                    results,
                    enrich_count=enrich_count,
                    include_access=include_access,
                    abstract_view=abstract_view,
                    abstract_field=abstract_field,
                )

        return {
            'database': db,
            'query': clean_query,
            'source_queries': resolved_queries,
            'source_query_variants': resolved_query_variants,
            'result_count': len(results),
            'results': results,
            'totals_by_database': totals_by_database,
            'used_backends': backends,
            'search_rerank': search_rerank,
            'warnings': warnings,
            'partial_errors': partial_errors,
        }

    # ------------------------------------------------------------------
    # Abstract / metadata retrieval
    # ------------------------------------------------------------------

    def _parse_author_details(self, core: Dict[str, Any]) -> List[Dict[str, str]]:
        authors_section = core.get('authors', {})
        author_list = authors_section.get('author', []) if isinstance(authors_section, dict) else []
        author_list = self._as_list(author_list)
        authors: List[Dict[str, str]] = []
        for author in author_list:
            if not isinstance(author, dict):
                continue
            preferred_name = author.get('preferred-name', {}) if isinstance(author.get('preferred-name'), dict) else {}
            name = self._normalize_text(
                str(author.get('ce:indexed-name', ''))
                or str(preferred_name.get('ce:indexed-name', ''))
                or str(preferred_name.get('ce:surname', ''))
            )
            affil = author.get('affiliation', {})
            if isinstance(affil, list) and affil:
                affil = affil[0]
            affil_name = ''
            if isinstance(affil, dict):
                affil_name = self._normalize_text(str(affil.get('affilname', '')))
            if name:
                authors.append({'name': name, 'affiliation': affil_name})
        return authors

    def _parse_keyword_list(self, value: Any, outer_key: str, inner_key: str) -> List[str]:
        if not isinstance(value, dict):
            return []
        items = self._as_list(value.get(outer_key, []))
        parsed: List[str] = []
        for item in items:
            if isinstance(item, dict):
                candidate = self._normalize_text(str(item.get(inner_key, '') or item.get('$', '')))
            else:
                candidate = self._normalize_text(str(item))
            if candidate and candidate not in parsed:
                parsed.append(candidate)
        return parsed

    def _parse_abstract_payload(
        self,
        payload: Dict[str, Any],
        *,
        requested_identifier: str,
        requested_view: str,
    ) -> Dict[str, Any]:
        core = payload.get('abstracts-retrieval-response', payload)
        coredata = core.get('coredata', {}) if isinstance(core, dict) else {}

        title = self._normalize_text(str(coredata.get('dc:title', '')))
        abstract = self._normalize_block_text(str(coredata.get('dc:description', '')))
        journal = self._normalize_text(str(coredata.get('prism:publicationName', '')))
        date = str(coredata.get('prism:coverDate', '')).strip()
        doi = self._normalize_doi(coredata.get('prism:doi', ''))
        scopus_id = str(coredata.get('dc:identifier', '')).replace('SCOPUS_ID:', '').strip()
        eid = str(coredata.get('eid', '')).strip()
        cited_by = self._coerce_int(coredata.get('citedby-count'))
        publication_type = self._normalize_text(str(coredata.get('prism:aggregationType', '')))
        article_url = self._extract_link(coredata.get('link', []), ['scopus', 'self'])
        if not article_url and doi:
            article_url = f'https://doi.org/{doi}'
        elif not article_url:
            article_url = self._normalize_text(str(coredata.get('prism:url', '')))

        authors = self._parse_author_details(core) if isinstance(core, dict) else []
        keywords = (
            self._parse_keyword_list(core.get('authkeywords', {}), 'author-keyword', '$')
            if isinstance(core, dict)
            else []
        )
        subject_areas = (
            self._parse_keyword_list(core.get('subject-areas', {}), 'subject-area', '$')
            if isinstance(core, dict)
            else []
        )

        author_text = ', '.join(author['name'] for author in authors if author.get('name'))

        result = {
            'identifier': requested_identifier,
            'requested_view': requested_view,
            'title': title,
            'abstract': abstract,
            'authors': authors,
            'author_text': author_text,
            'journal': journal,
            'date': date,
            'year': date[:4] if date else '',
            'doi': doi,
            'scopus_id': scopus_id,
            'eid': eid,
            'cited_by': cited_by,
            'publication_type': publication_type,
            'keywords': keywords,
            'subject_areas': subject_areas,
            'url': article_url,
            'snippet': self._truncate_text(abstract or title, 320),
        }
        return result

    def _retrieve_abstract_structured(
        self,
        *,
        doi: Optional[str] = None,
        scopus_id: Optional[str] = None,
        view: Optional[str] = None,
        field: Optional[str] = None,
    ) -> Dict[str, Any]:
        if doi:
            identifier = self._normalize_doi(doi)
            base_url = f'https://api.elsevier.com/content/abstract/doi/{parse.quote(identifier, safe="/")}'
        elif scopus_id:
            identifier = scopus_id.strip()
            base_url = f'https://api.elsevier.com/content/abstract/scopus_id/{parse.quote(identifier)}'
        else:
            raise RuntimeError('Provide either doi or scopus_id.')

        requested_view = (view or self.valves.DEFAULT_ABSTRACT_VIEW).strip() or self.valves.DEFAULT_ABSTRACT_VIEW
        params: Dict[str, Any] = {'view': requested_view}
        if field:
            params['field'] = field
        url = self._build_url(base_url, params)
        payload = self._fetch_json(url)
        return self._parse_abstract_payload(payload, requested_identifier=identifier, requested_view=requested_view)

    # ------------------------------------------------------------------
    # Access / permission helpers
    # ------------------------------------------------------------------

    def _flatten_json(self, value: Any, prefix: str = '', out: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if out is None:
            out = {}
        if isinstance(value, dict):
            for key, subvalue in value.items():
                next_prefix = f'{prefix}.{key}' if prefix else str(key)
                self._flatten_json(subvalue, next_prefix, out)
        elif isinstance(value, list):
            for idx, subvalue in enumerate(value):
                next_prefix = f'{prefix}[{idx}]'
                self._flatten_json(subvalue, next_prefix, out)
        else:
            out[prefix] = value
        return out

    def _normalize_signal_key(self, key: str) -> str:
        return re.sub(r'[^a-z0-9]+', '', key.lower())

    def _parse_access_payload(self, payload: Dict[str, Any], *, identifier: str) -> Dict[str, Any]:
        flat = self._flatten_json(payload)
        signals: Dict[str, Any] = {}

        open_access: Optional[bool] = None
        can_host: Optional[bool] = None
        entitled: Optional[bool] = None

        for key, raw_value in flat.items():
            norm_key = self._normalize_signal_key(key)
            interesting = any(
                token in norm_key
                for token in [
                    'openaccess',
                    'openarchivearticle',
                    'availabletohost',
                    'articleavailabletohost',
                    'hostpermission',
                    'canhost',
                    'entitled',
                    'hasaccess',
                    'canaccess',
                    'accessible',
                    'permission',
                    'download',
                    'subscription',
                    'fulltext',
                ]
            )
            if interesting:
                signals[key] = raw_value

            bool_value = self._coerce_bool(raw_value)
            if open_access is None and any(token in norm_key for token in ['openaccess', 'openarchivearticle']):
                open_access = bool_value
            if can_host is None and any(
                token in norm_key
                for token in [
                    'availabletohost',
                    'articleavailabletohost',
                    'canhost',
                    'hostpermission',
                ]
            ):
                can_host = bool_value
            if entitled is None and any(
                token in norm_key
                for token in [
                    'entitled',
                    'hasaccess',
                    'canaccess',
                    'accessible',
                    'download',
                    'subscription',
                    'fulltext',
                ]
            ):
                entitled = bool_value

        if open_access is True:
            can_try_fulltext = True
        elif entitled is True:
            can_try_fulltext = True
        elif can_host is True:
            can_try_fulltext = True
        elif False in [open_access, entitled, can_host]:
            can_try_fulltext = False
        else:
            can_try_fulltext = None

        return {
            'identifier': identifier,
            'open_access': open_access,
            'can_host_final_version': can_host,
            'access_via_api': entitled,
            'can_try_fulltext': can_try_fulltext,
            'signals': signals,
        }

    def _check_article_access_structured(
        self,
        *,
        doi: Optional[str] = None,
        pii: Optional[str] = None,
        eid: Optional[str] = None,
        field: Optional[str] = None,
    ) -> Dict[str, Any]:
        identifier_type = ''
        identifier_value = ''
        if doi:
            identifier_type = 'doi'
            identifier_value = self._normalize_doi(doi)
        elif pii:
            identifier_type = 'pii'
            identifier_value = pii.strip()
        elif eid:
            identifier_type = 'eid'
            identifier_value = eid.strip()
        else:
            raise RuntimeError('Provide doi, pii, or eid.')

        base_url = f'https://api.elsevier.com/content/article/hostingpermission/{identifier_type}/{parse.quote(identifier_value, safe="/")}'
        params: Dict[str, Any] = {'ver': 'new'}
        if field:
            params['field'] = field
        url = self._build_url(base_url, params)
        payload = self._fetch_json(url)
        result = self._parse_access_payload(payload, identifier=identifier_value)
        result.update(
            {
                'identifier_type': identifier_type,
                'url': (f'https://doi.org/{identifier_value}' if identifier_type == 'doi' else ''),
            }
        )
        return result

    # ------------------------------------------------------------------
    # Enrichment helpers
    # ------------------------------------------------------------------

    async def _enrich_single_result(
        self,
        result: Dict[str, Any],
        *,
        include_access: bool,
        abstract_view: Optional[str],
        abstract_field: Optional[str],
    ) -> Dict[str, Any]:
        enriched = dict(result)
        tasks: List[Tuple[str, asyncio.Future]] = []

        if result.get('doi') or result.get('scopus_id'):
            tasks.append(
                (
                    'abstract',
                    asyncio.to_thread(
                        self._retrieve_abstract_structured,
                        doi=result.get('doi') or None,
                        scopus_id=result.get('scopus_id') or None,
                        view=abstract_view,
                        field=abstract_field,
                    ),
                )
            )

        if not tasks:
            return enriched

        responses = await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
        for (task_name, _), response in zip(tasks, responses):
            if isinstance(response, Exception):
                enriched.setdefault('enrichment_warnings', []).append(f'{task_name}: {response}')
                continue

            if task_name == 'abstract':
                if response.get('abstract'):
                    enriched['abstract'] = response['abstract']
                    enriched['snippet'] = self._truncate_text(response['abstract'], 320)
                if response.get('title') and not enriched.get('title'):
                    enriched['title'] = response['title']
                if response.get('author_text') and not enriched.get('authors'):
                    enriched['authors'] = response['author_text']
                if response.get('journal') and not enriched.get('journal'):
                    enriched['journal'] = response['journal']
                if response.get('date') and not enriched.get('date'):
                    enriched['date'] = response['date']
                    enriched['year'] = response.get('year', enriched.get('year', ''))
                if response.get('doi') and not enriched.get('doi'):
                    enriched['doi'] = response['doi']
                if response.get('scopus_id') and not enriched.get('scopus_id'):
                    enriched['scopus_id'] = response['scopus_id']
                if response.get('cited_by') is not None:
                    enriched['cited_by'] = response.get('cited_by')
                if response.get('publication_type') and not enriched.get('publication_type'):
                    enriched['publication_type'] = response['publication_type']
                if response.get('keywords'):
                    enriched['keywords'] = response['keywords']
                if response.get('subject_areas'):
                    enriched['subject_areas'] = response['subject_areas']
                if response.get('authors'):
                    enriched['author_details'] = response['authors']
                if response.get('url') and not enriched.get('url'):
                    enriched['url'] = response['url']
                enriched['abstract_view'] = response.get('requested_view')

        return enriched

    async def _enrich_results(
        self,
        results: List[Dict[str, Any]],
        *,
        enrich_count: int,
        include_access: bool,
        abstract_view: Optional[str],
        abstract_field: Optional[str],
    ) -> List[Dict[str, Any]]:
        enriched_results = list(results)
        tasks: List[Tuple[int, asyncio.Future]] = []
        for idx, result in enumerate(results[:enrich_count]):
            tasks.append(
                (
                    idx,
                    self._enrich_single_result(
                        result,
                        include_access=include_access,
                        abstract_view=abstract_view,
                        abstract_field=abstract_field,
                    ),
                )
            )
        responses = await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
        for (idx, _), response in zip(tasks, responses):
            if isinstance(response, Exception):
                enriched_results[idx].setdefault('enrichment_warnings', []).append(str(response))
                continue
            enriched_results[idx] = response
        return enriched_results

    def _find_first_text_by_local_names(self, root: ET.Element, names: List[str]) -> str:
        wanted = {name.lower() for name in names}
        for elem in root.iter():
            if self._local_name(elem.tag) in wanted:
                text = self._normalize_text(' '.join(elem.itertext()))
                if text:
                    return text
        return ''

    # Sections that should be excluded from parsed full text output.
    _SKIP_SECTION_TITLES = {
        'references',
        'reference',
        'bibliography',
        'works cited',
        'literature cited',
        'notes and references',
        'supplementary data',
        'supplementary material',
        'supplementary materials',
        'graphical abstract',
        'keywords',
        'acknowledgments',
        'acknowledgements',
        'acknowledgment',
        'acknowledgement',
        'author contributions',
        'declaration of interests',
        'declaration of competing interest',
        'conflicts of interest',
        'conflict of interest',
        'credit authorship contribution statement',
        'creditauthorship contribution statement',
        'data sharing',
        'data availability',
        'data and code availability',
        'additional resources',
        'resource availability',
        'lead contact',
        'materials availability',
        'key resources table',
    }

    # Top-level method section titles whose sub-sections are collapsed into a
    # single "Methods (summary)" entry instead of being listed individually.
    _METHODS_SECTION_MARKERS = {
        'methods',
        'method',
        'materials and methods',
        'experimental procedures',
        'experimental section',
        'methodology',
    }

    def _is_skip_section(self, heading: str) -> bool:
        """Check if a section should be skipped entirely based on its title."""
        lowered = heading.lower().strip()
        if lowered in self._SKIP_SECTION_TITLES:
            return True
        # Match STAR★Methods and variants
        cleaned = re.sub(r'[^\w\s]', '', lowered)
        if cleaned.startswith('star') and 'method' in cleaned:
            return True
        if lowered.startswith('description:'):
            return True
        return False

    def _is_methods_parent(self, heading: str) -> bool:
        """Check if a section is a top-level Methods section whose children should be collapsed."""
        lowered = heading.lower().strip()
        cleaned = re.sub(r'[^\w\s]', '', lowered)
        if cleaned.startswith('star') and 'method' in cleaned:
            return True
        return lowered in self._METHODS_SECTION_MARKERS

    def _is_bibliography_like(self, elem: ET.Element) -> bool:
        local = self._local_name(elem.tag)
        if local in {
            'bibliography',
            'bib-reference',
            'bibliography-sec',
            'references',
            'ref-list',
            'ref',
            'reference',
            'citation',
            'citation-list',
            'footnote',
            'footnotes',
            'glossary',
            'acknowledgment',
            'acknowledgement',
            'appendix',
            'appendices',
            'supplementary-material',
            'supplementary-data',
            'conflict-of-interest',
        }:
            return True
        if 'bibliograph' in local or local.startswith('bib'):
            return True
        if local in {'section', 'sec'}:
            title = self._normalize_text(self._find_section_heading(elem)).lower()
            if title in self._SKIP_SECTION_TITLES:
                return True
        return False

    def _find_section_heading(self, section_elem: ET.Element) -> str:
        heading_tags = {
            'section-title',
            'title',
            'label',
            'caption',
            'para-title',
            'sectitle',
        }
        for child in section_elem:
            local = self._local_name(child.tag)
            if local in heading_tags:
                text = self._normalize_text(' '.join(child.itertext()))
                if text:
                    return text
        return ''

    def _collect_direct_text_from_section(self, section_elem: ET.Element) -> str:
        lines: List[str] = []

        def walk(elem: ET.Element) -> None:
            for child in elem:
                local = self._local_name(child.tag)
                if self._is_bibliography_like(child):
                    continue
                if local in {'section', 'sec'}:
                    continue
                if local in {
                    'section-title',
                    'title',
                    'label',
                    'caption',
                    'para-title',
                    'sectitle',
                }:
                    continue

                text = self._normalize_block_text(' '.join(child.itertext()))
                if text:
                    lines.append(text)

                if list(child) and local not in {
                    'para',
                    'p',
                    'simple-para',
                    'abstract',
                    'abstract-sec',
                    'note',
                    'list',
                    'list-item',
                    'item',
                    'entry',
                    'caption',
                    'table',
                    'table-wrap',
                    'table-row',
                }:
                    walk(child)

        walk(section_elem)

        deduped: List[str] = []
        seen = set()
        for line in lines:
            if line and line not in seen:
                seen.add(line)
                deduped.append(line)
        return self._normalize_block_text('\n\n'.join(deduped))

    def _parse_structured_abstract(self, abstract_elem: ET.Element) -> str:
        """Parse a structured abstract (e.g. Lancet: Background/Methods/Findings/...) into formatted text."""
        parts: List[str] = []
        for child in abstract_elem:
            local = self._local_name(child.tag)
            if local == 'abstract-sec':
                sec_title = ''
                for subchild in child:
                    if self._local_name(subchild.tag) == 'section-title':
                        sec_title = self._normalize_text(' '.join(subchild.itertext()))
                        break
                text = self._normalize_block_text(' '.join(child.itertext()))
                # Remove the heading text that itertext duplicates at the start
                if sec_title and text.startswith(sec_title):
                    text = text[len(sec_title) :].lstrip(' :\n')
                if sec_title and text:
                    parts.append(f'**{sec_title}**: {text}')
                elif text:
                    parts.append(text)
        if parts:
            return '\n\n'.join(parts)
        # Fallback: just get all text
        return self._normalize_block_text(' '.join(abstract_elem.itertext()))

    def _collect_methods_summary(self, methods_elem: ET.Element) -> str:
        """Collapse a STAR Methods / Methods section into a compact summary listing sub-section titles."""
        sub_titles: List[str] = []
        for child in methods_elem.iter():
            if child is methods_elem:
                continue
            local = self._local_name(child.tag)
            if local in {'section', 'sec'}:
                heading = self._find_section_heading(child)
                if heading and not self._is_skip_section(heading):
                    sub_titles.append(heading)
        if sub_titles:
            listing = ', '.join(sub_titles[:20])
            if len(sub_titles) > 20:
                listing += f', ... ({len(sub_titles)} sub-sections total)'
            return f'[Methods section with {len(sub_titles)} sub-sections: {listing}]'
        # If no sub-titles, extract top-level text
        text = self._collect_direct_text_from_section(methods_elem)
        if text and len(text) > 2000:
            return text[:2000] + '\n\n[... methods text truncated ...]'
        return text

    def _tokenize_for_bm25(self, text: str) -> List[str]:
        return re.findall(r'[a-z0-9]+', text.lower())

    def _build_bm25_index(self, tokenized_docs: List[List[str]]) -> Dict[str, Any]:
        term_frequencies: List[Counter] = []
        doc_lengths: List[int] = []
        doc_freq: Counter = Counter()

        for tokens in tokenized_docs:
            tf = Counter(tokens)
            term_frequencies.append(tf)
            length = len(tokens)
            doc_lengths.append(length)
            for term in tf:
                doc_freq[term] += 1

        doc_count = len(tokenized_docs)
        avg_doc_len = (sum(doc_lengths) / doc_count) if doc_count > 0 else 0.0
        idf: Dict[str, float] = {}
        for term, df in doc_freq.items():
            idf[term] = math.log(1.0 + (doc_count - df + 0.5) / (df + 0.5))

        return {
            'term_frequencies': term_frequencies,
            'doc_lengths': doc_lengths,
            'avg_doc_len': avg_doc_len,
            'idf': idf,
        }

    def _score_bm25(
        self,
        bm25_index: Dict[str, Any],
        query: str,
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> List[float]:
        query_terms = self._tokenize_for_bm25(query)
        term_frequencies: List[Counter] = bm25_index.get('term_frequencies', [])
        doc_lengths: List[int] = bm25_index.get('doc_lengths', [])
        avg_doc_len = float(bm25_index.get('avg_doc_len') or 0.0)
        idf: Dict[str, float] = bm25_index.get('idf', {})

        if not term_frequencies:
            return []
        if avg_doc_len <= 0:
            return [0.0 for _ in term_frequencies]

        scores = [0.0 for _ in term_frequencies]
        for term in query_terms:
            term_idf = idf.get(term)
            if term_idf is None:
                continue
            for idx, tf in enumerate(term_frequencies):
                freq = tf.get(term, 0)
                if freq <= 0:
                    continue
                doc_len = doc_lengths[idx] if idx < len(doc_lengths) else 0
                denom = freq + k1 * (1.0 - b + b * (doc_len / avg_doc_len))
                if denom <= 0:
                    continue
                scores[idx] += term_idf * ((freq * (k1 + 1.0)) / denom)
        return scores

    def _split_text_for_rag(self, text: str, chunk_size: int, overlap: int) -> List[Dict[str, Any]]:
        cleaned = self._normalize_block_text(text)
        if not cleaned:
            return []

        target_size = max(400, int(chunk_size))
        target_overlap = max(0, min(int(overlap), target_size - 1))
        min_split_pos = int(target_size * 0.6)
        chunks: List[Dict[str, Any]] = []
        start = 0
        text_len = len(cleaned)

        while start < text_len:
            tentative_end = min(text_len, start + target_size)
            end = tentative_end
            if tentative_end < text_len:
                split_window_start = min(tentative_end, start + min_split_pos)
                window = cleaned[split_window_start:tentative_end]
                split_offsets = [window.rfind(marker) for marker in ['\n\n', '. ', '; ', ': ', ', ', ' ']]
                best_offset = max(split_offsets)
                if best_offset > 0:
                    end = split_window_start + best_offset + 1

            chunk_text = cleaned[start:end].strip()
            if chunk_text:
                chunks.append(
                    {
                        'text': chunk_text,
                        'start': start,
                        'end': end,
                    }
                )

            if end >= text_len:
                break
            start = max(end - target_overlap, start + 1)
        return chunks

    def _build_rag_chunks(self, parsed: Dict[str, Any]) -> List[Dict[str, Any]]:
        chunk_size = max(400, int(self.valves.RAG_CHUNK_SIZE))
        overlap = max(0, min(int(self.valves.RAG_CHUNK_OVERLAP), chunk_size - 1))
        chunks: List[Dict[str, Any]] = []
        chunk_counter = 0

        abstract_text = self._normalize_block_text(str(parsed.get('abstract', '') or ''))
        if abstract_text:
            for part_idx, part in enumerate(self._split_text_for_rag(abstract_text, chunk_size, overlap), start=1):
                chunk_counter += 1
                chunks.append(
                    {
                        'chunk_id': f'c{chunk_counter}',
                        'section_title': 'Abstract',
                        'section_type': 'abstract',
                        'part_index': part_idx,
                        'text': part['text'],
                        'char_start': part['start'],
                        'char_end': part['end'],
                    }
                )

        for section in self._as_list(parsed.get('sections')):
            if not isinstance(section, dict):
                continue
            title = self._normalize_text(str(section.get('title', '') or 'Untitled Section'))
            section_text = self._normalize_block_text(str(section.get('text', '') or ''))
            if not section_text:
                continue
            for part_idx, part in enumerate(self._split_text_for_rag(section_text, chunk_size, overlap), start=1):
                chunk_counter += 1
                chunks.append(
                    {
                        'chunk_id': f'c{chunk_counter}',
                        'section_title': title or 'Untitled Section',
                        'section_type': 'section',
                        'part_index': part_idx,
                        'text': part['text'],
                        'char_start': part['start'],
                        'char_end': part['end'],
                    }
                )

        if not chunks:
            full_text = self._normalize_block_text(str(parsed.get('full_text', '') or ''))
            for part_idx, part in enumerate(self._split_text_for_rag(full_text, chunk_size, overlap), start=1):
                chunk_counter += 1
                chunks.append(
                    {
                        'chunk_id': f'c{chunk_counter}',
                        'section_title': 'Full Text',
                        'section_type': 'full_text',
                        'part_index': part_idx,
                        'text': part['text'],
                        'char_start': part['start'],
                        'char_end': part['end'],
                    }
                )
        return chunks

    def _fulltext_rag_signature(self) -> str:
        pieces = [
            'v1',
            self.valves.EMBEDDING_API_BASE.strip().lower(),
            self.valves.EMBEDDING_MODEL.strip(),
            str(int(self.valves.RAG_CHUNK_SIZE)),
            str(int(self.valves.RAG_CHUNK_OVERLAP)),
            str(bool(self.valves.RAG_KEEP_METHODS_GRANULAR)),
        ]
        return '|'.join(pieces)

    def _fulltext_rag_cache_get(self, cache_key: str, *, signature: str, content_hash: str) -> Optional[Dict[str, Any]]:
        with self._fulltext_rag_cache_lock:
            cached = self._fulltext_rag_cache.get(cache_key)
            if cached is None:
                return None
            if cached.get('signature') != signature:
                return None
            if cached.get('content_hash') != content_hash:
                return None
            self._fulltext_rag_cache.move_to_end(cache_key)
            return cached

    def _fulltext_rag_cache_set(self, cache_key: str, value: Dict[str, Any]) -> None:
        max_size = max(1, int(self.valves.RAG_INDEX_CACHE_SIZE))
        with self._fulltext_rag_cache_lock:
            self._fulltext_rag_cache[cache_key] = value
            self._fulltext_rag_cache.move_to_end(cache_key)
            while len(self._fulltext_rag_cache) > max_size:
                self._fulltext_rag_cache.popitem(last=False)

    def _build_fulltext_rag_index(
        self,
        *,
        cache_key: str,
        parsed: Dict[str, Any],
        signature: str,
        content_hash: str,
    ) -> Dict[str, Any]:
        chunks = self._build_rag_chunks(parsed)
        if not chunks:
            raise RuntimeError('No chunkable content was found in parsed full text.')

        chunk_texts = [chunk['text'] for chunk in chunks]
        bm25_index = self._build_bm25_index([self._tokenize_for_bm25(text) for text in chunk_texts])

        dense_vectors: List[List[float]] = []
        dense_norms: List[float] = []
        embedding_warning = ''
        try:
            dense_vectors = self._embed_texts(chunk_texts)
            dense_norms = [math.sqrt(sum(value * value for value in vec)) for vec in dense_vectors]
        except Exception as exc:
            embedding_warning = str(exc)
            dense_vectors = []
            dense_norms = []

        index_payload = {
            'cache_key': cache_key,
            'signature': signature,
            'content_hash': content_hash,
            'chunks': chunks,
            'bm25_index': bm25_index,
            'dense_vectors': dense_vectors,
            'dense_norms': dense_norms,
            'embedding_warning': embedding_warning,
            'built_at': time.time(),
        }
        self._fulltext_rag_cache_set(cache_key, index_payload)
        return index_payload

    def _query_fulltext_rag(
        self,
        *,
        rag_index: Dict[str, Any],
        query: str,
        top_k: int,
        bm25_candidates: int,
        dense_candidates: int,
        rrf_k: int,
        rerank_enabled: bool,
        rerank_candidates: int,
    ) -> Dict[str, Any]:
        clean_query = self._normalize_block_text(query)
        if not clean_query:
            raise RuntimeError('RAG query is empty.')

        chunks: List[Dict[str, Any]] = rag_index.get('chunks', [])
        if not chunks:
            raise RuntimeError('RAG index has no chunks.')

        bm25_scores = self._score_bm25(rag_index.get('bm25_index', {}), clean_query)
        bm25_ranked = list(range(len(chunks)))
        bm25_ranked.sort(
            key=lambda idx: bm25_scores[idx] if idx < len(bm25_scores) else 0.0,
            reverse=True,
        )
        bm25_ranked = bm25_ranked[: max(1, bm25_candidates)]

        dense_vectors: List[List[float]] = rag_index.get('dense_vectors', [])
        dense_norms: List[float] = rag_index.get('dense_norms', [])
        dense_scores = [0.0 for _ in chunks]
        dense_ranked: List[int] = []
        dense_used = False
        dense_query_warning = ''

        if dense_vectors:
            try:
                query_vector = self._embed_texts([clean_query])[0]
                query_norm = math.sqrt(sum(value * value for value in query_vector))
                if query_norm > 0:
                    for idx, vector in enumerate(dense_vectors):
                        if idx >= len(dense_scores):
                            break
                        doc_norm = dense_norms[idx] if idx < len(dense_norms) else 0.0
                        if doc_norm <= 0:
                            dense_scores[idx] = 0.0
                            continue
                        dot = sum(a * b for a, b in zip(query_vector, vector))
                        dense_scores[idx] = dot / (query_norm * doc_norm)
                    dense_ranked = list(range(len(chunks)))
                    dense_ranked.sort(key=lambda idx: dense_scores[idx], reverse=True)
                    dense_ranked = dense_ranked[: max(1, dense_candidates)]
                    dense_used = True
            except Exception:
                dense_used = False
                dense_ranked = []
                dense_query_warning = 'query embedding failed; fell back to BM25 ranking.'

        rrf_scores: Dict[int, float] = {}
        bm25_rank_map: Dict[int, int] = {}
        dense_rank_map: Dict[int, int] = {}
        bm25_k = max(1, int(rrf_k))

        for rank, idx in enumerate(bm25_ranked, start=1):
            bm25_rank_map[idx] = rank
            rrf_scores[idx] = rrf_scores.get(idx, 0.0) + (1.0 / (bm25_k + rank))

        for rank, idx in enumerate(dense_ranked, start=1):
            dense_rank_map[idx] = rank
            rrf_scores[idx] = rrf_scores.get(idx, 0.0) + (1.0 / (bm25_k + rank))

        if not rrf_scores:
            fallback_limit = min(max(1, top_k), len(chunks))
            rrf_scores = {idx: 1.0 / (bm25_k + idx + 1) for idx in range(fallback_limit)}

        ranked = list(rrf_scores.keys())
        ranked.sort(
            key=lambda idx: (
                rrf_scores.get(idx, 0.0),
                dense_scores[idx] if idx < len(dense_scores) else 0.0,
                bm25_scores[idx] if idx < len(bm25_scores) else 0.0,
            ),
            reverse=True,
        )

        rerank_score_map: Dict[int, float] = {}
        rerank_applied = False
        rerank_warning = ''
        actual_rerank_candidates = 0
        retrieval_mode = 'hybrid' if dense_used else 'bm25_only'

        if rerank_enabled and ranked:
            actual_rerank_candidates = min(
                len(ranked),
                max(1, int(rerank_candidates)),
            )
            rerank_pool = ranked[:actual_rerank_candidates]
            rerank_documents = []
            for idx in rerank_pool:
                chunk = chunks[idx]
                section_title = self._normalize_text(str(chunk.get('section_title', '') or ''))
                chunk_text = str(chunk.get('text', '') or '')
                rerank_documents.append(f'{section_title}\n{chunk_text}' if section_title else chunk_text)
            try:
                rerank_results = self._rerank_documents(clean_query, rerank_documents)
                reranked_pool: List[int] = []
                seen_indices: set = set()
                for item in rerank_results:
                    local_index = int(item['index'])
                    global_index = rerank_pool[local_index]
                    rerank_score_map[global_index] = round(float(item['relevance_score']), 8)
                    if global_index in seen_indices:
                        continue
                    reranked_pool.append(global_index)
                    seen_indices.add(global_index)

                for global_index in rerank_pool:
                    if global_index in seen_indices:
                        continue
                    reranked_pool.append(global_index)
                    seen_indices.add(global_index)

                ranked = reranked_pool + [idx for idx in ranked if idx not in seen_indices]
                rerank_applied = True
                retrieval_mode = 'hybrid_rerank' if dense_used else 'bm25_rerank'
            except Exception as exc:
                rerank_warning = str(exc)

        selected = ranked[: min(max(1, top_k), len(ranked))]
        evidence_chunks: List[Dict[str, Any]] = []
        context_parts: List[str] = []

        for rank, idx in enumerate(selected, start=1):
            chunk = chunks[idx]
            evidence = {
                'rank': rank,
                'chunk_id': chunk.get('chunk_id'),
                'section_title': chunk.get('section_title', 'Untitled Section'),
                'section_type': chunk.get('section_type', ''),
                'part_index': chunk.get('part_index', 0),
                'char_start': chunk.get('char_start', 0),
                'char_end': chunk.get('char_end', 0),
                'text': chunk.get('text', ''),
                'rrf_score': round(float(rrf_scores.get(idx, 0.0)), 8),
                'bm25_score': round(float(bm25_scores[idx] if idx < len(bm25_scores) else 0.0), 8),
                'dense_score': round(float(dense_scores[idx] if idx < len(dense_scores) else 0.0), 8),
                'rerank_score': rerank_score_map.get(idx),
                'bm25_rank': bm25_rank_map.get(idx),
                'dense_rank': dense_rank_map.get(idx),
            }
            evidence_chunks.append(evidence)
            section_title = evidence['section_title'] or 'Untitled Section'
            context_parts.append(f'[{rank}] {section_title}\n{evidence["text"]}')

        return {
            'query': clean_query,
            'top_k': len(evidence_chunks),
            'evidence_chunks': evidence_chunks,
            'answer_context': '\n\n'.join(context_parts),
            'retrieval_mode': retrieval_mode,
            'rerank_applied': rerank_applied,
            'rerank_candidates': actual_rerank_candidates,
            'embedding_warning': rag_index.get('embedding_warning', ''),
            'dense_query_warning': dense_query_warning,
            'rerank_warning': rerank_warning,
            'index_stats': {
                'chunk_count': len(chunks),
                'bm25_candidates': max(1, bm25_candidates),
                'dense_candidates': max(1, dense_candidates),
                'dense_available': bool(dense_vectors),
            },
        }

    def _parse_fulltext_xml(self, xml_text: str, *, collapse_methods: bool = True) -> Dict[str, Any]:
        root = ET.fromstring(xml_text)

        title = ''
        doi = ''
        abstract = ''
        sections: List[Dict[str, str]] = []
        seen_sections: set = set()

        # --- Pass 1: Extract title from coredata (most reliable) ---
        for elem in root.iter():
            local = self._local_name(elem.tag)
            if local == 'title' and not title:
                parent_tag = ''
                # dc:title in coredata is the most reliable
                candidate = self._normalize_text(' '.join(elem.itertext()))
                lowered = candidate.lower()
                if (
                    candidate
                    and lowered
                    not in {
                        'abstract',
                        'highlights',
                        'graphical abstract',
                        'keywords',
                        'error',
                    }
                    and len(candidate) > 5
                    and len(candidate) <= 500
                ):
                    title = candidate

        # --- Pass 2: Extract DOI from coredata ---
        for elem in root.iter():
            local = self._local_name(elem.tag)
            if local == 'doi' and not doi:
                candidate = self._normalize_text(' '.join(elem.itertext()))
                if candidate and '/' in candidate:
                    doi = candidate
                    break

        # --- Pass 3: Extract abstract (prefer class='author') ---
        author_abstract = ''
        any_abstract = ''
        for elem in root.iter():
            local = self._local_name(elem.tag)
            if local == 'abstract':
                cls = (elem.get('class', '') or '').lower()
                if cls in ('graphical', 'teaser', 'author-highlights', 'highlights'):
                    continue
                if cls == 'author':
                    # Check for structured abstract (has abstract-sec children)
                    has_sections = any(self._local_name(c.tag) == 'abstract-sec' for c in elem)
                    if has_sections:
                        author_abstract = self._parse_structured_abstract(elem)
                    else:
                        author_abstract = self._normalize_block_text(' '.join(elem.itertext()))
                    author_abstract = re.sub(
                        r'^abstract\s*[:\-]?\s*',
                        '',
                        author_abstract,
                        flags=re.IGNORECASE,
                    ).strip()
                elif not any_abstract:
                    any_abstract = self._normalize_block_text(' '.join(elem.itertext()))
                    any_abstract = re.sub(r'^abstract\s*[:\-]?\s*', '', any_abstract, flags=re.IGNORECASE).strip()

        abstract = author_abstract or any_abstract
        if abstract and abstract.lower() == 'abstract':
            abstract = ''

        # Fallback to dc:description in coredata
        if not abstract:
            for elem in root.iter():
                if self._local_name(elem.tag) == 'description':
                    candidate = self._normalize_block_text(' '.join(elem.itertext()))
                    if candidate and len(candidate) > 50:
                        abstract = candidate
                        break

        # --- Pass 4: Extract sections ---
        # Track elements whose descendants should be skipped (e.g. inside a
        # collapsed STAR Methods section).
        skip_descendants_of: set = set()

        for elem in root.iter():
            local = self._local_name(elem.tag)

            if local not in {'section', 'sec'}:
                continue

            # Skip if this element is a descendant of a collapsed parent
            if id(elem) in skip_descendants_of:
                continue

            if self._is_bibliography_like(elem):
                continue

            heading = self._find_section_heading(elem) or 'Untitled Section'

            # Skip non-content sections and all their descendants
            if self._is_skip_section(heading):
                for descendant in elem.iter():
                    skip_descendants_of.add(id(descendant))
                continue

            # Collapse methods sections: emit summary, skip all descendants
            if collapse_methods and self._is_methods_parent(heading):
                summary = self._collect_methods_summary(elem)
                if summary:
                    sections.append({'title': heading, 'text': summary})
                for descendant in elem.iter():
                    skip_descendants_of.add(id(descendant))
                continue

            body_text = self._collect_direct_text_from_section(elem)
            if not body_text:
                continue
            key = (heading, body_text[:200])
            if key in seen_sections:
                continue
            seen_sections.add(key)
            sections.append({'title': heading, 'text': body_text})

        body_blocks: List[str] = []
        for section in sections:
            heading = self._normalize_text(section.get('title', ''))
            section_text = self._normalize_block_text(section.get('text', ''))
            if not section_text:
                continue
            if heading and heading != 'Untitled Section':
                body_blocks.append(f'## {heading}\n\n{section_text}')
            else:
                body_blocks.append(section_text)
        body_text = self._normalize_block_text('\n\n'.join(body_blocks))

        full_text_parts: List[str] = []
        if abstract:
            full_text_parts.append(f'## Abstract\n\n{abstract}')
        if body_text:
            full_text_parts.append(body_text)
        full_text = self._normalize_block_text('\n\n'.join(full_text_parts))

        return {
            'title': title,
            'doi': doi,
            'abstract': abstract,
            'sections': sections,
            'body_text': body_text,
            'full_text': full_text,
            'char_count': len(full_text),
        }

    async def _build_fulltext_result(
        self,
        *,
        parsed: Dict[str, Any],
        resolved_mode: str,
        identifier: str,
        query: Optional[str],
        top_k: Optional[int],
        bm25_candidates: Optional[int],
        dense_candidates: Optional[int],
        return_full_text: bool,
        source: str,
        content_prefix: str,
        article_url: str,
        __event_emitter__,
    ) -> Dict[str, Any]:
        doi_val = self._normalize_doi(parsed.get('doi', ''))
        resolved_url = self._normalize_text(article_url or (f'https://doi.org/{doi_val}' if doi_val else ''))
        full_text = self._normalize_block_text(str(parsed.get('full_text', '') or ''))
        # An Article API HTTP 200 may contain metadata/abstract only. Never
        # index that as full-text evidence or emit a full-text success event.
        has_body = bool(self._normalize_block_text(str(parsed.get('body_text', '') or '')))
        if not has_body:
            raise RuntimeError(
                'Article response contains no parsed body text (abstract/metadata only or unsupported body format); '
                'full-text access was not established. Use get_article_abstract for the summary.'
            )
        base_result = {
            'title': parsed.get('title', ''),
            'doi': doi_val,
            'url': resolved_url,
            'abstract': parsed.get('abstract', ''),
            'char_count': len(full_text),
            'content_format': content_prefix,
            'source': source,
            'content_level': 'fulltext',
            'experimental': True,
            'mode': resolved_mode,
        }
        if parsed.get('pii'):
            base_result['pii'] = self._normalize_pii(parsed['pii'])

        if not base_result['abstract'] and not parsed.get('sections') and not full_text:
            raise RuntimeError('Full text was retrieved, but no abstract or sections were parsed.')

        if resolved_mode == 'preview':
            preview_limit = max(1000, int(self.valves.FULLTEXT_PREVIEW_CHAR_LIMIT))
            result = {
                **base_result,
                'sections': parsed.get('sections', []),
                'body_text': parsed.get('body_text', ''),
                'full_text': full_text,
                'content_format': f'{content_prefix}_preview',
            }
            if result['char_count'] > preview_limit:
                result['full_text'] = (
                    result['full_text'][:preview_limit]
                    + f'\n\n[... truncated, cleaned full text exceeds {preview_limit} characters ...]'
                )
                result['char_count'] = len(result['full_text'])
            return result

        clean_query = self._normalize_block_text(str(query or ''))
        resolved_top_k = min(max(1, int(top_k or self.valves.RAG_TOP_K)), 30)
        resolved_bm25_candidates = min(
            max(resolved_top_k, int(bm25_candidates or self.valves.RAG_BM25_CANDIDATES)),
            200,
        )
        resolved_dense_candidates = min(
            max(
                resolved_top_k,
                int(dense_candidates or self.valves.RAG_DENSE_CANDIDATES),
            ),
            200,
        )
        resolved_rrf_k = max(1, int(self.valves.RAG_RRF_K))
        resolved_rerank_candidates = min(
            max(resolved_top_k, int(self.valves.RERANK_CANDIDATES)),
            200,
        )

        cache_key = self._normalize_text(f'{source}:{doi_val or identifier}').lower()
        content_hash = hashlib.sha256(full_text.encode('utf-8', errors='ignore')).hexdigest()
        signature = self._fulltext_rag_signature()

        await self._emit_status(__event_emitter__, 'Building or loading full-text RAG index...', False)
        rag_index = self._fulltext_rag_cache_get(
            cache_key,
            signature=signature,
            content_hash=content_hash,
        )
        cache_hit = rag_index is not None
        if rag_index is None:
            rag_index = await asyncio.to_thread(
                self._build_fulltext_rag_index,
                cache_key=cache_key,
                parsed=parsed,
                signature=signature,
                content_hash=content_hash,
            )

        await self._emit_status(
            __event_emitter__,
            'Retrieving evidence chunks via BM25 + dense fusion...',
            False,
        )
        rag_result = await asyncio.to_thread(
            self._query_fulltext_rag,
            rag_index=rag_index,
            query=clean_query,
            top_k=resolved_top_k,
            bm25_candidates=resolved_bm25_candidates,
            dense_candidates=resolved_dense_candidates,
            rrf_k=resolved_rrf_k,
            rerank_enabled=bool(self.valves.RERANK_ENABLED),
            rerank_candidates=resolved_rerank_candidates,
        )

        result = {
            **base_result,
            'content_format': f'{content_prefix}_rag',
            'query': rag_result.get('query', clean_query),
            'top_k': rag_result.get('top_k', 0),
            'evidence_chunks': rag_result.get('evidence_chunks', []),
            'answer_context': rag_result.get('answer_context', ''),
            'retrieval_mode': rag_result.get('retrieval_mode', 'bm25_only'),
            'rerank_applied': bool(rag_result.get('rerank_applied', False)),
            'rerank_candidates': int(rag_result.get('rerank_candidates', 0) or 0),
            'index_stats': {
                **rag_result.get('index_stats', {}),
                'cache_hit': cache_hit,
            },
        }
        embedding_warning = rag_result.get('embedding_warning', '')
        if embedding_warning:
            result.setdefault('warnings', []).append(f'dense retrieval unavailable: {embedding_warning}')
        dense_query_warning = rag_result.get('dense_query_warning', '')
        if dense_query_warning:
            result.setdefault('warnings', []).append(dense_query_warning)
        rerank_warning = rag_result.get('rerank_warning', '')
        if rerank_warning:
            result.setdefault('warnings', []).append(f'rerank unavailable: {rerank_warning}')
        if result.get('evidence_chunks'):
            result['snippet'] = self._truncate_text(str(result['evidence_chunks'][0].get('text', '')), 320)
        if return_full_text:
            result['full_text'] = full_text
        return result

    # ------------------------------------------------------------------
    # Public tool methods
    # ------------------------------------------------------------------

    async def search_articles(
        self,
        query: Optional[str] = None,
        queries: Optional[List[str]] = None,
        mode: Optional[str] = None,
        database: Optional[str] = None,
        max_results: Optional[int] = None,
        sort: Optional[str] = None,
        date_range: Optional[str] = None,
        offset: int = 0,
        open_access_only: bool = False,
        article_types: Optional[str] = None,
        field: Optional[str] = None,
        enrich_top_n: int = 0,
        include_access: bool = False,
        abstract_view: Optional[str] = None,
        abstract_field: Optional[str] = None,
        title: Optional[str] = None,
        author: Optional[str] = None,
        author_id: Optional[str] = None,
        journal: Optional[str] = None,
        doi: Optional[str] = None,
        year: Any = None,
        affiliation: Optional[str] = None,
        keywords: Optional[str] = None,
        issn: Optional[str] = None,
        pii: Optional[str] = None,
        pubmed_id: Optional[str] = None,
        scopus_id: Optional[str] = None,
        top_k: Optional[int] = None,
        bm25_candidates: Optional[int] = None,
        dense_candidates: Optional[int] = None,
        include_results: bool = False,
        __event_emitter__=None,
    ) -> str:
        """
        Search for academic papers across Elsevier (Scopus & ScienceDirect) online databases.

        GUIDANCE:
        - Use this tool as the FIRST step to find literature, discover papers by a specific author, or search by title.
        - If the user asks for abstracts/summaries of the search results, set `enrich_top_n` to 3 or 5 to fetch them in one go.
        - This tool returns article basics such as title, authors, journal, date, URL, and a canonical DOI when available.
        - Recommended workflow: use `search_articles` first, read the returned DOI, then pass that DOI into `get_article_abstract` or `get_article_fulltext` if necessary.
        - Default behavior is retrieval orchestration: accept 2-3 intent-level queries, merge hits, dedupe by DOI/PII/title, then run article-level RAG (one article = one chunk) and rerank.
        - Prefer explicit `queries` input from the calling model over hidden query expansion.
        - `mode="list"` is a compatibility/debug path.
        - Do NOT use this tool if the user already provides a specific DOI; use `get_article_abstract` or `get_article_fulltext` instead.

        :param query: Single general search query (fallback input when `queries` is not provided).
        :param queries: Preferred input. Provide 2-3 complementary query strings inferred from user intent.
        :param mode: 'rag' (default) for article-level RAG over merged search results, or 'list' for compatibility/debug result lists.
        :param database: 'scopus', 'sciencedirect', or 'both'. Let system default unless specified.
        :param max_results: Number of results to return (1-25).
        :param sort: Sorting order (e.g., '-citedby' for most cited, '-date' for newest).
        :param date_range: Year range for ScienceDirect (e.g., '2020-2023').
        :param open_access_only: Set True if the user specifically requests free/open-access papers.
        :param enrich_top_n: SUPER HELPFUL. Set to 3 to automatically fetch the abstracts for the top 3 results.
        :param title: Exact title of the paper if known.
        :param author: Name of the author (e.g., "Smith, J" or "John Smith").
        :param journal: Specific journal name to search within.
        :param year: Specific publication year (e.g., 2023).
        :param keywords: Specific keywords that must be present.
        :param doi: Specific DOI. (Note: if you have a DOI, calling `get_article_abstract` is usually better).
        :param top_k: In `mode="rag"`, number of article chunks to return.
        :param bm25_candidates: In `mode="rag"`, BM25 candidate pool size before fusion.
        :param dense_candidates: In `mode="rag"`, dense candidate pool size before fusion.
        :param include_results: In `mode="rag"`, include the full merged search result list. Default False keeps output concise.
        :param offset: Pagination offset. Leave at 0 unless paginating.
        :param article_types, field, include_access, abstract_view, abstract_field, author_id, affiliation, issn, pii, pubmed_id, scopus_id: Advanced fields. Leave as None unless highly specific.
        """
        api_key = self.valves.ELSEVIER_API_KEY.strip()
        if not api_key:
            return json.dumps({'error': "Elsevier API Key is not configured. Set it in the tool's Valves settings."})

        clean_query = self._normalize_text(str(query or ''))
        explicit_queries, explicit_truncated = self._normalize_explicit_search_queries(queries, max_queries=3)
        resolved_mode = self._normalize_text(str(mode or 'rag')).lower()
        if resolved_mode not in {'list', 'rag'}:
            return json.dumps({'error': 'mode must be one of: list, rag'})
        has_structured_search_fields = any(
            self._normalize_text(str(value or ''))
            for value in (
                title,
                author,
                author_id,
                journal,
                doi,
                affiliation,
                keywords,
                issn,
                pii,
                pubmed_id,
                scopus_id,
            )
        ) or bool(year)
        if resolved_mode == 'rag' and not (clean_query or explicit_queries or has_structured_search_fields):
            return json.dumps(
                {
                    'error': "mode='rag' requires non-empty `query`, `queries`, or structured search fields such as `title`, `author`, or `doi`.",
                    'error_code': 'MISSING_SEARCH_INPUT',
                    'next_action': 'Provide query/queries, or pass structured fields like title, author, year, DOI, PII, or Scopus ID.',
                }
            )
        source_query_variants: Dict[str, List[str]] = {}
        query_warnings: List[str] = []
        if explicit_truncated:
            query_warnings.append('Only the first 3 distinct explicit queries were used.')

        if explicit_queries:
            query_variants = explicit_queries
        else:
            expand_search_queries = self._should_expand_search_queries(
                query=clean_query,
                doi=doi,
                pii=pii,
                scopus_id=scopus_id,
                pubmed_id=pubmed_id,
                title=title,
                author_id=author_id,
                issn=issn,
            )
            query_variants = (
                self._expand_search_query_variants(clean_query)
                if expand_search_queries
                else ([clean_query] if clean_query else [''])
            )

        primary_query = ''

        for query_variant in query_variants:
            variant_primary_query, variant_source_queries, variant_warnings = self._build_search_queries(
                query=query_variant,
                database=database or self.valves.DEFAULT_DATABASE,
                title=title,
                author=author,
                author_id=author_id,
                journal=journal,
                doi=doi,
                year=year,
                affiliation=affiliation,
                keywords=keywords,
                issn=issn,
                pii=pii,
                pubmed_id=pubmed_id,
                scopus_id=scopus_id,
            )
            if not primary_query and variant_primary_query:
                primary_query = variant_primary_query
            for warning in variant_warnings:
                if warning not in query_warnings:
                    query_warnings.append(warning)
            for source_name, source_query in variant_source_queries.items():
                clean_source_query = self._normalize_text(str(source_query or ''))
                if not clean_source_query:
                    continue
                source_query_variants.setdefault(source_name, [])
                if clean_source_query not in source_query_variants[source_name]:
                    source_query_variants[source_name].append(clean_source_query)

        for source_name in list(source_query_variants.keys()):
            source_query_variants[source_name] = source_query_variants[source_name][
                : self._search_query_variant_limit()
            ]
            if not source_query_variants[source_name]:
                source_query_variants.pop(source_name)

        source_queries = {
            source_name: variants[0] for source_name, variants in source_query_variants.items() if variants
        }
        if not primary_query:
            primary_query = next(
                (variants[0] for variants in source_query_variants.values() if variants),
                '',
            )

        response = await self._search_articles_structured(
            query=primary_query,
            database=database or self.valves.DEFAULT_DATABASE,
            max_results=min(max_results or self.valves.MAX_RESULTS, 25),
            sort=sort,
            date_range=date_range,
            offset=offset,
            open_access_only=open_access_only,
            article_types=article_types,
            field=field,
            enrich_top_n=max(0, int(enrich_top_n)),
            include_access=include_access,
            abstract_view=abstract_view,
            abstract_field=abstract_field,
            emitter=__event_emitter__,
            source_queries=source_queries,
            source_query_variants=source_query_variants,
            extra_warnings=query_warnings,
        )

        if not response.get('results'):
            await self._emit_status(__event_emitter__, response.get('error', 'No results found.'), True)
            return json.dumps(response, ensure_ascii=False, indent=2)

        if resolved_mode == 'list':
            await self._emit_citations(__event_emitter__, response['results'])
            await self._emit_status(__event_emitter__, f'Found {response["result_count"]} articles.', True)
            return json.dumps(response, ensure_ascii=False, indent=2)

        rag_query = clean_query or (explicit_queries[0] if explicit_queries else primary_query)
        resolved_top_k = min(max(1, int(top_k or self.valves.RAG_TOP_K)), 30)
        resolved_bm25_candidates = min(
            max(resolved_top_k, int(bm25_candidates or self.valves.RAG_BM25_CANDIDATES)),
            200,
        )
        resolved_dense_candidates = min(
            max(
                resolved_top_k,
                int(dense_candidates or self.valves.RAG_DENSE_CANDIDATES),
            ),
            200,
        )
        resolved_rrf_k = max(1, int(self.valves.RAG_RRF_K))

        await self._emit_status(__event_emitter__, 'Building article-level search RAG index...', False)
        rag_index = await asyncio.to_thread(
            self._build_search_article_rag_index,
            results=response['results'],
        )
        await self._emit_status(__event_emitter__, 'Retrieving ranked article evidence...', False)
        rag_result = await asyncio.to_thread(
            self._query_search_article_rag,
            rag_index=rag_index,
            query=rag_query,
            top_k=resolved_top_k,
            bm25_candidates=resolved_bm25_candidates,
            dense_candidates=resolved_dense_candidates,
            rrf_k=resolved_rrf_k,
        )

        rag_payload = {
            'database': response.get('database'),
            'mode': 'rag',
            'content_format': 'search_article_rag',
            'query': rag_result.get('query', rag_query),
            'top_k': rag_result.get('top_k', 0),
            'evidence_chunks': rag_result.get('evidence_chunks', []),
            'answer_context': rag_result.get('answer_context', ''),
            'retrieval_mode': rag_result.get('retrieval_mode', 'bm25_only'),
            'rerank_applied': bool(rag_result.get('rerank_applied', False)),
            'rerank_candidates': int(rag_result.get('rerank_candidates', 0) or 0),
            'index_stats': {
                **rag_result.get('index_stats', {}),
                'search_result_count': int(response.get('result_count', 0) or 0),
            },
            'source_queries': response.get('source_queries', {}),
            'source_query_variants': response.get('source_query_variants', {}),
            'search_rerank': response.get('search_rerank', {}),
            'result_count': response.get('result_count', 0),
            'candidate_articles': [
                {
                    'title': row.get('title', ''),
                    'doi': row.get('doi', ''),
                    'url': row.get('url', ''),
                    'journal': row.get('journal', ''),
                    'year': row.get('year', ''),
                    'database': row.get('database', ''),
                }
                for row in self._as_list(response.get('results', []))
                if isinstance(row, dict)
            ],
            'totals_by_database': response.get('totals_by_database', {}),
            'used_backends': response.get('used_backends', {}),
            'warnings': list(response.get('warnings', [])),
            'partial_errors': response.get('partial_errors', []),
        }

        if include_results:
            rag_payload['results'] = response.get('results', [])

        embedding_warning = rag_result.get('embedding_warning', '')
        if embedding_warning:
            rag_payload['warnings'].append(f'dense retrieval unavailable: {embedding_warning}')
        dense_query_warning = rag_result.get('dense_query_warning', '')
        if dense_query_warning:
            rag_payload['warnings'].append(dense_query_warning)
        rerank_warning = rag_result.get('rerank_warning', '')
        if rerank_warning:
            rag_payload['warnings'].append(f'rerank unavailable: {rerank_warning}')

        citation_rows: List[Dict[str, Any]] = []
        for evidence in self._as_list(rag_payload.get('evidence_chunks', [])):
            if not isinstance(evidence, dict):
                continue
            citation_rows.append(
                {
                    'title': evidence.get('title') or evidence.get('section_title', 'Search Result'),
                    'url': evidence.get('url', ''),
                    'snippet': self._truncate_text(str(evidence.get('text', '')), 320),
                }
            )
        if citation_rows:
            await self._emit_citations(__event_emitter__, citation_rows)
        await self._emit_status(
            __event_emitter__,
            f'Article-level RAG evidence retrieved (top {rag_payload.get("top_k", 0)}).',
            True,
        )

        return json.dumps(rag_payload, ensure_ascii=False, indent=2)

    async def search_authors(
        self,
        query: Optional[str] = None,
        name: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        affiliation: Optional[str] = None,
        orcid: Optional[str] = None,
        max_results: Optional[int] = None,
        __event_emitter__=None,
    ) -> str:
        """
        Search for academic researcher profiles on Scopus.

        GUIDANCE:
        - Use this tool ONLY when the user asks specifically about a PERSON (e.g., their h-index, affiliations, ORCID, or total publication count).
        - CRITICAL: Do NOT use this tool to find papers written by an author. To find an author's papers, use the `search_articles` tool with the `author` parameter.
        - You MUST provide at least one specific identifier (`name`, `first_name`, `last_name`, `affiliation`, or `orcid`).

        :param query: Raw Scopus author search query. Usually leave empty and use specific fields below.
        :param name: Full name of the researcher (e.g., "Andrew Ng").
        :param first_name: Given name.
        :param last_name: Surname.
        :param affiliation: The university, institution, or company the author works for.
        :param orcid: The 16-digit ORCID identifier of the researcher.
        :param max_results: Number of profiles to return. Default is sufficient.
        """
        api_key = self.valves.ELSEVIER_API_KEY.strip()
        if not api_key:
            return json.dumps({'error': "Elsevier API Key is not configured. Set it in the tool's Valves settings."})

        author_query = self._build_scopus_author_query(
            query=query,
            name=name,
            first_name=first_name,
            last_name=last_name,
            affiliation=affiliation,
            orcid=orcid,
        )
        if not author_query:
            return json.dumps(
                {'error': 'Provide query text or at least one of name, first_name, last_name, affiliation, or orcid.'}
            )

        limit = min(max(1, max_results or self.valves.MAX_RESULTS), 25)
        await self._emit_status(__event_emitter__, f'Searching author profiles for: {author_query}', False)

        try:
            params = {'query': author_query, 'count': limit}
            url = self._build_url('https://api.elsevier.com/content/search/author', params)
            payload = await asyncio.to_thread(self._fetch_json, url)
            results = self._parse_scopus_author_search_results(payload)
            response = {
                'query': author_query,
                'result_count': len(results),
                'results': results,
                'total_results': self._coerce_int(payload.get('search-results', {}).get('opensearch:totalResults')),
            }
            if results:
                await self._emit_status(__event_emitter__, f'Found {len(results)} author profiles.', True)
            else:
                await self._emit_status(__event_emitter__, 'No author profiles found.', True)
            return json.dumps(response, ensure_ascii=False, indent=2)
        except Exception as exc:
            await self._emit_status(__event_emitter__, f'Author search failed: {exc}', True)
            return json.dumps({'error': str(exc), 'query': author_query})

    async def get_article_abstract(
        self,
        doi: Optional[str] = None,
        scopus_id: Optional[str] = None,
        view: Optional[str] = None,
        field: Optional[str] = None,
        include_access: bool = False,
        __event_emitter__=None,
    ) -> str:
        """
        Retrieve complete metadata, the full abstract, keywords, and citation counts for a SINGLE specific article.

        GUIDANCE:
        - Use this tool when you ALREADY KNOW the exact exact DOI or Scopus ID of the paper, and you need its detailed summary.
        - If you only have the title of the paper, DO NOT guess the DOI. Use `search_articles` with the `title` parameter first to find the correct DOI.
        - The DOI returned by `search_articles` is canonicalized for direct reuse here.

        :param doi: The explicit DOI of the article (e.g., "10.1016/j.cell.2023.01.001"). `doi:...` and `https://doi.org/...` inputs are also accepted. Provide EXACTLY ONE of `doi` or `scopus_id`.
        :param scopus_id: The Scopus ID of the article. Provide EXACTLY ONE of `doi` or `scopus_id`.
        :param view: Level of detail. Leave as None for the default 'META_ABS' (which is usually best).
        :param field: Restrict return to a specific field. Leave as None to get all data.
        :param include_access: Deprecated compatibility flag. Access probing is skipped; use fulltext retrieval directly.
        """
        api_key = self.valves.ELSEVIER_API_KEY.strip()
        if not api_key:
            return json.dumps({'error': 'Elsevier API Key is not configured.'})

        normalized_doi = self._normalize_doi(doi)

        if not normalized_doi and not scopus_id:
            return json.dumps({'error': "Provide either 'doi' or 'scopus_id'."})

        identifier = normalized_doi if normalized_doi else scopus_id.strip()
        await self._emit_status(__event_emitter__, f'Retrieving abstract metadata for {identifier}', False)

        try:
            result = await asyncio.to_thread(
                self._retrieve_abstract_structured,
                doi=normalized_doi or None,
                scopus_id=scopus_id,
                view=view,
                field=field,
            )
            await self._emit_citations(__event_emitter__, [result])
            await self._emit_status(__event_emitter__, 'Abstract metadata retrieved.', True)
            return json.dumps(result, ensure_ascii=False, indent=2)

        except Exception as exc:
            await self._emit_status(__event_emitter__, f'Abstract retrieval failed: {exc}', True)
            return json.dumps({'error': str(exc), 'identifier': identifier})

    async def get_article_fulltext(
        self,
        doi: Optional[str] = None,
        pii: Optional[str] = None,
        query: Optional[str] = None,
        mode: Optional[str] = None,
        top_k: Optional[int] = None,
        bm25_candidates: Optional[int] = None,
        dense_candidates: Optional[int] = None,
        return_full_text: bool = False,
        __event_emitter__=None,
    ) -> str:
        """
        Retrieve and analyze an Elsevier article's full text through the official Elsevier API.
        Use this tool when you need deep details (e.g., methodology, specific data, conclusions) from inside a paper, rather than just the abstract.

        GUIDANCE:
        - Recommended workflow: use `search_articles` first to get the article basics and DOI, then pass that DOI into this tool.
        - The DOI returned by `search_articles` is canonicalized for direct reuse here.
        - This method supports Elsevier DOI values (`10.1016/...`) and ScienceDirect PII values or article URLs.
        - Other publisher identifiers and article URLs are unsupported. This method does not fetch non-Elsevier web pages.

        :param doi: Elsevier DOI. `doi:...` and `https://doi.org/...` inputs are also accepted. Provide EXACTLY ONE of `doi` or `pii`.
        :param pii: Elsevier Publisher Item Identifier. A ScienceDirect article URL is also accepted. Provide EXACTLY ONE of `doi` or `pii`.
        :param query: REQUIRED if mode is 'rag'. The specific question, claim, or keywords you want to search for within the article's full text.
        :param mode: Choose 'rag' (default) to extract specific evidence answering your `query`, or 'preview' to get the beginning of the full text for a general overview.
        :param top_k: Number of evidence chunks to return in 'rag' mode. Leave as None for default.
        :param bm25_candidates: Advanced parameter. Leave as None.
        :param dense_candidates: Advanced parameter. Leave as None.
        :param return_full_text: Set to True ONLY IF you desperately need the entire parsed text. Warning: It can consume your entire context window. Default is False.
        """
        resolved_mode = (mode or self.valves.FULLTEXT_DEFAULT_MODE or 'rag').strip().lower()
        if resolved_mode not in {'rag', 'preview'}:
            return json.dumps({'error': 'mode must be one of: rag, preview'})
        if resolved_mode == 'rag' and not self._normalize_text(str(query or '')):
            return json.dumps({'error': "RAG mode requires a non-empty 'query'."})

        api_key = self.valves.ELSEVIER_API_KEY.strip()
        normalized_doi = self._normalize_doi(doi)
        normalized_pii = self._normalize_pii(pii)
        if normalized_doi and normalized_pii:
            return json.dumps({'error': "Provide exactly one of 'doi' or 'pii'."})

        if normalized_doi:
            identifier = normalized_doi
        elif normalized_pii:
            identifier = normalized_pii
        else:
            return json.dumps({'error': "Provide either 'doi' or 'pii'."})

        if normalized_doi and not normalized_doi.lower().startswith('10.1016/'):
            message = (
                'Unsupported identifier: this tool retrieves full text only through '
                'the Elsevier API for Elsevier DOI or ScienceDirect PII inputs.'
            )
            await self._emit_status(__event_emitter__, message, True)
            return json.dumps(
                {
                    'status': 'unsupported',
                    'error': message,
                    'identifier': identifier,
                }
            )

        if not api_key:
            message = 'Elsevier API Key is not configured.'
            await self._emit_status(__event_emitter__, message, True)
            return json.dumps({'error': message, 'identifier': identifier})

        xml_url = (
            f'https://api.elsevier.com/content/article/doi/{parse.quote(normalized_doi, safe="/")}'
            if normalized_doi
            else f'https://api.elsevier.com/content/article/pii/{parse.quote(normalized_pii, safe="")}'
        )
        xml_url = self._build_url(xml_url, {'view': 'FULL'})
        await self._emit_status(__event_emitter__, f'Retrieving XML full text for {identifier}', False)
        try:
            xml_text = await asyncio.to_thread(self._fetch_text, xml_url, accept='application/xml')
            collapse_methods = not bool(self.valves.RAG_KEEP_METHODS_GRANULAR) if resolved_mode == 'rag' else True
            parsed_xml = await asyncio.to_thread(
                self._parse_fulltext_xml,
                xml_text,
                collapse_methods=collapse_methods,
            )
            # XML may omit the identifier that was already supplied by the
            # caller. Reuse it; no extra lookup or retrieval route is needed.
            resolved_doi = self._normalize_doi(parsed_xml.get('doi', '')) or normalized_doi
            resolved_pii = self._normalize_pii(parsed_xml.get('pii', '')) or normalized_pii
            parsed_xml['doi'] = resolved_doi
            parsed_xml['pii'] = resolved_pii
            resolved_article_url = (
                f'https://doi.org/{resolved_doi}'
                if resolved_doi
                else f'https://www.sciencedirect.com/science/article/pii/{parse.quote(resolved_pii, safe="")}'
                if resolved_pii
                else ''
            )
            result = await self._build_fulltext_result(
                parsed=parsed_xml,
                resolved_mode=resolved_mode,
                identifier=identifier,
                query=query,
                top_k=top_k,
                bm25_candidates=bm25_candidates,
                dense_candidates=dense_candidates,
                return_full_text=return_full_text,
                source='elsevier_xml',
                content_prefix='elsevier_xml',
                article_url=resolved_article_url,
                __event_emitter__=__event_emitter__,
            )
            await self._emit_citations(__event_emitter__, [result])
            if resolved_mode == 'rag':
                await self._emit_status(
                    __event_emitter__,
                    f'RAG evidence retrieved (top {result.get("top_k", 0)}).',
                    True,
                )
            else:
                await self._emit_status(__event_emitter__, 'XML full text preview retrieved.', True)
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as exc:
            error_msg = str(exc)
            tip = ''
            if '401' in error_msg or '403' in error_msg:
                tip = 'Access denied. Full text usually requires institutional subscription and often institutional network or insttoken.'
            await self._emit_status(__event_emitter__, f'Full text retrieval failed: {exc}', True)
            return json.dumps({'error': error_msg, 'identifier': identifier, 'tip': tip})
