import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
	backfillKnowledgeLayers,
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

	it('sends selected layer types for file-level regenerate', async () => {
		const fetchMock = vi.fn().mockResolvedValue({
			ok: true,
			json: async () => ({ status: 'queued' })
		});
		vi.stubGlobal('fetch', fetchMock);

		await regenerateKnowledgeFileLayers('token-1', 'kb-1', 'file-1', {
			layerTypes: ['abstract', 'key_data'],
			force: false
		});

		expect(fetchMock).toHaveBeenCalledTimes(1);
		expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
			method: 'POST'
		});
		expect(fetchMock.mock.calls[0]?.[1]?.body).toBe(
			JSON.stringify({
				layer_types: ['abstract', 'key_data'],
				force: false
			})
		);
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

	it('triggers knowledge-level backfill with selected layers', async () => {
		const fetchMock = vi.fn().mockResolvedValue({
			ok: true,
			json: async () => ({ total_files: 3, scheduled_files: 2, skipped_files: 1 })
		});
		vi.stubGlobal('fetch', fetchMock);

		const result = await backfillKnowledgeLayers('token-1', 'kb-1', {
			layerTypes: ['abstract', 'key_findings'],
			force: false
		});

		expect(fetchMock).toHaveBeenCalledTimes(1);
		expect(fetchMock.mock.calls[0]?.[0]).toContain('/api/v1/knowledge/kb-1/layers/backfill');
		expect(fetchMock.mock.calls[0]?.[1]?.body).toBe(
			JSON.stringify({
				layer_types: ['abstract', 'key_findings'],
				force: false
			})
		);
		expect(result).toEqual({ total_files: 3, scheduled_files: 2, skipped_files: 1 });
	});
});
