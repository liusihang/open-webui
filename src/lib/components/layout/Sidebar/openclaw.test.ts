import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('$lib/apis/channels', () => ({
	getOpenClawMe: vi.fn()
}));

import { getOpenClawMe } from '$lib/apis/channels';
import {
	resolveOpenClawChannelId,
	resetOpenClawChannelResolver
} from './openclaw';

describe('openclaw sidebar helper', () => {
	beforeEach(() => {
		resetOpenClawChannelResolver();
		vi.mocked(getOpenClawMe).mockReset();
	});

	it('resolves the current user openclaw channel once and reuses it', async () => {
		vi.mocked(getOpenClawMe).mockResolvedValue({
			id: 'openclaw-123',
			type: 'openclaw',
			user_id: 'user-1',
			name: 'openclaw'
		});

		const first = await resolveOpenClawChannelId('token-1');
		const second = await resolveOpenClawChannelId('token-1');

		expect(first).toBe('openclaw-123');
		expect(second).toBe('openclaw-123');
		expect(getOpenClawMe).toHaveBeenCalledTimes(1);
		expect(getOpenClawMe).toHaveBeenCalledWith('token-1');
	});
});
