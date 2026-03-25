import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
	getKnowledgeFileLayers,
	regenerateKnowledgeFileLayerByType,
	regenerateKnowledgeFileLayers
} from './index';

describe('knowledge layered api bindings', () => {
	beforeEach(() => {
		vi.restoreAllMocks();
	});

	it('fetches file layers for a knowledge file', async () => {
		const fetchMock = vi.fn().mockResolvedValue({
			ok: true,
			json: async () => ({ items: [{ layer_type: 'abstract' }] })
		});
		vi.stubGlobal('fetch', fetchMock);

		const result = await getKnowledgeFileLayers('token-1', 'kb-1', 'file-1');

		expect(fetchMock).toHaveBeenCalledTimes(1);
		expect(fetchMock.mock.calls[0]?.[0]).toContain('/api/v1/knowledge/kb-1/file/file-1/layers');
		expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
			method: 'GET',
			headers: {
				authorization: 'Bearer token-1'
			}
		});
		expect(result).toEqual({ items: [{ layer_type: 'abstract' }] });
	});

	it('triggers regenerate all layers for one file', async () => {
		const fetchMock = vi.fn().mockResolvedValue({
			ok: true,
			json: async () => ({ status: 'queued' })
		});
		vi.stubGlobal('fetch', fetchMock);

		const result = await regenerateKnowledgeFileLayers('token-1', 'kb-1', 'file-1');

		expect(fetchMock).toHaveBeenCalledTimes(1);
		expect(fetchMock.mock.calls[0]?.[0]).toContain(
			'/api/v1/knowledge/kb-1/file/file-1/layers/regenerate'
		);
		expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
			method: 'POST'
		});
		expect(result).toEqual({ status: 'queued' });
	});

	it('triggers regenerate for a single layer type', async () => {
		const fetchMock = vi.fn().mockResolvedValue({
			ok: true,
			json: async () => ({ status: 'queued', layer_type: 'key_data' })
		});
		vi.stubGlobal('fetch', fetchMock);

		const result = await regenerateKnowledgeFileLayerByType(
			'token-1',
			'kb-1',
			'file-1',
			'key_data'
		);

		expect(fetchMock).toHaveBeenCalledTimes(1);
		expect(fetchMock.mock.calls[0]?.[0]).toContain(
			'/api/v1/knowledge/kb-1/file/file-1/layers/regenerate/key_data'
		);
		expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
			method: 'POST'
		});
		expect(result).toEqual({ status: 'queued', layer_type: 'key_data' });
	});

	it('falls back to readable error text when backend omits detail', async () => {
		const fetchMock = vi.fn().mockResolvedValue({
			ok: false,
			json: async () => ({})
		});
		vi.stubGlobal('fetch', fetchMock);

		await expect(getKnowledgeFileLayers('token-1', 'kb-1', 'file-1')).rejects.toBe(
			'Failed to fetch file layers'
		);
	});

	it('uses status text when error response is not json', async () => {
		const fetchMock = vi.fn().mockResolvedValue({
			ok: false,
			status: 503,
			statusText: 'Service Unavailable',
			json: async () => {
				throw new SyntaxError('Unexpected token < in JSON');
			}
		});
		vi.stubGlobal('fetch', fetchMock);

		await expect(getKnowledgeFileLayers('token-1', 'kb-1', 'file-1')).rejects.toBe(
			'503 Service Unavailable'
		);
	});
});
