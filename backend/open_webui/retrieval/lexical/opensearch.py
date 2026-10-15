"""Small optional HTTP adapter. Construct only when custom hybrid retrieval is enabled."""

import json

import aiohttp


class OpenSearchLexicalAdapter:
    def __init__(
        self, *, url, index='openwebui-evidence-v1', username=None, password=None, verify_ssl=True, request_json=None
    ):
        if not url.startswith(('http://', 'https://')):
            raise ValueError('OpenSearch URL must be configured')
        if not index or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in index):
            raise ValueError('invalid OpenSearch index name')
        self.url = url.rstrip('/')
        self.index_name = index
        self.auth = aiohttp.BasicAuth(username, password or '') if username else None
        self.verify_ssl = verify_ssl
        self.request_json = request_json or self._request

    async def _request(self, method, path, *, payload=None, ndjson=None):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30), auth=self.auth) as session:
            async with session.request(
                method,
                self.url + path,
                json=payload,
                data=ndjson,
                headers={'Content-Type': 'application/x-ndjson'} if ndjson is not None else None,
                ssl=self.verify_ssl,
            ) as response:
                response.raise_for_status()
                return await response.json()

    async def index(self, documents):
        if not documents:
            return
        # No hidden analyzer/plugin requirement. Operators can precreate the index with their analyzer.
        body = []
        for document in documents:
            body.extend(
                (
                    json.dumps({'index': {'_index': self.index_name, '_id': document['evidence_ref']}}),
                    json.dumps(document, ensure_ascii=False),
                )
            )
        response = await self.request_json('POST', '/_bulk?refresh=wait_for', ndjson='\n'.join(body) + '\n')
        if response.get('errors'):
            raise RuntimeError('OpenSearch bulk indexing contains failed items')

    async def search(self, text, *, evidence_refs, count=20):
        if not evidence_refs:
            return []
        # Explicit IDs protect shared indices and avoid keyword-field mapping assumptions for references.
        response = await self.request_json(
            'POST',
            '/' + self.index_name + '/_search',
            payload={
                'size': min(count, 200),
                '_source': False,
                'query': {
                    'bool': {'must': [{'match': {'text': text}}], 'filter': [{'ids': {'values': evidence_refs}}]}
                },
            },
        )
        return [hit['_id'] for hit in response.get('hits', {}).get('hits', [])]

    async def delete(self, evidence_refs):
        if evidence_refs:
            body = (
                '\n'.join(json.dumps({'delete': {'_index': self.index_name, '_id': ref}}) for ref in evidence_refs)
                + '\n'
            )
            response = await self.request_json('POST', '/_bulk?refresh=wait_for', ndjson=body)
            if response.get('errors'):
                # Treat missing (404) deletions as already complete.
                failures = [
                    item['delete']
                    for item in response.get('items', [])
                    if item.get('delete', {}).get('status', 500) not in (200, 202, 404)
                ]
                if failures:
                    raise RuntimeError('OpenSearch cleanup contains failed items')
