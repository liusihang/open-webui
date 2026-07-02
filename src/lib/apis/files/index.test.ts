import { beforeEach, describe, expect, it, vi } from 'vitest';

import { getFileContentById } from './index';

describe('files api bindings', () => {
	beforeEach(() => {
		vi.restoreAllMocks();
	});

	it('sends bearer authorization when fetching file content with a token', async () => {
		const bytes = new Uint8Array([1, 2, 3]).buffer;
		const fetchMock = vi.fn().mockResolvedValue({
			ok: true,
			arrayBuffer: async () => bytes
		});
		vi.stubGlobal('fetch', fetchMock);

		const result = await getFileContentById('file-1', 'token-1');

		expect(result).toBe(bytes);
		expect(fetchMock).toHaveBeenCalledTimes(1);
		expect(fetchMock.mock.calls[0]?.[0]).toContain('/api/v1/files/file-1/content');
		expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
			method: 'GET',
			credentials: 'include',
			headers: {
				Accept: 'application/json',
				authorization: 'Bearer token-1'
			}
		});
	});

	it('keeps cookie auth available when fetching file content without a token', async () => {
		const bytes = new Uint8Array([4, 5, 6]).buffer;
		const fetchMock = vi.fn().mockResolvedValue({
			ok: true,
			arrayBuffer: async () => bytes
		});
		vi.stubGlobal('fetch', fetchMock);

		await getFileContentById('file-1');

		expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
			credentials: 'include',
			headers: {
				Accept: 'application/json'
			}
		});
		expect(fetchMock.mock.calls[0]?.[1]?.headers).not.toHaveProperty('authorization');
	});
});
