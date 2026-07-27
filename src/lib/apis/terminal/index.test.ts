import { afterEach, describe, expect, it, vi } from 'vitest';

import { getTerminalServers } from './index';

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('getTerminalServers', () => {
	it('keeps the legacy empty-list fallback for ordinary callers', async () => {
		vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));

		await expect(getTerminalServers('token')).resolves.toEqual([]);
	});

	it('rejects discovery failures when the caller must preserve last-known catalog truth', async () => {
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503 }));

		await expect(getTerminalServers('token', { throwOnError: true })).rejects.toThrow(
			'Terminal discovery failed'
		);
	});
});
